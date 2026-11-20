"""Run the Stage 1 gate calibration (research only; synthetic series built from cached real bars).

    python tools/run_gate_calibration.py --cache-dir runtime/autopoiesis/replay --out docs/evidence/gate_calibration

Never calls a broker. Writes <out>/<date>.json (summary, per-run rows, data fingerprints,
commit, seeds, runtime) and <out>/<date>.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.replay import load_bars
from autopoiesis.research import gate_calibration as gc
from autopoiesis.research.manifest import dataset_fingerprint

CACHE_NAME = "{symbol}_1Day_adjall_2020-07-27_2026-11-06.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--null-runs", type=int, default=200)
    p.add_argument("--signal-runs", type=int, default=100)
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--only", default="", help="comma-separated scenario subset")
    p.add_argument("--suffix", default="", help="appended to the output file stem, so a rerun does not overwrite")
    p.add_argument("--breadth", default="", help="comma-separated symbol counts; runs the breadth study instead")
    p.add_argument("--panel", default="", help="pickled pandas panel with (symbol, 'close') columns, for --breadth")
    p.add_argument("--breadth-runs", type=int, default=100)
    args = p.parse_args(argv)
    if args.breadth:
        return _breadth(args)

    returns, data = {}, {}
    dates = None
    for symbol in gc.SATELLITES:
        path = Path(args.cache_dir) / CACHE_NAME.format(symbol=symbol)
        bars = load_bars(path)
        d = [b.timestamp.date() for b in bars]
        if dates is not None and d != dates:
            raise SystemExit(f"{symbol}: bar dates differ from the other satellites")
        dates = d
        returns[symbol] = gc.returns_of([b.close for b in bars])
        data[symbol] = {"bars": len(bars), "first": d[0].isoformat(), "last": d[-1].isoformat(),
                        "fingerprint": dataset_fingerprint(symbol, bars)}

    chosen = [s for s in (args.only.split(",") if args.only else gc.SCENARIOS) if s]
    runs = {s: (args.null_runs if s.startswith("null") else args.signal_runs) for s in chosen}
    t0 = time.monotonic()
    result = gc.run(returns, runs, workers=args.workers)
    elapsed = time.monotonic() - t0
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "tools"],
                                capture_output=True, text=True).stdout.strip())
    record = {"label": "SYNTHETIC series from real bars; evidence about the gate, not about markets",
              "commit": commit, "src_dirty": dirty, "rule": "G4 v4 strict", "data": data,
              "runs": runs, "base_seed": 20261120, "elapsed_seconds": round(elapsed, 1), **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = date.today().isoformat()
    (out / f"{stem}{args.suffix}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    lines = [f"# Gate calibration {stem} (synthetic; see module docstring)", "",
             f"commit `{commit}`{' (src dirty)' if dirty else ''}, {elapsed:.0f} s", "",
             "| scenario | runs | B-A PROVEN | C-B PROVEN | any in family | B vs exposure-matched B&H | mean B excess/yr |",
             "|---|---:|---|---|---|---|---:|"]
    for s, e in result["summary"].items():
        def cell(k: str, e: dict = e) -> str:
            lo, hi = e[k]["wilson_95"]
            return f"{e[k]['proven']} ({e[k]['rate']:.1%}, [{lo:.1%}, {hi:.1%}])"
        lines.append(f"| {s} | {e['runs']} | {cell('B-A')} | {cell('C-B')} | {cell('any')} | "
                     f"{cell('B-BENCH_EXPOSURE_B')} | {e['mean_b_annual_excess']:+.3%} |")
    variants = sorted({k.split(":")[0] for e in result["summary"].values() for k in e if ":" in k})
    if variants:
        lines += ["", "Candidate replacements (not registered): PROVEN rate, any in family / B alone", "",
                  "| scenario | " + " | ".join(variants) + " |", "|---|" + "---|" * len(variants)]
        for s, e in result["summary"].items():
            lines.append(f"| {s} | " + " | ".join(
                f"{e[v + ':any']['rate']:.1%} / {e[v + ':B']['rate']:.1%}" for v in variants) + " |")
    (out / f"{stem}{args.suffix}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def _breadth(args: argparse.Namespace) -> int:
    """Power of the x2 rule as the number of symbols grows (synthetic worlds from a panel)."""
    import hashlib

    import pandas as pd  # research extra (pyproject [project.optional-dependencies])

    frame = pd.read_pickle(args.panel)
    closes = frame.xs("close", axis=1, level=1)
    complete = closes.dropna(axis=1, how="any")
    panel = {str(sym): gc.returns_of([float(x) for x in complete[sym].tolist()]) for sym in complete.columns}
    digest = hashlib.sha256(Path(args.panel).read_bytes()).hexdigest()
    n_values = [int(x) for x in args.breadth.split(",")]
    scenarios = {"null": 0.0, "signal_2pct": 0.02, "signal_5pct": 0.05, "signal_10pct": 0.10}
    t0 = time.monotonic()
    result = gc.breadth_power(panel, n_values, scenarios, args.breadth_runs, workers=args.workers)
    elapsed = time.monotonic() - t0
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "tools"],
                                capture_output=True, text=True).stdout.strip())
    record = {"label": "SYNTHETIC worlds permuted from a panel of unknown provenance; evidence about the gate only",
              "commit": commit, "src_dirty": dirty, "rule": "x2: B vs exposure-matched B&H, CI_low > 0, mean >= 1%/yr",
              "panel": {"path": args.panel, "sha256": digest, "symbols_complete": len(panel),
                        "symbols_total": int(closes.shape[1]), "days": int(closes.shape[0]),
                        "first": str(closes.index[0].date()), "last": str(closes.index[-1].date())},
              "n_values": n_values, "runs": args.breadth_runs, "elapsed_seconds": round(elapsed, 1), **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = date.today().isoformat() + (args.suffix or "-breadth")
    (out / f"{stem}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    lines = [f"# Gate power by breadth {stem} (synthetic; see gate_calibration.breadth_run)", "",
             f"commit `{commit}`{' (src dirty)' if dirty else ''}, {elapsed:.0f} s, panel sha256 `{digest[:16]}`, "
             f"{len(panel)} symbols with complete history", "",
             "| N | " + " | ".join(scenarios) + " |", "|---:|" + "---|" * len(scenarios)]
    for n in n_values:
        cells = []
        for name in scenarios:
            e = result["summary"][f"{name}@N={n}"]
            lo, hi = e["wilson_95"]
            cells.append(f"{e['rate']:.0%} [{lo:.0%}, {hi:.0%}], excess {e['mean_annual_excess']:+.2%}/yr")
        lines.append(f"| {n} | " + " | ".join(cells) + " |")
    (out / f"{stem}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
