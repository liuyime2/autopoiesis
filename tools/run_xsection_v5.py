"""Run the registered v5 cross-sectional family on one phase (research only; never calls a broker).

    python tools/run_xsection_v5.py --phase dev|holdout --spec docs/research/specs/v5-xsection-top500.spec

`dev` reads bars before 2025-11-01 only and is reported, not used to choose anything. `holdout`
reads through 2026-11-20 and may be run once: it refuses when the spec is not byte-identical to
its registration, or when the holdout result already exists. Writes
`docs/evidence/xsection_v5/<phase>.{json,md}`. `power` reads what `dev` reads and measures what
information ratio a holdout could confirm, from the candidates' own development noise.
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
    # Not registered: the power of a holdout, from the candidates' development noise (no holdout read).
    "power": (date(2025, 11, 1), date(2021, 8, 2), date(2025, 10, 31)),
}
POWER_LENGTHS = (252, 504)
POWER_IRS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)


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
    p.add_argument("--runs", type=int, default=500, help="for --phase power")
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
    if args.phase == "power":
        return _power(args, panel, facts, start, end, t0)
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


def _power(args: argparse.Namespace, panel: dict, facts: dict, start: int, end: int, t0: float) -> int:
    from autopoiesis.research.gate_calibration import selection_series

    diffs = {}
    for name in xsection.CANDIDATES:
        bench, strat, _ = selection_series(panel, start, end, xsection.EVERY, xsection.selector(name, panel))
        diffs[name] = [y - x for x, y in zip(bench, strat, strict=True)]
    result = xsection.residual_power(diffs, POWER_LENGTHS, POWER_IRS, args.runs, workers=args.workers)
    elapsed = time.monotonic() - t0
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "tools"],
                                capture_output=True, text=True).stdout.strip())
    record = {"label": "SYNTHETIC injection (IR x TE) on the REAL, demeaned development differences; no holdout read",
              "phase": "power", "spec_sha256": prereg.spec_hash(args.spec), "commit": commit, "src_dirty": dirty,
              "sessions": [panel["calendar"][start].isoformat(), panel["calendar"][end - 1].isoformat(), end - start],
              "lengths": list(POWER_LENGTHS), "irs": list(POWER_IRS), "runs": args.runs,
              "universe": {k: v for k, v in facts.items() if k != "seconds"},
              "seconds": {**facts["seconds"], "total": round(elapsed, 1)}, **result}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "power.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    te = result["tracking_error"]
    names = list(xsection.CANDIDATES)
    lines = ["# v5 candidates: power of a holdout on their own noise (synthetic injection, real residuals)", "",
             f"commit `{commit}`{' (src dirty)' if dirty else ''}, {elapsed:.0f} s, development sessions "
             f"{record['sessions'][0]} .. {record['sessions'][1]} ({record['sessions'][2]}), {args.runs} runs a cell; "
             "tracking error " + ", ".join(f"{c} {te[c]:.2%}" for c in names), "",
             "PROVEN rate of the injected candidate (Holm over all three). `single`: only that candidate has the edge; "
             "`all`: every candidate has it.", "",
             "| L, IR | " + " | ".join(f"single {c}" for c in names) + " | all: any |",
             "|---|" + "---:|" * (len(names) + 1)]
    for n in POWER_LENGTHS:
        for ir in POWER_IRS:
            cells = []
            for c in names:
                e = result["summary"][f"single:{c}@L={n}@IR={ir:g}"][c]
                cells.append(f"{e['rate']:.1%} [{e['wilson_95'][0]:.1%}, {e['wilson_95'][1]:.1%}]")
            a = result["summary"][f"all@L={n}@IR={ir:g}"]["any"]
            cells.append(f"{a['rate']:.1%} [{a['wilson_95'][0]:.1%}, {a['wilson_95'][1]:.1%}]")
            lines.append(f"| {n}, {ir:g} | " + " | ".join(cells) + " |")
    (args.out / "power.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
