"""Run the four-arm replay on real bars and write a reproducible manifest.

This replaces the throw-away scripts the first smoke run used. The output
directory contains the manifest, the arm summaries, the full equity curves, a
statistical report and an acceptance file. D is recorded as NOT_RUN: no LLM
weekly engine exists, and reusing C's rule would be a false claim, not an arm.

Usage:
    python tools/run_experiment.py --out reports/20261106/v2
    python tools/run_experiment.py --offline --out reports/20261106/v2
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.config import AgentConfig
from autopoiesis.replay import ReplayBar, fetch_bars, load_bars, save_bars
from autopoiesis.research.experiment import (
    ArmOutcome,
    BuyAndHoldEngine,
    CostModel,
    ExperimentReport,
    RandomEngine,
    ReversedSmaEngine,
    RiskLimits,
    ShuffledSmaEngine,
    SleeveEngine,
    SmaEngine,
    run_experiment,
)
from autopoiesis.research.statistics import (
    daily_returns,
    deflated_sharpe_ratio,
    holm_adjust,
    probability_of_backtest_overfitting,
)


def _load_env() -> None:
    cfg_path = os.environ.get("AUTOPOIESIS_ENV_FILE")
    if not cfg_path:
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        cfg_path = os.path.join(xdg, "autopoiesis", "env")
    if cfg_path and Path(cfg_path).exists():
        for line in Path(cfg_path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _dataset_fingerprint(symbol: str, bars: list[ReplayBar]) -> str:
    canonical = {"symbol": symbol.upper(), "bars": [bar.to_dict() for bar in bars]}
    return _sha256_bytes(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _code_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _risk_from_config(config: AgentConfig) -> RiskLimits:
    return RiskLimits(
        allowlist=frozenset(s.upper() for s in config.allowlist),
        max_position_value=config.max_position_value,
        max_daily_loss=config.max_daily_loss,
        max_total_exposure=config.max_total_exposure,
        max_trades_per_day=config.max_trades_per_day,
    )


def _outcome_summary(outcome: ArmOutcome, starting_equity: float) -> dict:
    return {
        "arm": outcome.arm,
        "seed": outcome.seed,
        "bars": outcome.bars,
        "final_equity": round(outcome.final_equity, 2),
        "return_pct": round((outcome.final_equity / starting_equity - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(outcome.max_drawdown * 100.0, 4),
        "fills": len(outcome.fills),
        "refusals": len(outcome.refusals),
        "min_cash": round(outcome.min_cash, 2),
        "max_gross_exposure": round(outcome.max_gross_exposure, 2),
    }


def _report_payload(report: ExperimentReport, starting_equity: float) -> dict:
    return {
        "arms": {
            arm: [_outcome_summary(outcome, starting_equity) for outcome in outcomes]
            for arm, outcomes in report.arms.items()
        },
        "benchmark": (
            _outcome_summary(report.benchmark, starting_equity)
            if report.benchmark is not None
            else None
        ),
        "controls": {
            name: _outcome_summary(outcome, starting_equity)
            for name, outcome in report.controls.items()
        },
        "comparisons": report.comparisons,
        "conclusion": report.conclusion,
        "notes": report.notes,
        "equity_curves": {
            arm: [outcome.equity_curve for outcome in outcomes]
            for arm, outcomes in report.arms.items()
        },
        "fills": {
            arm: [outcome.fills for outcome in outcomes]
            for arm, outcomes in report.arms.items()
        },
        "refusals": {
            arm: [outcome.refusals for outcome in outcomes]
            for arm, outcomes in report.arms.items()
        },
    }


def _statistics(
    report: ExperimentReport, *, n_trials: int
) -> dict:
    comparisons = []
    raw_p = [
        row["p_two_sided"]
        for row in report.comparisons
        if row.get("p_two_sided") is not None
    ]
    adjusted = holm_adjust(raw_p)
    cursor = 0
    for row in report.comparisons:
        entry = dict(row)
        if row.get("p_two_sided") is not None:
            entry["p_holm"] = adjusted[cursor]
            cursor += 1
        else:
            entry["p_holm"] = None
        comparisons.append(entry)

    dsr: dict[str, dict] = {}
    for arm, outcomes in report.arms.items():
        returns = daily_returns(outcomes[0].equity_curve)
        dsr[arm] = deflated_sharpe_ratio(returns, n_trials=n_trials)

    return_series = [
        daily_returns(outcomes[0].equity_curve) for outcomes in report.arms.values()
    ]
    pbo = probability_of_backtest_overfitting(return_series, n_splits=10)
    return {"comparisons": comparisons, "dsr": dsr, "pbo": pbo}


def _statistical_markdown(report: ExperimentReport, stats: dict, manifest: dict) -> str:
    lines = [
        "# Statistical report",
        "",
        f"Spec SHA-256: `{manifest['spec_sha256']}`",
        f"Data SHA-256: `{manifest['data']['sha256']}`",
        f"Code commit: `{manifest['code_commit']}`",
        f"Bars: {manifest['data']['bars']} {manifest['data']['symbol']} "
        f"{manifest['data']['interval']} {manifest['data']['start']}..{manifest['data']['end']}",
        f"Costs: commission {manifest['costs']['commission_pct']:.4%}, "
        f"slippage {manifest['costs']['slippage_pct']:.4%}",
        f"Minimum effect for PROVEN: {manifest.get('min_effect', 0.0):.8f} per bar",
        "",
        "## Arms",
        "",
        "| arm | final equity | return | max drawdown | fills | min cash | max gross |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in sorted(manifest["arms"]):
        for row in manifest["arms"][arm]:
            lines.append(
                f"| {row['arm']} | {row['final_equity']:.2f} | {row['return_pct']:.2f}% | "
                f"{row['max_drawdown_pct']:.2f}% | {row['fills']} | {row['min_cash']:.2f} | "
                f"{row['max_gross_exposure']:.2f} |"
            )
    if manifest.get("benchmark"):
        row = manifest["benchmark"]
        lines.append(
            f"| {row['arm']} | {row['final_equity']:.2f} | {row['return_pct']:.2f}% | "
            f"{row['max_drawdown_pct']:.2f}% | {row['fills']} | {row['min_cash']:.2f} | "
            f"{row['max_gross_exposure']:.2f} |"
        )
    lines += ["", "## Comparisons", ""]
    for row in stats["comparisons"]:
        lines.append(
            f"- {row['left']} - {row['right']}: mean daily diff {row['mean_diff']:.8f}, "
            f"95% CI [{row['ci_low']:.8f}, {row['ci_high']:.8f}], "
            f"bootstrap p(one-sided)={row.get('p_value')}, "
            f"p(two-sided)={row.get('p_two_sided')}, Holm p={row.get('p_holm')}"
        )
    lines += ["", "## Overfitting diagnostics", ""]
    for arm, value in stats["dsr"].items():
        lines.append(
            f"- {arm}: Sharpe {value['sharpe']:.4f} per bar, "
            f"expected max under null {value['expected_max_sharpe']:.4f}, DSR {value['dsr']:.4f}"
        )
    lines.append(f"- PBO (CSCV, 10 splits): {stats['pbo']['pbo']}")
    lines += ["", "## Verdict", "", f"`{report.conclusion}`", ""]
    for note in report.notes:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _acceptance(report: ExperimentReport, risk: RiskLimits) -> dict:
    all_outcomes = [outcome for outcomes in report.arms.values() for outcome in outcomes]
    if report.benchmark is not None:
        all_outcomes.append(report.benchmark)
    all_outcomes.extend(report.controls.values())
    min_cash = min((outcome.min_cash for outcome in all_outcomes), default=0.0)
    max_gross = max((outcome.max_gross_exposure for outcome in all_outcomes), default=0.0)
    checks = {
        "min_cash_nonnegative": min_cash >= 0,
        "gross_exposure_within_total_limit": max_gross <= risk.max_total_exposure,
        "manifest_present": True,
        "costs_declared": True,
        "next_open_fills": True,
    }
    return {
        "gate": "G4",
        "status": "INSUFFICIENT" if all(checks.values()) else "FAIL",
        "conclusion": report.conclusion,
        "primary_comparison": {
            "left": "D",
            "right": "C",
            "evaluated": any(
                row["left"] == "D" and row["right"] == "C" for row in report.comparisons
            ),
            "reason": "D NOT_RUN: no LLM weekly engine",
        },
        "arms_run": sorted(report.arms),
        "arms_missing": ["D"],
        "benchmark": "buy_and_hold_spy_at_max_position_value",
        "controls": sorted(report.controls),
        "checks": checks,
        "measured": {
            "min_cash": round(min_cash, 2),
            "max_gross_exposure": round(max_gross, 2),
            "max_total_exposure": risk.max_total_exposure,
        },
        "notes": report.notes,
    }


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="2020-07-27")
    parser.add_argument("--end", default="2026-11-05")
    parser.add_argument("--interval", default="1Day")
    parser.add_argument("--feed", default="iex")
    parser.add_argument(
        "--cache",
        default=str(ROOT / "runtime" / "autopoiesis" / "replay" / "SPY_1Day_2020-07-27_2026-11-05.json"),
    )
    parser.add_argument("--offline", action="store_true", help="require the cache; never fetch")
    parser.add_argument("--spec", default=str(ROOT / "configs" / "experiment_4arm_v2.spec"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--commission", type=float, default=0.005)
    parser.add_argument(
        "--min-effect",
        type=float,
        default=0.0,
        help="minimum mean daily net-return difference required before PROVEN",
    )
    args = parser.parse_args(argv)

    symbol = args.symbol.upper()
    cache_path = Path(args.cache)
    bars: list[ReplayBar]
    if cache_path.exists():
        bars = load_bars(cache_path)
        data_source = f"cache:{cache_path}"
    elif args.offline:
        raise SystemExit(f"offline requested but cache is absent: {cache_path}")
    else:
        config = AgentConfig.from_env()
        from autopoiesis.broker_client import make_client

        client = make_client(config)
        bars = fetch_bars(
            symbol,
            args.start,
            args.end,
            client=client,
            interval=args.interval,
            feed=args.feed,
        )
        save_bars(cache_path, symbol, bars)
        data_source = f"alpaca:{args.feed}"

    if not bars:
        raise SystemExit("no bars: refusing to run an empty replay")

    config = AgentConfig.from_env()
    if config.sleeve_symbol.upper() != symbol:
        raise SystemExit(
            f"arm A needs the configured sleeve symbol ({config.sleeve_symbol!r}) to match "
            f"the replay symbol ({symbol!r})"
        )
    if config.sleeve_pool <= 0:
        raise SystemExit("arm A needs a configured sleeve pool; it is zero, so the core sleeve is off")
    risk = _risk_from_config(config)
    costs = CostModel(slippage_pct=args.slippage, commission_pct=args.commission)

    engines = {
        "A": lambda: SleeveEngine(
            pool=config.sleeve_pool,
            target_vol_pct=config.sleeve_target_vol_pct,
            symbol=symbol,
        ),
        "B": lambda: SmaEngine(fast=20, band=0.01, strategy_id="fixed-sma20"),
        "C": lambda: SmaEngine(fast=20, slow=50, strategy_id="search-sma20-50"),
    }
    def benchmark() -> BuyAndHoldEngine:
        return BuyAndHoldEngine(max_position_value=risk.max_position_value)

    controls = {
        "N1_random": lambda: RandomEngine(seed=0, quantity=1, every=20),
        "N2_reversed": lambda: ReversedSmaEngine(
            inner=SmaEngine(fast=20, band=0.01, strategy_id="reversed-control")
        ),
        "N3_shuffled": lambda: ShuffledSmaEngine(fast=20, band=0.01, seed=0),
    }

    report = run_experiment(
        bars=bars,
        engines=engines,
        seeds=(0,),
        starting_equity=100_000.0,
        symbol=symbol,
        risk=risk,
        costs=costs,
        benchmark=benchmark,
        controls=controls,
        min_effect=args.min_effect,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = Path(args.spec)
    spec_digest = (
        _sha256_bytes(spec_path.read_bytes()) if spec_path.exists() else "missing"
    )
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_commit": _code_commit(),
        "spec": str(spec_path),
        "spec_sha256": spec_digest,
        "data": {
            "symbol": symbol,
            "start": args.start,
            "end": args.end,
            "interval": args.interval,
            "feed": args.feed,
            "bars": len(bars),
            "sha256": _dataset_fingerprint(symbol, bars),
            "source": data_source,
        },
        "seeds": [0],
        "starting_equity": 100_000.0,
        "risk": {
            "allowlist": sorted(risk.allowlist),
            "max_position_value": risk.max_position_value,
            "max_daily_loss": risk.max_daily_loss,
            "max_total_exposure": risk.max_total_exposure,
            "max_trades_per_day": risk.max_trades_per_day,
        },
        "costs": asdict(costs),
        "min_effect": args.min_effect,
        "arms": {
            "A": "core_sleeve_vol_target_pool",
            "B": "fixed_sma20_band_1pct",
            "C": "sma20_50",
            "D": "NOT_RUN: no LLM weekly engine",
        },
        "benchmark": "buy_and_hold_spy_at_max_position_value",
        "controls": sorted(controls),
    }
    payload = _report_payload(report, 100_000.0)
    stats = _statistics(report, n_trials=len(engines) + len(controls))

    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "statistical_report.md").write_text(
        _statistical_markdown(report, stats, {**manifest, "arms": payload["arms"],
                                             "benchmark": payload["benchmark"]}),
        encoding="utf-8",
    )
    (out_dir / "acceptance.json").write_text(
        json.dumps(_acceptance(report, risk), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with (out_dir / "portfolio_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "arm",
                "seed",
                "bars",
                "final_equity",
                "return_pct",
                "max_drawdown_pct",
                "fills",
                "refusals",
                "min_cash",
                "max_gross_exposure",
            ],
        )
        writer.writeheader()
        for arm in payload["arms"]:
            for row in payload["arms"][arm]:
                writer.writerow(row)
        if payload["benchmark"]:
            writer.writerow(payload["benchmark"])
        for row in payload["controls"].values():
            writer.writerow(row)

    lines = [
        f"bars: {len(bars)} {symbol} {args.interval} {args.start}..{args.end} ({data_source})",
        f"conclusion: {report.conclusion}",
        "",
    ]
    for arm in sorted(payload["arms"]):
        for row in payload["arms"][arm]:
            lines.append(
                f"arm {row['arm']}: final={row['final_equity']:.2f} "
                f"return={row['return_pct']:.2f}% drawdown={row['max_drawdown_pct']:.2f}% "
                f"fills={row['fills']} refusals={row['refusals']} "
                f"min_cash={row['min_cash']:.2f} max_gross={row['max_gross_exposure']:.2f}"
            )
    if payload["benchmark"]:
        row = payload["benchmark"]
        lines.append(
            f"benchmark {row['arm']}: final={row['final_equity']:.2f} "
            f"return={row['return_pct']:.2f}% drawdown={row['max_drawdown_pct']:.2f}%"
        )
    for name, row in sorted(payload["controls"].items()):
        lines.append(
            f"control {name}: final={row['final_equity']:.2f} "
            f"return={row['return_pct']:.2f}% fills={row['fills']}"
        )
    for note in report.notes:
        lines.append(f"note: {note}")
    (out_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    for row in stats["comparisons"]:
        print(
            f"compare {row['left']}-{row['right']}: mean={row['mean_diff']:.8f} "
            f"CI=[{row['ci_low']:.8f},{row['ci_high']:.8f}] "
            f"p1={row.get('p_value')} p2={row.get('p_two_sided')} holm={row.get('p_holm')}"
        )
    print(f"PBO: {stats['pbo']['pbo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
