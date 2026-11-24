"""Run the registered v5 cross-sectional family on one phase (research only; never calls a broker).

    python tools/run_xsection_v5.py --phase dev|holdout --spec docs/research/specs/v5-xsection-top500.spec

`dev` reads bars before 2025-11-01 only and is reported, not used to choose anything. `holdout`
reads through 2026-11-20 and may be run once: it refuses when the spec is not byte-identical to
its registration, or when the holdout result already exists. Writes
`docs/evidence/xsection_v5/<phase>.{json,md}`.
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
sys.path.insert(0, str(ROOT / "tools"))

from autopoiesis.research import prereg, xsection

UNIVERSE = ROOT / "runtime" / "autopoiesis" / "replay" / "universe-1Day-adjall"
SET_SHA256 = "ac0531645963ae972dd3d4c0725ae92a5993aafe7b65f5538bf2ab6199e44351"
#: phase -> (bars read before, first session, last session), as the spec registers them.
PHASES = {
    "dev": (date(2025, 11, 1), date(2021, 8, 2), date(2025, 10, 31)),
    "holdout": (date(2026, 11, 21), date(2025, 11, 1), date(2026, 11, 20)),
}


def window(calendar: list[date], first: date, last: date) -> tuple[int, int]:
    """[start, end) indexes of the sessions in first..last."""
    idx = [i for i, d in enumerate(calendar) if first <= d <= last]
    if not idx:
        raise SystemExit(f"no session in {first}..{last}")
    return idx[0], idx[-1] + 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--phase", choices=sorted(PHASES), required=True)
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--registry", type=Path, default=None, help="default: $AUTOPOIESIS_PREREGISTRY or runtime/autopoiesis/preregistered")
    p.add_argument("--universe", type=Path, default=UNIVERSE)
    p.add_argument("--out", type=Path, default=ROOT / "docs" / "evidence" / "xsection_v5")
    p.add_argument("--workers", type=int, default=16)
    args = p.parse_args(argv)

    ok, problems = prereg.verify(args.spec, args.registry)
    if not ok:
        raise SystemExit("the spec does not match its registration:\n  " + "\n  ".join(problems))
    stem = args.out / args.phase
    if args.phase == "holdout" and (stem.with_suffix(".json").exists() or stem.with_suffix(".md").exists()):
        raise SystemExit(f"{stem}.json exists: the holdout has been opened; it is not re-tested")
    manifest = json.loads((args.universe / "manifest.json").read_text())
    if manifest["set_sha256"] != SET_SHA256:
        raise SystemExit(f"universe set_sha256 {manifest['set_sha256'][:16]} is not the registered {SET_SHA256[:16]}")

    from run_gate_calibration import load_universe_panel

    cutoff, first, last = PHASES[args.phase]
    t0 = time.monotonic()
    panel, facts = load_universe_panel(args.universe, cutoff, top_n=500, grid_step=5, workers=args.workers)
    start, end = window(panel["calendar"], first, last)
    result = xsection.run_family(panel, start, end)
    elapsed = time.monotonic() - t0
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "tools"],
                                capture_output=True, text=True).stdout.strip())
    record = {"label": "REAL returns, registered candidates; no synthetic data",
              "phase": args.phase, "spec": str(args.spec.relative_to(ROOT) if args.spec.is_absolute() else args.spec),
              "spec_sha256": prereg.spec_hash(args.spec), "commit": commit, "src_dirty": dirty,
              "sessions": [panel["calendar"][start].isoformat(), panel["calendar"][end - 1].isoformat(), end - start],
              "universe": {k: v for k, v in facts.items() if k != "seconds"},
              "seconds": {**facts["seconds"], "total": round(elapsed, 1)}, "candidates": result}
    args.out.mkdir(parents=True, exist_ok=True)
    mode = "x" if args.phase == "holdout" else "w"
    with open(stem.with_suffix(".json"), mode, encoding="utf-8") as fh:
        fh.write(json.dumps(record, indent=1, sort_keys=True) + "\n")
    lines = [f"# v5 cross-section, {args.phase} (real returns; spec `{record['spec_sha256'][:16]}`)", "",
             f"commit `{commit}`{' (src dirty)' if dirty else ''}, {elapsed:.0f} s, sessions "
             f"{record['sessions'][0]} .. {record['sessions'][1]} ({record['sessions'][2]}), universe `{SET_SHA256[:16]}`", "",
             "| candidate | excess/yr | CI low/yr | p | Holm p | PROVEN | rebalances | mean turnover |",
             "|---|---:|---:|---:|---:|---|---:|---:|"]
    for name, r in result.items():
        lines.append(f"| {name} | {r['annual_excess']:+.2%} | {r['annual_ci_low']:+.2%} | {r['p_two_sided']:.4f} | "
                     f"{r['holm_p']:.4f} | {r['proven']} | {r['rebalances']} | {r['mean_turnover']:.1%} |")
    with open(stem.with_suffix(".md"), mode, encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
