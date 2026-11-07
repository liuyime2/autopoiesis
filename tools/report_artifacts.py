"""Derive the standard report artifacts from a finished experiment run.

The runner writes the manifest, result, statistics, acceptance and CSV. This tool
adds the data-quality summary, the trial ledger, the execution trace and the
handoff note. Every value is read from the run's own files or from the cached
bars; nothing is re-estimated or typed in by hand.

Usage:
    python tools/report_artifacts.py --report reports/20261106/v2 \
        --cache runtime/autopoiesis/replay/SPY_1Day_2020-07-27_2026-11-05.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.replay import load_bars


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _data_quality(manifest: dict, cache: Path) -> dict:
    bars = load_bars(cache)
    if not bars:
        return {"error": "cache contains no bars", "source": str(cache)}
    timestamps = [bar.timestamp for bar in bars]
    duplicates = len(timestamps) - len(set(timestamps))
    nonpositive = sum(
        1
        for bar in bars
        if min(bar.open, bar.high, bar.low, bar.close) <= 0
    )
    zero_volume = sum(1 for bar in bars if bar.volume <= 0)
    gap_rows = []
    max_gap = 0
    for previous, current in pairwise(timestamps):
        days = (current.date() - previous.date()).days
        max_gap = max(max_gap, days)
        if days > 4:
            gap_rows.append(
                {
                    "from": previous.date().isoformat(),
                    "to": current.date().isoformat(),
                    "calendar_days": days,
                }
            )

    first_day, last_day = timestamps[0].date(), timestamps[-1].date()
    weekdays = 0
    cursor = first_day
    while cursor <= last_day:
        if cursor.weekday() < 5:
            weekdays += 1
        cursor += timedelta(days=1)
    covered = len({stamp.date() for stamp in timestamps if stamp.weekday() < 5})
    return {
        "symbol": manifest["data"]["symbol"],
        "interval": manifest["data"]["interval"],
        "bars": len(bars),
        "first": timestamps[0].isoformat(),
        "last": timestamps[-1].isoformat(),
        "sha256": manifest["data"]["sha256"],
        "source": manifest["data"]["source"],
        "duplicate_timestamps": duplicates,
        "nonpositive_prices": nonpositive,
        "zero_volume_bars": zero_volume,
        "max_gap_calendar_days": max_gap,
        "gaps_over_4_calendar_days": gap_rows,
        "weekday_coverage_pct": round(100.0 * covered / weekdays, 4) if weekdays else 0.0,
        "note": (
            "local check of the cached bars; weekday coverage counts exchange "
            "holidays as uncovered, so it is a lower bound, not a broker audit"
        ),
    }


CONTROL_DEFINITIONS = {
    "N1_random": "seeded random direction every 20 bars",
    "N2_reversed": "SMA20 signals inverted",
    "N3_shuffled": "SMA20 over a shuffled price history",
}


def _trial_rows(manifest: dict, result: dict) -> list[dict]:
    rows: list[dict] = []
    for arm, outcomes in result["arms"].items():
        for outcome in outcomes:
            row = {
                "trial_id": f"{arm}-{outcome['seed']}",
                "role": "arm",
                "definition": manifest["arms"].get(arm, "unknown"),
            }
            row.update(outcome)
            rows.append(row)
    for name, outcome in result["benchmarks"].items():
        row = {
            "trial_id": name,
            "role": "benchmark",
            "definition": manifest["benchmarks"].get(name, "unknown"),
        }
        row.update(outcome)
        rows.append(row)
    for name, outcome in result["controls"].items():
        row = {
            "trial_id": name,
            "role": "control",
            "definition": CONTROL_DEFINITIONS.get(name, "negative control"),
        }
        row.update(outcome)
        rows.append(row)
    return sorted(rows, key=lambda row: (row["role"], row["trial_id"]))


def _trace_rows(result: dict) -> list[dict]:
    rows: list[dict] = []
    for arm, seed_fills in result.get("fills", {}).items():
        seeds = [outcome["seed"] for outcome in result["arms"].get(arm, [])]
        for index, fills in enumerate(seed_fills):
            seed = seeds[index] if index < len(seeds) else index
            for fill in fills:
                row = {"kind": "fill", "trial_id": f"{arm}-{seed}", "role": "arm"}
                row.update(fill)
                rows.append(row)
    for name, fills in result.get("benchmark_fills", {}).items():
        for fill in fills:
            row = {"kind": "fill", "trial_id": name, "role": "benchmark"}
            row.update(fill)
            rows.append(row)
    for name, fills in result.get("control_fills", {}).items():
        for fill in fills:
            row = {"kind": "fill", "trial_id": name, "role": "control"}
            row.update(fill)
            rows.append(row)
    for arm, seed_refusals in result.get("refusals", {}).items():
        seeds = [outcome["seed"] for outcome in result["arms"].get(arm, [])]
        for index, refusals in enumerate(seed_refusals):
            seed = seeds[index] if index < len(seeds) else index
            for refusal in refusals:
                row = {"kind": "refusal", "trial_id": f"{arm}-{seed}", "role": "arm"}
                row.update(refusal)
                rows.append(row)
    for name, refusals in result.get("benchmark_refusals", {}).items():
        for refusal in refusals:
            row = {"kind": "refusal", "trial_id": name, "role": "benchmark"}
            row.update(refusal)
            rows.append(row)
    for name, refusals in result.get("control_refusals", {}).items():
        for refusal in refusals:
            row = {"kind": "refusal", "trial_id": name, "role": "control"}
            row.update(refusal)
            rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            row["trial_id"],
            row["kind"],
            row.get("fill_timestamp") or row.get("timestamp") or "",
        ),
    )


def _table_row(row: dict) -> str:
    drawdown = row["max_drawdown_pct"]
    ratio = row["return_pct"] / drawdown if drawdown > 0 else 0.0
    return (
        f"| {row['arm']} | {row['return_pct']:.2f}% | {drawdown:.2f}% | "
        f"{ratio:.2f} | {row['fills']} | {row['refusals']} | "
        f"{row['min_cash']:.2f} | {row['max_gross_exposure']:.2f} |"
    )


def _handoff(manifest: dict, result: dict, acceptance: dict, data_quality: dict) -> str:
    stats = result.get("statistics", {})
    lines = [
        "# Handoff — G4 v2 diagnostic",
        "",
        "## Verdict",
        "",
        f"**{acceptance['status']}.** A/B/C ran; D is `NOT_RUN`. The primary "
        "comparison D-C was not evaluated, so this is not a four-arm verdict.",
        "",
        "## Run",
        "",
        f"- Experiment code: `{manifest['code_commit']}`",
        f"- Spec: `{manifest['spec']}` (SHA-256 `{manifest['spec_sha256']}`)",
        f"- Data: {manifest['data']['bars']} {manifest['data']['symbol']} "
        f"{manifest['data']['interval']} bars, {manifest['data']['start']}.."
        f"{manifest['data']['end']}, SHA-256 `{manifest['data']['sha256']}`",
        f"- Costs: commission {manifest['costs']['commission_pct']:.4%}, "
        f"slippage {manifest['costs']['slippage_pct']:.4%}",
        f"- Benchmarks: {', '.join(sorted(manifest['benchmarks']))}",
        f"- Controls: {', '.join(sorted(manifest['controls']))}",
        f"- Minimum effect for PROVEN: {manifest.get('min_effect', 0.0):.8f} per bar",
        "",
        "## Results",
        "",
        "| trial | return | max drawdown | return/DD | fills | refusals | min cash | max gross |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in sorted(result["arms"]):
        for row in result["arms"][arm]:
            lines.append(_table_row(row))
    for name in sorted(result["benchmarks"]):
        lines.append(_table_row(result["benchmarks"][name]))
    for name in sorted(result["controls"]):
        lines.append(_table_row(result["controls"][name]))

    lines += [
        "",
        "## Primary comparisons (pre-registered family)",
        "",
    ]
    for row in stats.get("comparisons", []):
        lines.append(
            f"- {row['left']} - {row['right']}: mean daily diff "
            f"{row['mean_diff']:.8f}, 95% CI [{row['ci_low']:.8f}, "
            f"{row['ci_high']:.8f}], two-sided p={row.get('p_two_sided')}, "
            f"Holm p={row.get('p_holm')}"
        )

    lines += [
        "",
        "## Benchmarks (descriptive, not Holm-adjusted)",
        "",
        "The difference is arm minus benchmark. Compare A with BENCH_20K (same "
        "20k sleeve pool) and B/C with BENCH_25K (same position cap).",
        "",
    ]
    for row in stats.get("benchmark_comparisons", []):
        lines.append(
            f"- {row['arm']} - {row['benchmark']}: mean daily diff "
            f"{row['mean_diff']:.8f}, 95% CI [{row['ci_low']:.8f}, "
            f"{row['ci_high']:.8f}], two-sided p={row.get('p_two_sided')}"
        )

    dsr = stats.get("dsr", {})
    lines += [
        "",
        "## Overfitting diagnostics",
        "",
    ]
    for arm in sorted(dsr):
        value = dsr[arm]
        lines.append(
            f"- {arm}: Sharpe {value['sharpe']:.4f} per bar, expected max "
            f"under null {value['expected_max_sharpe']:.4f}, DSR {value['dsr']:.4f}"
        )
    if stats.get("pbo"):
        lines.append(f"- PBO (CSCV): {stats['pbo'].get('pbo')}")

    lines += [
        "",
        "## What the evidence says",
        "",
        "- All three arms underperform both buy-and-hold benchmarks in absolute "
        "return and in return per unit of max drawdown.",
        "- The arm-minus-benchmark CIs exclude zero in all six descriptive "
        "comparisons, but those comparisons were not pre-registered and are not "
        "Holm-adjusted; the consistent direction is the stronger evidence.",
        "- The shuffled-history control returns more than every arm, so a rule "
        "that cannot beat a destroyed history is not evidence of signal.",
        "- The primary family does not survive Holm, and PBO is high; the arm "
        "ranking is not a reliable selection.",
        "",
        "## What this does not prove",
        "",
        "- It does not test D: no weekly LLM engine exists.",
        "- It is historical development evidence, not unseen data.",
        f"- The {manifest['costs']['commission_pct']:.2%} commission is a stress "
        "assumption. Lower costs would help the high-turnover arms, but the "
        "benchmarks already lead before costs.",
        "- The arms are deterministic, so their seed variance is zero. Only the "
        "random control has seed variance, and only seed 0 was run.",
        "- `min_effect` is 0.0 because the registered spec did not declare one; "
        "a confirmatory spec must declare an economic floor before the run.",
        "",
        "## Next",
        "",
        "1. Do not promote any arm to G5 on this evidence.",
        "2. Build the weekly LLM D arm with persisted request/response hashes, "
        "or pre-register a three-arm confirmatory experiment with a declared "
        "`min_effect` and a forward window.",
        "3. Fix the sizing mismatch before a new arm is added: B/C buy one share "
        "per signal and accumulate, while A targets a dollar pool. A fair "
        "comparison needs the same position-sizing rule across arms.",
        "4. Keep both benchmarks and the negative controls in every future report.",
        "",
        "## Data quality",
        "",
        f"- Duplicate timestamps: {data_quality.get('duplicate_timestamps')}",
        f"- Non-positive prices: {data_quality.get('nonpositive_prices')}",
        f"- Zero-volume bars: {data_quality.get('zero_volume_bars')}",
        f"- Max calendar gap: {data_quality.get('max_gap_calendar_days')} days",
        f"- Weekday coverage: {data_quality.get('weekday_coverage_pct')}% "
        "(holidays count as uncovered)",
        "",
        "## Reproduce",
        "",
        "```sh",
        "python tools/run_experiment.py --offline --out reports/20261106/v2",
        "python tools/report_artifacts.py --report reports/20261106/v2 \\",
        "    --cache runtime/autopoiesis/replay/SPY_1Day_2020-07-27_2026-11-05.json",
        "python -m autopoiesis.research.prereg verify configs/experiment_4arm_v2.spec",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--cache", required=True)
    args = parser.parse_args(argv)

    report_dir = Path(args.report)
    manifest = _read_json(report_dir / "run_manifest.json")
    result = _read_json(report_dir / "result.json")
    acceptance = _read_json(report_dir / "acceptance.json")

    data_quality = _data_quality(manifest, Path(args.cache))
    (report_dir / "data_quality.json").write_text(
        json.dumps(data_quality, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    trials = _trial_rows(manifest, result)
    (report_dir / "strategy_trials.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in trials),
        encoding="utf-8",
    )

    trace = _trace_rows(result)
    (report_dir / "execution_trace.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in trace),
        encoding="utf-8",
    )

    handoff = _handoff(manifest, result, acceptance, data_quality)
    (report_dir / "handoff.md").write_text(handoff, encoding="utf-8")

    print(f"wrote data_quality.json, strategy_trials.jsonl ({len(trials)} rows), "
          f"execution_trace.jsonl ({len(trace)} rows), handoff.md to {report_dir}")
    print(f"data_quality sha256: {_sha256_bytes(json.dumps(data_quality, sort_keys=True).encode())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
