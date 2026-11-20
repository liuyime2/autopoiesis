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

import concurrent.futures
import math
import random
from collections.abc import Mapping, Sequence
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
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        for scenario, seed, row in pool.map(_worker, tasks, chunksize=1):
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
