"""Run the registered G4 v3 portfolio experiment (configs/experiment_portfolio_v3.spec).

Research only; reads cached real daily bars and never a broker. Every arm and control goes
through `research.experiment.run_arm` (production Guardian, next-open fills, explicit costs).

    python tools/run_portfolio_experiment.py --out reports/20261107/v3 [--slippage 0.0003]

Writes result.json (deterministic: no timestamps), run_manifest.json, statistical_report.md,
portfolio_comparison.csv and the walk-forward choices. D is not run.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.replay import load_bars
from autopoiesis.research.experiment import (
    BuyAndHoldEngine,
    CostModel,
    RiskLimits,
    SleeveEngine,
    run_arm,
)
from autopoiesis.research.manifest import dataset_fingerprint, sha256_file
from autopoiesis.research.portfolio_experiment import (
    CashEngine,
    MarkovTiming,
    Reversed,
    Shuffled,
    SmaBand,
    TargetEngine,
    WalkForwardSignal,
    deployed_fraction,
    held_returns,
    long_runs,
    portfolio_invariants,
    search_grid,
    signal_path,
    sum_curves,
)
from autopoiesis.research.statistics import (
    daily_returns,
    deflated_sharpe_ratio,
    holm_adjust,
    paired_block_bootstrap,
    probability_of_backtest_overfitting,
)

CORE = "SPY"
SATELLITES = ("QQQ", "IWM", "TLT", "GLD", "XLF", "XLE")
SLEEVE_POOL = 20_000.0
SATELLITE_POOL = 5_000.0
CAPITAL = SLEEVE_POOL + SATELLITE_POOL * len(SATELLITES)
MIN_EFFECT_DAILY = 0.01 / 252
SEEDS = range(10)
CACHE = ROOT / "runtime" / "autopoiesis" / "replay"
DEFAULT_CACHE_NAME = "{symbol}_1Day_2020-07-27_2026-11-06.json"


def _commit() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _r(x: float, n: int = 8) -> float:
    return round(float(x), n)


def main(argv: list[str] | None = None) -> int:
    global SATELLITES, CAPITAL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cache-dir", default=str(CACHE))
    parser.add_argument("--slippage", type=float, default=0.0003)
    parser.add_argument("--spec", default=str(ROOT / "configs" / "experiment_portfolio_v3.spec"))
    parser.add_argument("--satellites", default=",".join(SATELLITES), help="comma-separated satellite symbols")
    parser.add_argument("--cache-name", default=DEFAULT_CACHE_NAME, help="bar cache file name with a {symbol} placeholder")
    parser.add_argument(
        "--strict", action="store_true",
        help="v4 rules: PROVEN also needs the CI lower bound above min_effect; report autocorrelation and block sensitivity",
    )
    parser.add_argument("--universe-label", default="", help="written to result.json when --strict (e.g. production-permitted)")
    args = parser.parse_args(argv)

    SATELLITES = tuple(s.strip().upper() for s in args.satellites.split(",") if s.strip())
    CAPITAL = SLEEVE_POOL + SATELLITE_POOL * len(SATELLITES)

    bars = {}
    data = {}
    for symbol in (CORE, *SATELLITES):
        path = Path(args.cache_dir) / args.cache_name.format(symbol=symbol)
        if not path.exists():
            raise SystemExit(f"missing cached bars: {path} (fetch them first; this runner never calls a broker)")
        bars[symbol] = load_bars(path)
        data[symbol] = {
            "bars": len(bars[symbol]),
            "first": bars[symbol][0].timestamp.date().isoformat(),
            "last": bars[symbol][-1].timestamp.date().isoformat(),
            "sha256": dataset_fingerprint(symbol, bars[symbol]),
        }
    dates = {s: [b.timestamp.date() for b in bs] for s, bs in bars.items()}
    if len({tuple(d) for d in dates.values()}) != 1:
        raise SystemExit("bar dates differ across symbols; the summed curve would mix days")

    risk = RiskLimits(allowlist=frozenset((CORE, *SATELLITES)))
    costs = CostModel(slippage_pct=args.slippage, commission_pct=0.0)

    def run(symbol: str, engine, arm: str, seed: int = 0, pool: float = SATELLITE_POOL):
        return run_arm(
            arm=arm, bars=bars[symbol], engine=engine, seed=seed, starting_equity=pool,
            symbol=symbol, risk=risk, costs=costs,
        )

    # Core: identical in every arm, run once.
    core = run(CORE, SleeveEngine(pool=SLEEVE_POOL, target_vol_pct=10.0, symbol=CORE), "core", pool=SLEEVE_POOL)
    core_fraction = deployed_fraction(core, bars[CORE], SLEEVE_POOL)

    arms: dict[str, dict] = {}
    engines: dict[str, dict] = {}
    wf_choices: dict[str, list] = {}

    def satellite_arm(name: str, make_signal) -> None:
        books = {}
        engines[name] = {}
        for symbol in SATELLITES:
            signal = make_signal(symbol)
            engine = TargetEngine(signal=signal, pool=SATELLITE_POOL, strategy_id=f"{name}-{symbol}")
            books[symbol] = run(symbol, engine, name)
            engines[name][symbol] = engine
            if isinstance(signal, WalkForwardSignal):
                wf_choices.setdefault(name, []).append(
                    {"symbol": symbol, "choices": [[t, bars[symbol][t].timestamp.date().isoformat(), c] for t, c in signal.choices]}
                )
        arms[name] = books

    arms["A"] = {s: run(s, CashEngine(), "A") for s in SATELLITES}
    satellite_arm("B", lambda s: SmaBand(20, 0.01))
    satellite_arm("C", lambda s: WalkForwardSignal(grid=search_grid(), cost=args.slippage))

    def portfolio(books: dict) -> list[float]:
        return sum_curves([core.equity_curve, *[books[s].equity_curve for s in SATELLITES]])

    curves = {name: portfolio(books) for name, books in arms.items()}

    # Controls for B and C: random timing at each arm's own per-symbol exposure; B reversed; B shuffled.
    controls: dict[str, list[float]] = {}
    seed_sweep: dict[str, list[float]] = {}
    for name in ("B", "C"):
        finals = []
        for seed in SEEDS:
            books = {}
            for symbol in SATELLITES:
                share, run_len = long_runs(engines[name][symbol].states)
                signal = MarkovTiming(long_fraction=share, mean_run=run_len, seed=seed * 101 + SATELLITES.index(symbol))
                books[symbol] = run(symbol, TargetEngine(signal=signal, pool=SATELLITE_POOL, strategy_id="n1"), f"N1_{name}", seed)
            curve = portfolio(books)
            if seed == 0:
                controls[f"N1_random_timing_{name}"] = curve
            finals.append(curve[-1])
            seed_sweep.setdefault(f"N1_random_timing_{name}", []).append(curve[-1])
    controls["N2_reversed_B"] = portfolio({
        s: run(s, TargetEngine(signal=Reversed(SmaBand(20, 0.01)), pool=SATELLITE_POOL, strategy_id="n2"), "N2") for s in SATELLITES
    })
    controls["N3_shuffled_B_drift"] = portfolio({
        s: run(s, TargetEngine(signal=Shuffled(lambda: SmaBand(20, 0.01), seed=0), pool=SATELLITE_POOL, strategy_id="n3"), "N3")
        for s in SATELLITES
    })

    # Benchmarks.
    def bench(fractions: dict[str, float], core_value: float) -> list[float]:
        core_book = run(CORE, BuyAndHoldEngine(max_position_value=core_value), "bench", pool=SLEEVE_POOL)
        books = {
            s: run(s, BuyAndHoldEngine(max_position_value=SATELLITE_POOL * fractions[s]), "bench")
            for s in SATELLITES
        }
        return sum_curves([core_book.equity_curve, *[books[s].equity_curve for s in SATELLITES]])

    benchmarks = {"BENCH_POOL": bench(dict.fromkeys(SATELLITES, 0.98), SLEEVE_POOL)}
    exposure: dict[str, dict] = {"core_SPY": _r(core_fraction, 4)}
    for name in ("A", "B", "C"):
        fr = {s: deployed_fraction(arms[name][s], bars[s], SATELLITE_POOL) for s in SATELLITES}
        exposure[name] = {s: _r(v, 4) for s, v in fr.items()}
        benchmarks[f"BENCH_EXPOSURE_{name}"] = bench(fr, SLEEVE_POOL * core_fraction)

    # Statistics on daily portfolio returns.
    rets = {k: daily_returns(v) for k, v in {**curves, **controls, **benchmarks}.items()}

    def compare(a: str, b: str) -> dict:
        res = paired_block_bootstrap(rets[a], rets[b], block_size=5, n_boot=2000, seed=0)
        return {"pair": f"{b}-{a}", **{k: (_r(v) if isinstance(v, float) else v) for k, v in res.items()}}

    primary = [compare("A", "B"), compare("B", "C")]
    adjusted = holm_adjust([p["p_two_sided"] for p in primary])
    for p, h in zip(primary, adjusted, strict=True):
        p["holm_p"] = _r(h)
        p["meets_min_effect"] = p["mean_diff"] >= MIN_EFFECT_DAILY
        p["verdict"] = "PROVEN" if (h < 0.05 and p["meets_min_effect"]) else "NOT_PROVEN"
        if args.strict:
            b_name, a_name = p["pair"].split("-")
            diff = [rets[b_name][i] - rets[a_name][i] for i in range(min(len(rets[a_name]), len(rets[b_name])))]
            mean = sum(diff) / len(diff)
            var = sum((x - mean) ** 2 for x in diff)
            p["lag1_autocorr"] = _r(sum((diff[i] - mean) * (diff[i - 1] - mean) for i in range(1, len(diff))) / var if var else 0.0)
            p["p_two_sided_by_block"] = {
                str(bs): _r(paired_block_bootstrap(rets[a_name], rets[b_name], block_size=bs, n_boot=2000, seed=0)["p_two_sided"])
                for bs in (5, 10, 20)
            }
            p["ci_lower_meets_min_effect"] = p["ci_low"] >= MIN_EFFECT_DAILY
            p["verdict"] = "PROVEN" if (h < 0.05 and p["meets_min_effect"] and p["ci_lower_meets_min_effect"]) else "NOT_PROVEN"
    descriptive = [
        compare("BENCH_EXPOSURE_B", "B"), compare("BENCH_EXPOSURE_C", "C"), compare("BENCH_EXPOSURE_A", "A"),
        compare("N1_random_timing_B", "B"), compare("N1_random_timing_C", "C"),
        compare("N2_reversed_B", "B"), compare("N3_shuffled_B_drift", "B"),
    ]
    beat_random = {
        name: sum(1 for f in seed_sweep[f"N1_random_timing_{name}"] if curves[name][-1] > f) for name in ("B", "C")
    }

    # Search diagnostics: the 17 configs, each as an equal-weight satellite portfolio of held returns.
    grid = search_grid()
    matrix = []
    for config in grid:
        per_symbol = [held_returns(signal_path(config.fresh, [b.close for b in bars[s]]), [b.close for b in bars[s]], args.slippage) for s in SATELLITES]
        matrix.append([sum(col) / len(col) for col in zip(*per_symbol, strict=True)])
    pbo = probability_of_backtest_overfitting(matrix, n_splits=10)
    dsr_c = deflated_sharpe_ratio(rets["C"], n_trials=len(grid))

    invariants = {
        name: portfolio_invariants([core, *arms[name].values()], max_gross=risk.max_total_exposure, max_daily_loss=risk.max_daily_loss)
        for name in arms
    }
    fills = {name: len(core.fills) + sum(len(b.fills) for b in books.values()) for name, books in arms.items()}
    refusals = {name: len(core.refusals) + sum(len(b.refusals) for b in books.values()) for name, books in arms.items()}

    refusal_reasons = {
        name: dict(Counter(r["reason"][:60] for b in [core, *books.values()] for r in b.refusals).most_common(5))
        for name, books in arms.items()
    }

    def summary(curve: list[float]) -> dict:
        peak, mdd = curve[0], 0.0
        for v in curve:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / peak)
        ret = curve[-1] / curve[0] - 1
        return {"final": _r(curve[-1], 2), "return_pct": _r(ret * 100, 3), "max_drawdown_pct": _r(mdd * 100, 3),
                "return_over_dd": _r(ret / mdd, 3) if mdd else None}

    rows = {k: summary(v) for k, v in {**curves, **benchmarks, **controls}.items()}
    result = {
        "spec_sha256": sha256_file(args.spec),
        "capital": CAPITAL,
        "costs": {"slippage_pct": args.slippage, "commission_pct": 0.0},
        "data": data,
        "rows": rows,
        "fills": fills,
        "guardian_refusals": refusals,
        "guardian_refusal_reasons": refusal_reasons,
        "exposure": exposure,
        "primary": primary,
        "descriptive": descriptive,
        "random_timing_seeds_beaten_of_10": beat_random,
        "seed_sweep_final_equity": {k: [_r(v, 2) for v in vs] for k, vs in seed_sweep.items()},
        "search": {"n_configs": len(grid), "pbo": pbo, "dsr_C": {k: (_r(v) if isinstance(v, float) else v) for k, v in dsr_c.items()}},
        "invariants": invariants,
        "D": "NOT_RUN (account holder's decision 2026-11-07)",
        **({"universe": args.universe_label or "unlabelled", "satellites": list(SATELLITES), "bar_cache": args.cache_name} if args.strict else {}),
        "conclusion": "PROVEN" if any(p["verdict"] == "PROVEN" for p in primary) else "NOT_PROVEN",
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    (out / "result.json").write_text(text, encoding="utf-8")
    (out / "walk_forward_choices.json").write_text(json.dumps(wf_choices, indent=2) + "\n", encoding="utf-8")
    with (out / "portfolio_comparison.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["row", "final", "return_pct", "max_drawdown_pct", "return_over_dd"])
        for k, v in rows.items():
            writer.writerow([k, v["final"], v["return_pct"], v["max_drawdown_pct"], v["return_over_dd"]])
    manifest = {
        "code_commit": _commit(),
        "spec": str(Path(args.spec).relative_to(ROOT)) if Path(args.spec).is_relative_to(ROOT) else args.spec,
        "spec_sha256": result["spec_sha256"],
        "result_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "data": data,
        "argv": argv if argv is not None else sys.argv[1:],
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# G4 v3 statistical report", "", f"Conclusion: **{result['conclusion']}** (D not run).", "",
             "| row | return % | max DD % | return/DD |", "|---|---:|---:|---:|"]
    for k, v in rows.items():
        lines.append(f"| {k} | {v['return_pct']:+.2f} | {v['max_drawdown_pct']:.2f} | {v['return_over_dd']} |")
    lines += ["", "## Primary family (Holm)", "", "| pair | mean daily diff | 95% CI | p two-sided | Holm p | >= min effect | verdict |",
              "|---|---:|---|---:|---:|---|---|"]
    for p in primary:
        lines.append(f"| {p['pair']} | {p['mean_diff']:+.6f} | [{p['ci_low']:+.6f}, {p['ci_high']:+.6f}] | {p['p_two_sided']:.3f} | {p['holm_p']:.3f} | {p['meets_min_effect']} | {p['verdict']} |")
    lines += ["", "## Descriptive (not adjusted)", "", "| pair | mean daily diff | 95% CI | p two-sided |", "|---|---:|---|---:|"]
    for p in descriptive:
        lines.append(f"| {p['pair']} | {p['mean_diff']:+.6f} | [{p['ci_low']:+.6f}, {p['ci_high']:+.6f}] | {p['p_two_sided']:.3f} |")
    lines += ["", f"Random-timing seeds beaten (of 10): {beat_random}", f"PBO over {len(grid)} configs: {pbo}",
              f"DSR of C (n_trials {len(grid)}): {result['search']['dsr_C']}", "", f"Invariants: {json.dumps(invariants)}", ""]
    (out / "statistical_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
