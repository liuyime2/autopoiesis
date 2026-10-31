"""What the signal ledger has found, read for the two production modules that act on it.

`runtime/autopoiesis/signal_trials.jsonl` is written by the research tree (which production may not
import) and read here, read-only: the curriculum shows a summary of it to the model, and strategy
admission refuses a rule that repeats a test the ledger already ran and found nothing in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: `intraday:SPY:M12->f48` is the return over 12 five-minute bars against the next 48; `S12` is the
#: distance from the 12-bar average. Both were tested two-sided (rank correlation), so a refusal
#: covers a momentum rule and a mean-reversion rule alike.
_INTRADAY_FAMILY = re.compile(r"^intraday:[A-Z]+:([MS])(\d+)->")
_RULE_SIGNAL_OF_FAMILY = {"M": "return_over_n", "S": "price_vs_sma"}


def ledger_summary(path: Path | str | None, *, examples: int = 6) -> dict[str, object] | None:
    """What the signal ledger has found so far, small enough to put in a prompt. None if empty.

    The curriculum used to propose strategies without seeing this, so it kept proposing hypotheses
    the ledger had already refuted (173 tests on 2026-10-24: 151 NOT_SIGNIFICANT, 6 SURVIVES, all
    of them about volatility, none about price direction).
    """
    rows = read_rows(path)
    if not rows:
        return None
    counts: dict[str, int] = {}
    for row in rows:
        verdict = str(row.get("verdict"))
        counts[verdict] = counts.get(verdict, 0) + 1

    def names(verdict: str) -> list[str]:
        return sorted({str(r.get("name")) for r in rows if r.get("verdict") == verdict})

    return {
        "tests": len(rows),
        "verdicts": dict(sorted(counts.items())),
        "survives": names("SURVIVES"),
        "exploratory": names("EXPLORATORY")[:examples],
        "refuted_examples": names("NOT_SIGNIFICANT")[:examples],
    }


def read_rows(path: Path | str | None) -> list[dict[str, object]]:
    """The ledger's JSON lines, or nothing when it is absent. Read-only, and unreadable lines are skipped."""
    rows: list[dict[str, object]] = []
    if path is None:
        return rows
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def refuted_lookbacks(rows: list[dict[str, object]]) -> dict[str, tuple[int, tuple[str, ...]]]:
    """Per RULE signal, the longest intraday lookback the ledger tested and found nothing in.

    `{"return_over_n": (48, ("intraday:SPY:M1->f12", ...)), "price_vs_sma": (48, (...))}`. A family
    with any SURVIVES row is left out: the ledger would then be evidence for the rule, not against it.
    Two caveats stay with the number. The tests are on SPY and are read as covering any symbol; and a
    RULE's lookback counts the prices the loop has observed, which are about five minutes apart but
    not exactly bars.
    """
    tested: dict[str, list[tuple[int, str]]] = {}
    survived: set[str] = set()
    for row in rows:
        match = _INTRADAY_FAMILY.match(str(row.get("name", "")))
        if match is None:
            continue
        signal = _RULE_SIGNAL_OF_FAMILY[match.group(1)]
        if row.get("verdict") == "SURVIVES":
            survived.add(signal)
        elif row.get("verdict") == "NOT_SIGNIFICANT":
            tested.setdefault(signal, []).append((int(match.group(2)), str(row["name"])))
    return {
        signal: (max(n for n, _ in found), tuple(sorted({name for _, name in found})))
        for signal, found in tested.items() if signal not in survived
    }
