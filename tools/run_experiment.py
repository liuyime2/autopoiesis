"""Run the four-arm replay on real bars and write a reproducible manifest.

This replaces the throw-away scripts the first smoke run used. The output
directory contains the manifest, the arm summaries, the full equity curves and a
human-readable summary. D is recorded as NOT_RUN: no LLM weekly engine exists,
and reusing C's rule would be a false claim, not an arm.

Usage:
    python tools/run_experiment.py --out reports/20261106-corrected
    python tools/run_experiment.py --offline --out reports/20261106-corrected
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
    CostModel,
    ExperimentReport,
    RiskLimits,
    run_experiment,
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
    canonical = {
        "symbol": symbol.upper(),
        "bars": [bar.to_dict() for bar in bars],
    }
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
        "return_pct": round(
            (outcome.final_equity / starting_equity - 1.0) * 100.0, 4
        ),
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
    parser.add_argument("--spec", default=str(ROOT / "configs" / "experiment_4arm.spec"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--commission", type=float, default=0.005)
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
    risk = _risk_from_config(config)
    costs = CostModel(slippage_pct=args.slippage, commission_pct=args.commission)
    report = run_experiment(
        bars=bars,
        seeds=(0,),
        starting_equity=100_000.0,
        symbol=symbol,
        risk=risk,
        costs=costs,
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
        "arms": {
            "A": "hold",
            "B": "fixed-sma20",
            "C": "search-sma20-50",
            "D": "NOT_RUN: no LLM weekly engine",
        },
    }
    payload = _report_payload(report, 100_000.0)

    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
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
    for note in report.notes:
        lines.append(f"note: {note}")
    (out_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
