"""The v5 cross-sectional candidates: three textbook scores over the point-in-time universe.

Research only. Registered in `docs/research/specs/v5-xsection-top500.spec` before any of their
returns were computed; the parameters are the conventional ones from the literature, not chosen
from this data. Each candidate holds half of the current members, equal weight, against all
members equal weight (the exposure-matched buy-and-hold of the x2 rule), and is judged with the
other two as one Holm family. A score at session `t` reads returns strictly before `t`; a name
with less of its own history than the score needs is not eligible, and stays in the benchmark.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from statistics import pstdev
from typing import Any

from autopoiesis.research.gate_calibration import selection_series, v4_verdicts

EVERY = 4  # grid points of 5 sessions: rebalance every 20 sessions
SEED = 20261124  # bootstrap seed, registered


def _compound(r: Sequence[float]) -> float:
    out = 1.0
    for x in r:
        out *= 1.0 + x
    return out - 1.0


#: name -> (sessions of own history needed, score of a name's returns before t, hold the highest?)
CANDIDATES: dict[str, tuple[int, Callable[[Sequence[float], int], float], bool]] = {
    "MOM_12_1": (252, lambda r, t: _compound(r[t - 252:t - 21]), True),
    "REV_1M": (21, lambda r, t: _compound(r[t - 21:t]), False),
    "LOWVOL_3M": (63, lambda r, t: pstdev(r[t - 63:t]), False),
}


def selector(name: str, panel: Mapping[str, Any]) -> Callable[[int, Sequence[str]], list[str]]:
    """`select(t, members)` for candidate `name`: half of the members, by score, eligible names only."""
    need, score, highest = CANDIDATES[name]
    returns, first = panel["returns"], panel["first"]

    def select(t: int, members: Sequence[str]) -> list[str]:
        eligible = [s for s in members if first[s] <= t - need]
        scored = sorted(((score(returns[s], t), s) for s in eligible),
                        key=lambda x: ((-x[0] if highest else x[0]), x[1]))
        return [s for _, s in scored[:len(members) // 2]]

    return select


def run_family(panel: Mapping[str, Any], start: int, end: int) -> dict[str, Any]:
    """Every candidate over sessions [start, end), judged together by the x2 rule."""
    pairs, stats = {}, {}
    for name in CANDIDATES:
        bench, strat, st = selection_series(panel, start, end, EVERY, selector(name, panel))
        pairs[name] = (bench, strat)
        stats[name] = st
    verdicts = v4_verdicts(pairs, seed=SEED, ci_floor=0.0)
    return {name: {**verdicts[name], **stats[name], "annual_excess": verdicts[name]["mean_diff"] * 252,
                   "annual_ci_low": verdicts[name]["ci_low"] * 252, "sessions": end - start}
            for name in CANDIDATES}
