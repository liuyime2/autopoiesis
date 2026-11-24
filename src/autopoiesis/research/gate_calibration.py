"""Stage 1: how often the G4 v4 verdict rule says PROVEN when it should not, and when it should.

Research only. The rule calibrated is the one `configs/experiment_portfolio_v4.spec` registers
and `tools/run_portfolio_experiment.py --strict` applies: primary family {B-A, C-B}, paired
block bootstrap (block 5, 2000 resamples), Holm; PROVEN only if Holm p < 0.05, the mean daily
difference >= 1%/yr of capital and the CI lower bound >= 1%/yr.

Every series here is SYNTHETIC, built from the real total-return-adjusted daily returns of the
seven v4 satellites: the days are permuted (one permutation shared by all symbols, so their
cross-correlation survives and their temporal structure does not). Nothing produced here is
evidence about real markets; it is evidence about the gate.

Scenarios:
- ``null_demeaned``: each symbol's returns demeaned before permuting. No drift, no timing, so
  any PROVEN is a false positive.
- ``null_drift``: permuted with the real drift kept. Still no timing. A PROVEN for B-A here
  means the rule credits a long/flat rule with plain market drift; the exposure-matched
  buy-and-hold benchmark is reported beside it.
- ``signal_<a>``: the demeaned null, plus `a`/252 added to the next day's return whenever arm
  B's own rule (SMA20, 1% band) is long. A known timing edge; PROVEN for B-A is a recovery.

Simplifications, stated so nobody reads more into this than it carries: satellite returns are
close-to-close with the decision at close i earning i->i+1 (the v4 replay fills at the next
open); each satellite is a fixed 5,000 of 55,000 capital with no compounding and no Guardian;
the core sleeve is identical in every arm and so cancels from every difference.
"""
from __future__ import annotations

import math
import multiprocessing
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from autopoiesis.research.portfolio_experiment import (
    SmaBand,
    WalkForwardSignal,
    held_returns,
    search_grid,
    signal_path,
)
from autopoiesis.research.statistics import holm_adjust, paired_block_bootstrap

SATELLITES = ("XLB", "XLE", "XLF", "QQQ", "NVDA", "MSFT", "AAPL")
SATELLITE_POOL = 5_000.0
CAPITAL = 55_000.0
COST = 0.0003
MIN_EFFECT_DAILY = 0.01 / 252
BLOCK = 5
N_BOOT = 2000


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denom
    spread = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denom
    return max(0.0, centre - spread), min(1.0, centre + spread)


def v4_verdicts(
    pairs: Mapping[str, tuple[Sequence[float], Sequence[float]]],
    *,
    seed: int = 0,
    ci_floor: float = MIN_EFFECT_DAILY,
    scale: float = 1.0,
) -> dict[str, dict[str, Any]]:
    """The v4 rule on a family of (baseline, candidate) daily-return pairs.

    Same resampling as the registered runner (block 5, 2000 resamples, seed fixed per call).
    `ci_floor` and `scale` exist only to measure candidate replacements of the rule: the
    registered rule is the default (CI lower bound >= min_effect, returns on total capital).
    A `ci_floor` of 0 requires the lower bound to be strictly above 0.
    """
    names = list(pairs)
    boots = {
        n: paired_block_bootstrap(a, b, block_size=BLOCK, n_boot=N_BOOT, seed=seed)
        for n, (a, b) in pairs.items()
    }
    adjusted = holm_adjust([boots[n]["p_two_sided"] for n in names])
    out = {}
    for n, h in zip(names, adjusted, strict=True):
        b = boots[n]
        out[n] = {
            "mean_diff": b["mean_diff"],
            "ci_low": b["ci_low"],
            "p_two_sided": b["p_two_sided"],
            "holm_p": h,
            "proven": bool(
                h < 0.05
                and b["mean_diff"] * scale >= MIN_EFFECT_DAILY
                and (b["ci_low"] * scale >= ci_floor if ci_floor > 0 else b["ci_low"] > 0)
            ),
        }
    return out


