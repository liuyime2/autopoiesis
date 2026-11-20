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
    args = p.parse_args(argv)

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


if __name__ == "__main__":
    raise SystemExit(main())
