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
4. clear a significance bar that counts **every hypothesis the ledger has ever seen**, not only
   this search's (`threshold`). A larger search raises its own bar and cannot buy a cheaper verdict
   by trying harder, which is the same rule `research.driver` applies to strategies;
5. have an interval from resampling **whole blocks** (trading days, months) rather than single
   bars, because neighbouring observations share their session and a bar-level interval is far
   too narrow;
6. where the hypothesis shares a common driver with the thing it claims to predict, **still hold
   once that driver is removed** (`control`). Significance cannot tell a signal from a confound:
   "high-volatility stocks earn more" cleared the bar above (p = 0.0003, the same sign in both
   periods) and was beta in disguise - it earned +0.16 in months the market rose, -0.09 in months
   it fell, and +0.02 once beta times the market was taken out.

Pure standard library, like the rest of this package, and it writes only its own ledger.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from min_agent.research.trials import record_trial

DEFAULT_SIGNAL_LEDGER = Path("runtime") / "min_agent" / "signal_trials.jsonl"
#: The family-wise error rate the whole ledger is held to.
FAMILY_ALPHA = 0.05


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


def judge(
    name: str,
    selection: CorrelationResult,
    test: CorrelationResult,
    *,
    control: CorrelationResult | None = None,
    ledger: Path | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """Record the hypothesis, then say whether it survives.

    The count includes this hypothesis and everything recorded before it, so the bar rises as the
    ledger grows. Recording comes first and cannot be skipped: a hypothesis that failed leaves the
    same trace as one that passed.

    `control` is the same test with the common driver removed (for a stock-selection signal: the
    return in excess of beta times the market). A hypothesis that would survive but does not hold
    under its control is CONFOUNDED, not a signal.
    """
    total = count_trials(ledger) + 1
    bar = threshold(total)
    same_sign = (
        selection.rho == selection.rho and test.rho == test.rho and (selection.rho > 0) == (test.rho > 0)
    )
    if test.p != test.p:
        verdict = "UNDERPOWERED"
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
    row: dict[str, object] = {
        "kind": "signal", "name": name, "verdict": verdict, "bar": bar, "trials_so_far": total,
        "selection": selection.__dict__, "test": test.__dict__,
        **({"control": control.__dict__} if control is not None else {}), **(extra or {}),
    }
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