def returns_of(closes: Sequence[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]


def synthetic_closes(
    returns: Mapping[str, Sequence[float]],
    order: Sequence[int],
    *,
    demean: bool,
    inject: float = 0.0,
) -> dict[str, list[float]]:
    """Rebuild each symbol's closes from permuted (optionally demeaned) returns.

    With `inject` > 0 the next return gains `inject`/252 whenever SmaBand(20, 0.01), run
    causally on the closes built so far, is long.
    """
    out = {}
    for symbol, rets in returns.items():
        mean = sum(rets) / len(rets) if demean else 0.0
        rule = SmaBand(20, 0.01).fresh()
        closes = [100.0]
        for i in order:
            r = rets[i] - mean
            if inject and rule(closes):
                r += inject / 252.0
            closes.append(closes[-1] * (1.0 + r))
        out[symbol] = closes
    return out


def portfolio(per_symbol: Mapping[str, Sequence[float]]) -> list[float]:
    """Daily return on capital of the satellites, each a fixed pool."""
    n = min(len(v) for v in per_symbol.values())
    w = SATELLITE_POOL / CAPITAL
    return [w * sum(v[t] for v in per_symbol.values()) for t in range(n)]


def evaluate_run(closes: dict[str, list[float]], *, seed: int) -> dict[str, Any]:
    """Arms A (cash), B (SMA20 1% band) and C (walk-forward over the 17 configs) on one world."""
    b_sat, c_sat, bench_sat, bench_c_sat = {}, {}, {}, {}
    for symbol, cl in closes.items():
        b_path = signal_path(SmaBand(20, 0.01).fresh, cl)
        c_path = signal_path(lambda: WalkForwardSignal(grid=search_grid()), cl)
        b_sat[symbol] = held_returns(b_path, cl, COST)
        c_sat[symbol] = held_returns(c_path, cl, COST)
        held = returns_of(cl)
        frac_b = sum(b_path[:-1]) / max(1, len(b_path) - 1)
        frac_c = sum(c_path[:-1]) / max(1, len(c_path) - 1)
        bench_sat[symbol] = [frac_b * r for r in held]
        bench_c_sat[symbol] = [frac_c * r for r in held]
    a = [0.0] * len(next(iter(b_sat.values())))
    b, c = portfolio(b_sat), portfolio(c_sat)
    bench_b, bench_c = portfolio(bench_sat), portfolio(bench_c_sat)
    family = v4_verdicts({"B-A": (a, b), "C-B": (b, c)}, seed=seed)
    exposure = v4_verdicts({"B-BENCH_EXPOSURE_B": (bench_b, b)}, seed=seed)
    # Candidate replacements, measured on the same world; none of them is registered.
    exposure_family = {"B-BENCH_B": (bench_b, b), "C-BENCH_C": (bench_c, c)}
    satellite_scale = CAPITAL / (SATELLITE_POOL * len(closes))
    variants = {
        "x1_exposure": v4_verdicts(exposure_family, seed=seed),
        "x2_exposure_ci0": v4_verdicts(exposure_family, seed=seed, ci_floor=0.0),
        "x3_exposure_ci0_satcap": v4_verdicts(exposure_family, seed=seed, ci_floor=0.0, scale=satellite_scale),
    }
    return {
        **{f"{v}:any": any(r["proven"] for r in res.values()) for v, res in variants.items()},
        **{f"{v}:B": res["B-BENCH_B"]["proven"] for v, res in variants.items()},
        "B-A": family["B-A"]["proven"],
        "C-B": family["C-B"]["proven"],
        "any": family["B-A"]["proven"] or family["C-B"]["proven"],
        "B-BENCH_EXPOSURE_B": exposure["B-BENCH_EXPOSURE_B"]["proven"],
        "b_annual_excess": family["B-A"]["mean_diff"] * 252,
        "c_minus_b_annual": family["C-B"]["mean_diff"] * 252,
    }


def _worker(task: tuple[str, int, dict[str, list[float]], bool, float]) -> tuple[str, int, dict[str, Any]]:
    scenario, seed, returns, demean, inject = task
    n = len(next(iter(returns.values())))
    order = list(range(n))
    random.Random(seed).shuffle(order)
    world = synthetic_closes(returns, order, demean=demean, inject=inject)
    return scenario, seed, evaluate_run(world, seed=seed)


SCENARIOS: dict[str, tuple[bool, float]] = {
    "null_demeaned": (True, 0.0),
    "null_drift": (False, 0.0),
    "signal_2pct": (True, 0.02),
    "signal_5pct": (True, 0.05),
    "signal_10pct": (True, 0.10),
    "signal_20pct": (True, 0.20),
    "signal_40pct": (True, 0.40),
}


def run(
    returns: dict[str, list[float]],
    runs: dict[str, int],
    *,
    base_seed: int = 20261120,
    workers: int = 16,
) -> dict[str, Any]:
    tasks = []
    for k, (scenario, count) in enumerate(runs.items()):
        demean, inject = SCENARIOS[scenario]
        tasks += [(scenario, base_seed + 100_000 * k + i, returns, demean, inject) for i in range(count)]
    rows: dict[str, list[dict[str, Any]]] = {s: [] for s in runs}
    # Not ProcessPoolExecutor: on Python 3.10 its submit() can deadlock against its manager
    # thread once the wakeup pipe fills (CPython gh-105829). Measured 2026-11-24: the v5 power
    # run hung 8 h 47 min that way. `Pool` has no wakeup pipe; `imap` keeps the order.
    with multiprocessing.get_context("fork").Pool(workers) as pool:
        for scenario, seed, row in pool.imap(_worker, tasks, chunksize=1):
            rows[scenario].append({"seed": seed, **row})
    summary: dict[str, Any] = {}
    for scenario, rs in rows.items():
        rs.sort(key=lambda r: r["seed"])
        entry: dict[str, Any] = {"runs": len(rs)}
        for key in [k for k in rs[0] if isinstance(rs[0][k], bool)] if rs else []:
            k = sum(1 for r in rs if r[key])
            lo, hi = wilson_interval(k, len(rs))
            entry[key] = {"proven": k, "rate": k / len(rs) if rs else 0.0, "wilson_95": [lo, hi]}
        entry["mean_b_annual_excess"] = sum(r["b_annual_excess"] for r in rs) / max(1, len(rs))
        summary[scenario] = entry
    return {"summary": summary, "rows": rows}


# ----------------------------------------------------------------------------- breadth
#: Panel returns shared with worker processes once, instead of being pickled into every task.
_PANEL: dict[str, list[float]] = {}


def _init_panel(panel: dict[str, list[float]]) -> None:
    global _PANEL
    _PANEL = panel


def breadth_run(n: int, seed: int, inject: float, panel: Mapping[str, Sequence[float]] | None = None) -> dict[str, Any]:
    """One synthetic world of `n` symbols drawn from the panel; arm B against its exposure.

    Total capital is fixed and split evenly over the `n` symbols, so an effect measured on
    capital means the same thing at every breadth. The rule is candidate x2 (exposure-matched
    buy-and-hold, CI lower bound > 0, mean >= 1%/yr); the edge, when injected, is B's own.
    """
    source = panel if panel is not None else _PANEL
    rng = random.Random(seed)
    names = rng.sample(sorted(source), n)
    length = len(source[names[0]])
    order = list(range(length))
    rng.shuffle(order)
    world = synthetic_closes({s: source[s] for s in names}, order, demean=True, inject=inject)
    weight = 1.0 / n
    strat: list[float] = [0.0] * length
    bench: list[float] = [0.0] * length
    for cl in world.values():
        path = signal_path(SmaBand(20, 0.01).fresh, cl)
        held = held_returns(path, cl, COST)
        frac = sum(path[:-1]) / max(1, len(path) - 1)
        for t, (h, r) in enumerate(zip(held, returns_of(cl), strict=True)):
            strat[t] += weight * h
            bench[t] += weight * frac * r
    verdict = v4_verdicts({"B-BENCH_B": (bench, strat)}, seed=seed, ci_floor=0.0)["B-BENCH_B"]
    return {"n": n, "seed": seed, "proven": verdict["proven"], "annual_excess": verdict["mean_diff"] * 252,
            "symbols": names}


def cross_section_run(
    n: int,
    seed: int,
    inject: float,
    panel: Mapping[str, Sequence[float]] | None = None,
    rebalance_every: int = 1,
) -> dict[str, Any]:
    """One synthetic world: hold the top half of `n` names by 20-day return, against all `n`.

    Both portfolios are equal weight and fully invested on the same capital, so the market
    factor cancels and only the names' relative behaviour is tested. When injected, the edge
    is idiosyncratic: a name's next return gains `inject`/252 while it ranks in the top half.
    The holdings are re-ranked every `rebalance_every` closes and kept in between; the edge
    follows the current rank, so rebalancing less often trades some of it for lower turnover.
    """
    source = panel if panel is not None else _PANEL
    rng = random.Random(seed)
    names = rng.sample(sorted(source), n)
    length = len(source[names[0]])
    order = list(range(length))
    rng.shuffle(order)
    rets = {s: list(source[s]) for s in names}
    means = {s: sum(r) / len(r) for s, r in rets.items()}
    lookback = 20
    top = max(1, n // 2)
    closes = {s: [100.0] for s in names}
    held: list[str] = []
    rebalances = 0
    strat: list[float] = []
    bench: list[float] = []
    for step, i in enumerate(order):
        # Rank on the closes seen so far; the ranking at close `step` decides the next return.
        if step >= lookback:
            score = {s: closes[s][-1] / closes[s][-1 - lookback] for s in names}
            ranked = sorted(names, key=lambda s: (-score[s], s))[:top]
        else:
            ranked = []
        day = {s: rets[s][i] - means[s] + (inject / 252.0 if s in ranked else 0.0) for s in names}
        for s in names:
            closes[s].append(closes[s][-1] * (1.0 + day[s]))
        turnover = 0.0
        if ranked and (not held or step % rebalance_every == 0):
            turnover = len(set(ranked) ^ set(held)) / (2 * top) if held else 1.0
            held = ranked
            rebalances += 1
        if held:
            strat.append(sum(day[s] for s in held) / top - COST * turnover)
        else:
            strat.append(sum(day.values()) / n)
        bench.append(sum(day.values()) / n)
    verdict = v4_verdicts({"TOP-EW": (bench, strat)}, seed=seed, ci_floor=0.0)["TOP-EW"]
    return {"n": n, "seed": seed, "proven": verdict["proven"], "annual_excess": verdict["mean_diff"] * 252,
            "symbols": names, "rebalances": rebalances}


def _breadth_worker(task: tuple[str, int, int, float, str, int]) -> tuple[str, dict[str, Any]]:
    scenario, n, seed, inject, kind, rebalance_every = task
    if kind == "cross_section":
        return scenario, cross_section_run(n, seed, inject, rebalance_every=rebalance_every)
    return scenario, breadth_run(n, seed, inject)


def breadth_power(
    panel: dict[str, list[float]],
    n_values: Sequence[int],
    scenarios: Mapping[str, float],
    runs: int,
    *,
    base_seed: int = 20261121,
    workers: int = 16,
    kind: str = "timing",
    rebalance_every: int = 1,
) -> dict[str, Any]:
    tasks = [(f"{name}@N={n}", n, base_seed + 1_000 * n + 100_000 * k + i, inject, kind, rebalance_every)
             for k, (name, inject) in enumerate(scenarios.items()) for n in n_values for i in range(runs)]
    rows: dict[str, list[dict[str, Any]]] = {}
    with multiprocessing.get_context("fork").Pool(workers, _init_panel, (panel,)) as pool:
        for key, row in pool.imap(_breadth_worker, tasks, chunksize=1):
            rows.setdefault(key, []).append(row)
    summary = {}
    for key, rs in rows.items():
        rs.sort(key=lambda r: r["seed"])
        k = sum(1 for r in rs if r["proven"])
        lo, hi = wilson_interval(k, len(rs))
        summary[key] = {"runs": len(rs), "proven": k, "rate": k / len(rs), "wilson_95": [lo, hi],
                        "mean_annual_excess": sum(r["annual_excess"] for r in rs) / len(rs)}
    return {"summary": summary, "rows": rows}


# --- Power on the real universe (2026-11-22) -------------------------------------------------
#
# The cross-section study above draws worlds from a panel of names with complete history, which
# is survivors only, and measures one length: the panel's. This one uses the survivorship-free
# universe (`tools/fetch_universe_bars.py`) with point-in-time membership, and asks what the x2
# rule can see over the window lengths a holdout or forward test would actually have.
#
# The candidate is a placebo: a seeded hash of the symbol picks half of the members, so the
# selection knows nothing about returns. Its real noise against equal weight (the random sector
# and size tilts) survives, which is what decides how often a meaningless selection passes the
# rule on this sample. With `inject`, held names gain `inject`/252 a day: SYNTHETIC, labelled.

_UNIVERSE: dict[str, Any] = {}


def _init_universe(panel: dict[str, Any]) -> None:
    global _UNIVERSE
    _UNIVERSE = panel


def universe_panel(
    bars_by_symbol: Mapping[str, Sequence[Any]],
    members_by_date: Mapping[Any, Sequence[str]],
    cutoff: Any,
) -> dict[str, Any]:
    """Daily returns of every symbol that was ever a member, on the sessions before `cutoff`.

    The calendar is every date any symbol has a bar on, before `cutoff`; nothing on or after it
    is read. A return is close over the last close seen (a gap earns 0 and the next bar catches
    up), and after a symbol's last bar it is 0: cash at the last close, which is about what an
    acquiree's holders were paid. `members_by_date` maps a session to its members, decided from
    bars before it (`universe.members_at`); a session there is a rebalance point.
    """
    calendar = sorted({b.timestamp.date() for bars in bars_by_symbol.values() for b in bars
                       if b.timestamp.date() < cutoff})
    index = {d: i for i, d in enumerate(calendar)}
    grid = sorted(d for d in members_by_date if d in index)
    names = sorted({s for d in grid for s in members_by_date[d]})
    returns: dict[str, list[float]] = {}
    for s in names:
        closes: dict[int, float] = {index[b.timestamp.date()]: b.close for b in bars_by_symbol.get(s, ())
                                    if b.timestamp.date() < cutoff}
        r, last = [0.0] * len(calendar), None
        for i in range(len(calendar)):
            c = closes.get(i)
            if c is not None:
                if last is not None:
                    r[i] = c / last - 1.0
                last = c
        returns[s] = r
    first = {s: min((index[b.timestamp.date()] for b in bars_by_symbol.get(s, ()) if b.timestamp.date() < cutoff),
                    default=len(calendar)) for s in names}
    return {"calendar": calendar, "grid": [index[d] for d in grid], "first": first,
            "members": [list(members_by_date[d]) for d in grid], "returns": returns}


def _placebo_held(seed: int, symbol: str) -> bool:
    import hashlib

    return hashlib.sha256(f"{seed}:{symbol}".encode()).digest()[0] % 2 == 0


def selection_series(
    panel: Mapping[str, Any],
    start: int,
    end: int,
    every: int,
    select: Callable[[int, Sequence[str]], list[str]],
    inject: float = 0.0,
) -> tuple[list[float], list[float], dict[str, Any]]:
    """(benchmark, candidate, stats) over sessions [start, end) of a selection from the members.

    Rebalances on every `every`-th grid point: `select(t, members)` is called at the window's
    start and at each rebalance session `t`, and must decide from returns before `t` only (the
    membership already does). Equal weight, fully invested, same capital, so the benchmark is
    the exposure-matched buy-and-hold of the x2 rule; cost is `COST` x turnover on the candidate
    only, as in `cross_section_run`. `inject`/252 a day on held names is SYNTHETIC.
    """
    grid, members, returns = panel["grid"][::every], panel["members"][::every], panel["returns"]
    g = max(j for j, t in enumerate(grid) if t <= start)
    held: list[str] = []
    current: Sequence[str] = ()
    strat: list[float] = []
    bench: list[float] = []
    rebalances, turnovers = 0, []
    for t in range(start, end):
        turnover = 0.0
        if t == start or (g + 1 < len(grid) and grid[g + 1] == t):
            if t != start:
                g += 1
            new = select(t, members[g])
            turnover = len(set(new) ^ set(held)) / (2 * max(1, len(new))) if held else 1.0
            held, current = new, members[g]
            rebalances += 1
            turnovers.append(turnover)
        day = [returns[s][t] for s in current]
        bench.append(sum(day) / len(day))
        if held:
            strat.append(sum(returns[s][t] for s in held) / len(held) + inject / 252.0 - COST * turnover)
        else:
            strat.append(bench[-1])
    return bench, strat, {"rebalances": rebalances, "mean_turnover": sum(turnovers) / len(turnovers)}


def placebo_series(
    length: int, every: int, seed: int, inject: float, panel: Mapping[str, Any]
) -> tuple[list[float], list[float], int]:
    """(benchmark, candidate, start index) for one placebo window of `length` sessions.

    The window's start is drawn by `seed` from the sessions where the grid has begun; the
    selection is the seeded symbol hash (`_placebo_held`), so it knows nothing about returns.
    """
    first, total = panel["grid"][0], len(panel["calendar"])
    if total - first < length:
        raise ValueError(f"window of {length} sessions does not fit after the first rebalance")
    start = random.Random(seed).randint(first, total - length)
    bench, strat, _ = selection_series(
        panel, start, start + length, every,
        lambda _t, members: [s for s in members if _placebo_held(seed, s)], inject)
    return bench, strat, start


def placebo_universe_run(
    length: int, every: int, seed: int, inject: float, panel: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """One placebo window (`placebo_series`) judged by the x2 rule."""
    u = panel if panel is not None else _UNIVERSE
    bench, strat, start = placebo_series(length, every, seed, inject, u)
    verdict = v4_verdicts({"PLACEBO": (bench, strat)}, seed=seed, ci_floor=0.0)["PLACEBO"]
    return {"length": length, "every": every, "seed": seed, "inject": inject, "start": u["calendar"][start].isoformat(),
            "proven": verdict["proven"], "annual_excess": verdict["mean_diff"] * 252, "ci_low": verdict["ci_low"]}


def _universe_worker(task: tuple[str, int, int, int, float]) -> tuple[str, dict[str, Any]]:
    key, length, every, seed, inject = task
    return key, placebo_universe_run(length, every, seed, inject)


def universe_power(
    panel: dict[str, Any],
    lengths: Sequence[int],
    everys: Sequence[int],
    scenarios: Mapping[str, float],
    runs: int,
    *,
    base_seed: int = 20261122,
    workers: int = 16,
) -> dict[str, Any]:
    """PROVEN rate of the placebo for each (scenario, length, every); seeds shared across scenarios."""
    tasks = [(f"{name}@L={n}@every={k}", n, k, base_seed + i, inject)
             for name, inject in scenarios.items() for n in lengths for k in everys for i in range(runs)]
    rows: dict[str, list[dict[str, Any]]] = {}
    with multiprocessing.get_context("fork").Pool(workers, _init_universe, (panel,)) as pool:
        for key, row in pool.imap(_universe_worker, tasks, chunksize=4):
            rows.setdefault(key, []).append(row)
    summary = {}
    for key, rs in rows.items():
        rs.sort(key=lambda r: r["seed"])
        k = sum(1 for r in rs if r["proven"])
        lo, hi = wilson_interval(k, len(rs))
        summary[key] = {"runs": len(rs), "proven": k, "rate": k / len(rs), "wilson_95": [lo, hi],
                        "mean_annual_excess": sum(r["annual_excess"] for r in rs) / len(rs)}
    return {"summary": summary, "rows": rows}
