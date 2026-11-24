"""The v5 cross-sectional candidates: three textbook scores over the point-in-time universe.

Research only. Registered in `docs/research/specs/v5-xsection-top500.spec` before any of their
returns were computed; the parameters are the conventional ones from the literature, not chosen
from this data. Each candidate holds half of the current members, equal weight, against all
members equal weight (the exposure-matched buy-and-hold of the x2 rule), and is judged with the
other two as one Holm family. A score at session `t` reads returns strictly before `t`; a name
with less of its own history than the score needs is not eligible, and stays in the benchmark.
"""
from __future__ import annotations

import multiprocessing
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


# --- What a one-year holdout can confirm (2026-11-24) ----------------------------------------
#
# The 11-23 placebo study measured power for a selection with ~2%/yr tracking error; the v5
# candidates' development tracking error is 6.4-9.3%/yr, so its power figures do not carry over.
# This measures power on the candidates' own noise: their development difference series
# (candidate minus equal weight), demeaned so it carries no edge, sampled as one contiguous
# window shared by all three (their volatility, autocorrelation and correlation survive), with
# an edge injected in information-ratio units: IR x TE per year. SYNTHETIC injection.

_DIFFS: dict[str, list[float]] = {}


def _init_diffs(diffs: dict[str, list[float]]) -> None:
    global _DIFFS
    _DIFFS = diffs


def tracking_error(d: Sequence[float]) -> float:
    """Annualised population stdev of a daily difference series."""
    return pstdev(d) * 252 ** 0.5


def residual_run(
    diffs: Mapping[str, Sequence[float]], length: int, seed: int, inject_ir: Mapping[str, float]
) -> dict[str, Any]:
    """One simulated holdout: a window of `length` demeaned sessions, `inject_ir[c]` x TE_c added."""
    import random

    n = len(next(iter(diffs.values())))
    start = random.Random(seed).randint(0, n - length)
    pairs = {}
    for c, d in diffs.items():
        mean, te = sum(d) / len(d), tracking_error(d)
        add = inject_ir.get(c, 0.0) * te / 252.0
        pairs[c] = ([0.0] * length, [x - mean + add for x in d[start:start + length]])
    verdicts = v4_verdicts(pairs, seed=seed, ci_floor=0.0)
    return {"seed": seed, "start": start, **{c: {"proven": v["proven"], "annual_excess": v["mean_diff"] * 252}
                                              for c, v in verdicts.items()}}


def _residual_worker(task: tuple[str, int, int, dict[str, float]]) -> tuple[str, dict[str, Any]]:
    key, length, seed, inject = task
    return key, residual_run(_DIFFS, length, seed, inject)


def residual_power(
    diffs: dict[str, list[float]],
    lengths: Sequence[int],
    irs: Sequence[float],
    runs: int,
    *,
    base_seed: int = 20261124,
    workers: int = 16,
) -> dict[str, Any]:
    """PROVEN rates by (mode, length, IR). Mode `all` injects every candidate; `single:<c>` only c.

    Seeds are shared across IRs and modes, so the cells differ only by the injection.
    """
    from autopoiesis.research.gate_calibration import wilson_interval

    modes = {"all": None, **{f"single:{c}": c for c in diffs}}
    tasks = []
    for mode, target in modes.items():
        for n in lengths:
            for ir in irs:
                inject = dict.fromkeys(diffs, ir) if target is None else {target: ir}
                tasks += [(f"{mode}@L={n}@IR={ir:g}", n, base_seed + i, inject) for i in range(runs)]
    rows: dict[str, list[dict[str, Any]]] = {}
    # Pool, not ProcessPoolExecutor: see gate_calibration.run (CPython gh-105829).
    with multiprocessing.get_context("fork").Pool(workers, _init_diffs, (diffs,)) as pool:
        for key, row in pool.imap(_residual_worker, tasks, chunksize=8):
            rows.setdefault(key, []).append(row)
    summary: dict[str, Any] = {}
    for key, rs in rows.items():
        rs.sort(key=lambda r: r["seed"])
        mode = key.split("@", 1)[0]
        judged = list(diffs) if mode == "all" else [mode.split(":", 1)[1]]
        cell: dict[str, Any] = {"runs": len(rs)}
        for c in judged + (["any"] if mode == "all" else []):
            k = sum(1 for r in rs if (any(r[x]["proven"] for x in diffs) if c == "any" else r[c]["proven"]))
            lo, hi = wilson_interval(k, len(rs))
            cell[c] = {"proven": k, "rate": k / len(rs), "wilson_95": [lo, hi]}
        summary[key] = cell
    return {"summary": summary, "rows": rows,
            "tracking_error": {c: tracking_error(d) for c, d in diffs.items()}}
