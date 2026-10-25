"""A gate for hypotheses about what predicts returns: powered, time-split, and honest about how many were tried.

The model-zoo comparison found that no model, of any size, predicted the next hour. Before asking
whether the *system* was at fault, the question was whether anything could: 17 intraday, 18 daily
and 12 cross-sectional hypotheses on 2.75 to 10 years of real bars were put through the protocol
below, and none of them carried a direction. The two that looked like survivors were a volatility
that persists (real, and a statement about risk rather than direction) and high-volatility stocks
beating low-volatility ones, which was beta in disguise: it flipped sign when the market fell and
vanished once the market's share was removed.

So the protocol is the product, not any one signal. A hypothesis has to:

1. be stated before it is looked at, and **recorded whether or not it works**;
2. be selected on an earlier period and **read once** on a later one (`time_split`);
3. show the same sign in both;
4. clear a significance bar that counts **the hypotheses in its own family**, not the whole ledger
   (`judge(family=...)`), and is reported beside the flat whole-ledger Bonferroni bar so neither is
   hidden. A larger search still raises its own bar and cannot buy a cheaper verdict by trying
   harder;
5. be labelled **confirmatory** or **exploratory**; only a confirmatory hypothesis can SURVIVE, and
   an exploratory one is reported with its uncorrected p so a lead can be chased without being
   smuggled in as a finding;
6. have an interval from resampling **whole blocks** (trading days, months) rather than single
   bars, because neighbouring observations share their session and a bar-level interval is far
   too narrow;
7. where the hypothesis shares a common driver with the thing it claims to predict, **still hold
   once that driver is removed** (`control`). Significance cannot tell a signal from a confound:
   "high-volatility stocks earn more" cleared the bar above (p = 0.0003, the same sign in both
   periods) and was beta in disguise - it earned +0.16 in months the market rose, -0.09 in months
   it fell, and +0.02 once beta times the market was taken out;
8. say what it could not have seen. A null with no power is not evidence of absence, so every row
   carries the smallest effect its block count could have detected.

Pure standard library, like the rest of this package, and it writes only its own ledger.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from autopoiesis.research.trials import record_trial

DEFAULT_SIGNAL_LEDGER = Path("runtime") / "autopoiesis" / "signal_trials.jsonl"
#: The family-wise error rate each family is held to.
FAMILY_ALPHA = 0.05
#: The searches the ledger has actually seen. A family is one way of asking a question, and its
#: multiplicity is counted within it: a hypothesis about a 5-minute lead-lag has nothing to do with
#: a cross-sectional fundamentals hypothesis, and charging one for the other is what made the flat
#: whole-ledger Bonferroni bar unable to detect anything real.
KNOWN_FAMILIES = frozenset(
    {"intraday", "daily", "xs", "long", "spy-only", "news", "news-intraday", "options", "fundamentals", "1min"}
)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def _corr(n: float, sx: float, sy: float, sxx: float, syy: float, sxy: float) -> float:
    vx = sxx / n - (sx / n) ** 2
    vy = syy / n - (sy / n) ** 2
    if vx <= 0 or vy <= 0:
        return float("nan")
    return (sxy / n - (sx / n) * (sy / n)) / math.sqrt(vx * vy)


@dataclass(frozen=True)
class CorrelationResult:
    n: int
    blocks: int
    rho: float
    low: float
    high: float
    p: float


def block_rank_correlation(
    blocks: Sequence[object], x: Sequence[float], y: Sequence[float], *, reps: int = 2000, seed: int = 11
) -> CorrelationResult:
    """Spearman correlation of `x` with `y`, with a 95% interval and a two-sided p-value from
    resampling whole `blocks` (the day or month an observation belongs to) with replacement.

    Pairs where either value is missing are dropped. With fewer than 30 pairs or 5 blocks there is
    nothing honest to say, so the result is NaN rather than a number that looks like evidence.
    """
    keep = [i for i in range(len(x)) if x[i] == x[i] and y[i] == y[i]]
    nan = float("nan")
    if len(keep) < 30 or len({blocks[i] for i in keep}) < 5:
        return CorrelationResult(len(keep), 0, nan, nan, nan, nan)
    xs, ys = _ranks([x[i] for i in keep]), _ranks([y[i] for i in keep])
    index: dict[object, int] = {}
    sums: list[list[float]] = []
    for pos, i in enumerate(keep):
        b = index.setdefault(blocks[i], len(index))
        if b == len(sums):
            sums.append([0.0] * 6)
        s = sums[b]
        s[0] += 1.0
        s[1] += xs[pos]
        s[2] += ys[pos]
        s[3] += xs[pos] * xs[pos]
        s[4] += ys[pos] * ys[pos]
        s[5] += xs[pos] * ys[pos]
    total = [sum(col) for col in zip(*sums, strict=True)]
    rho = _corr(*total)
    rng = random.Random(seed)
    draws: list[float] = []
    count = len(sums)
    for _ in range(reps):
        acc = [0.0] * 6
        for _ in range(count):
            for k, v in enumerate(sums[rng.randrange(count)]):
                acc[k] += v
        r = _corr(*acc)
        if r == r:
            draws.append(r)
    draws.sort()
    if not draws:
        return CorrelationResult(len(keep), count, rho, nan, nan, nan)
    below = sum(1 for d in draws if d <= 0) / len(draws)
    above = sum(1 for d in draws if d >= 0) / len(draws)
    p = max(2 * min(below, above), 1.0 / len(draws))
    return CorrelationResult(
        len(keep), count, rho, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1], p
    )


def time_split(days: Sequence[object], selection_fraction: float = 0.6) -> object:
    """The first day of the test period: the selection period is the earliest `fraction` of days."""
    ordered = sorted(set(days))  # type: ignore[type-var]
    return ordered[int(len(ordered) * selection_fraction)]


def threshold(total_trials: int) -> float:
    """Bonferroni: the per-hypothesis bar when `total_trials` hypotheses are held to one family rate."""
    return FAMILY_ALPHA / max(1, total_trials)


def count_trials(path: Path | None = None) -> int:
    target = Path(path or DEFAULT_SIGNAL_LEDGER)
    try:
        with open(target, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _ledger_rows(path: Path | None) -> list[dict]:
    try:
        return [json.loads(line) for line in Path(path or DEFAULT_SIGNAL_LEDGER).open(encoding="utf-8") if line.strip()]
    except OSError:
        return []


def family_bar(family: str, ledger: Path | None = None) -> int:
    """How many distinct hypotheses this family already holds, replicas excluded.

    A hypothesis re-run with the same `name` is one hypothesis, not a new one. The flat
    whole-ledger Bonferroni counted re-runs as new trials, so repeating a measurement raised the
    bar for everything: a session that re-ran the 13-ETF long-history search to reproduce it
    appended 18 rows whose numbers were identical to four decimal places, and the bar moved from
    0.05/107 to 0.05/125 in the process.
    """
    names = {str(r.get("name")) for r in _ledger_rows(ledger) if not r.get("replication_of")}
    if not names:
        return 0
    return sum(1 for name in names if not family or str(name).split(":")[0] == family)


def min_detectable_effect(blocks: int, alpha: float = 0.05) -> float | None:
    """The smallest correlation this test could have called significant at this many blocks.

    A null with no power is not evidence of absence. Reported on every row so a reader can tell
    "we looked and saw nothing" from "we could not have seen anything".
    """
    if blocks < 5:
        return None
    return 1.645 / (blocks**0.5)


def judge(
    name: str,
    selection: CorrelationResult,
    test: CorrelationResult,
    *,
    family: str = "unfiled",
    kind: str = "confirmatory",
    control: CorrelationResult | None = None,
    ledger: Path | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """Record the hypothesis, then say whether it survives.

    Recording comes first and cannot be skipped: a hypothesis that failed leaves the same trace as
    one that passed.

    **`family`** is the search this hypothesis belongs to, and the bar is Bonferroni within it. The
    whole-ledger flat bar is still computed and written to the row as `ledger_bar`, so nothing is
    hidden by the change - and neither is the reason for it. Held to one flat family, 119
    hypotheses put the bar at 3.1e-4, and two effects that are real and behave as the literature
    predicts could not clear it: post-earnings drift at +0.072 (p = 0.004) and trailing volatility
    against the next 21 days' return at +0.168 on SPY alone (p = 0.005, same sign in 2000, 2007,
    2020 and 2022). A bar that rejects known effects is not measuring them.

    **`kind`** separates confirmatory from exploratory. `confirmatory` is held to the family bar and
    may return SURVIVES. `exploratory` is *reported* with its uncorrected p and never returns
    SURVIVES: its verdict is EXPLORATORY, so a lead can be published and chased without being
    smuggled in as a finding.

    **`control`** is the same test with the common driver removed. A hypothesis that would survive
    but does not hold under its control is CONFOUNDED, not a signal.

    **Replication.** If `name` is already recorded, this row is a replica: it carries
    `replication_of` naming the original, inherits the original's `trials_so_far`, and does not
    raise the bar. If its numbers diverge from the original's it also carries
    `replication_delta`, because a re-run that cannot reproduce is a finding rather than a repeat.
    """
    if family not in KNOWN_FAMILIES:
        # An unfiled hypothesis would inherit a bar from nothing, so it is counted against the
        # whole ledger rather than silently escaping a multiplicity correction.
        family_bar_count = max(1, count_trials(ledger) + 1)
    else:
        family_bar_count = family_bar(family, ledger) + 1
    bar = threshold(family_bar_count)
    ledger_bar = threshold(count_trials(ledger) + 1)

    prior = [r for r in _ledger_rows(ledger) if r.get("name") == name]
    original = prior[0] if prior else None
    is_replication = original is not None
    # Distinct hypotheses, not rows: a re-run inherits its original's position and a new
    # hypothesis is charged for the distinct ones recorded before it.
    distinct = sum(1 for r in _ledger_rows(ledger) if not r.get("replication_of"))
    total = int((original or {}).get("trials_so_far", distinct + 1)) if is_replication else distinct + 1

    same_sign = (
        selection.rho == selection.rho and test.rho == test.rho and (selection.rho > 0) == (test.rho > 0)
    )
    if test.p != test.p:
        verdict = "UNDERPOWERED"
    elif kind == "exploratory":
        # Reported, never a finding. The uncorrected p travels with it so a reader can see
        # how close it came and what a confirmatory pass would need.
        verdict = "EXPLORATORY"
    elif test.p < bar and same_sign:
        verdict = "SURVIVES"
    elif test.p < bar:
        verdict = "SIGN_FLIPPED"
    else:
        verdict = "NOT_SIGNIFICANT"
    if verdict == "SURVIVES" and control is not None:
        holds = control.p == control.p and control.p < bar and (control.rho > 0) == (test.rho > 0)
        if not holds:
            verdict = "CONFOUNDED"

    detectable = min_detectable_effect(test.blocks)
    if verdict == "NOT_SIGNIFICANT" and detectable is not None and abs(test.rho) < detectable:
        # The test could not have seen an effect this small, whatever the verdict says.
        verdict = "UNDERPOWERED"

    row: dict[str, object] = {
        "kind": "signal", "name": name, "verdict": verdict, "bar": bar, "ledger_bar": ledger_bar,
        "family": family, "kind_of_test": kind, "trials_so_far": total, "min_detectable_rho": detectable,
        "selection": selection.__dict__, "test": test.__dict__,
        **({"control": control.__dict__} if control is not None else {}), **(extra or {}),
    }
    if is_replication:
        assert original is not None
        row["replication_of"] = original["name"]
        before = (original.get("test") or {}).get("rho")
        if isinstance(before, (int, float)) and before == before and test.rho == test.rho and test.rho != before:
            # Any difference at all, unrounded, so the reader decides what is material. A
            # re-run that cannot reproduce is a finding, not a repeat.
            row["replication_delta"] = round(test.rho - before, 9)
    record_trial(row, path=ledger or DEFAULT_SIGNAL_LEDGER)
    return row


def read_ledger(path: Path | None = None) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    try:
        with open(Path(path or DEFAULT_SIGNAL_LEDGER), encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    out.append(json.loads(line))
    except OSError:
        pass
    return out
