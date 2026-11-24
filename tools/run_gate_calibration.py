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
    p.add_argument("--cache-dir", default="", help="replay cache with the satellites (the Stage 1 study)")
    p.add_argument("--out", required=True)
    p.add_argument("--null-runs", type=int, default=200)
    p.add_argument("--signal-runs", type=int, default=100)
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--only", default="", help="comma-separated scenario subset")
    p.add_argument("--suffix", default="", help="appended to the output file stem, so a rerun does not overwrite")
    p.add_argument("--breadth", default="", help="comma-separated symbol counts; runs the breadth study instead")
    p.add_argument("--panel", default="", help="pickled pandas panel with (symbol, 'close') columns, for --breadth")
    p.add_argument("--breadth-runs", type=int, default=100)
    p.add_argument("--kind", choices=("timing", "cross_section"), default="timing", help="for --breadth")
    p.add_argument("--rebalance-every", default="1", help="comma-separated closes between re-rankings (cross_section)")
    p.add_argument("--scenarios", default="null,signal_2pct,signal_5pct,signal_10pct", help="for --breadth/--universe")
    p.add_argument("--universe", default="", help="universe cache dir (tools/fetch_universe_bars.py); runs the placebo study")
    p.add_argument("--cutoff", default="2025-11-01", help="for --universe: no bar on or after this date is read")
    p.add_argument("--lengths", default="63,126,252,504", help="for --universe: window lengths in sessions")
    p.add_argument("--top-n", type=int, default=500, help="for --universe")
    p.add_argument("--grid-step", type=int, default=5, help="for --universe: sessions between membership dates")
    args = p.parse_args(argv)
    if args.breadth:
        return _breadth(args)
    if args.universe:
        return _universe(args)
    if not args.cache_dir:
        p.error("--cache-dir is required unless --breadth or --universe is given")

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
    known = {"null": 0.0, "signal_2pct": 0.02, "signal_5pct": 0.05, "signal_10pct": 0.10}
    scenarios = {name: known[name] for name in args.scenarios.split(",")}
    cadences = [int(x) for x in args.rebalance_every.split(",")]
    t0 = time.monotonic()
    result: dict = {"summary": {}, "rows": {}}
    for k in cadences:
        part = gc.breadth_power(panel, n_values, scenarios, args.breadth_runs, workers=args.workers,
                                kind=args.kind, rebalance_every=k)
        suffix = "" if cadences == [1] else f"@every={k}"
        for part_key in ("summary", "rows"):
            result[part_key].update({key + suffix: v for key, v in part[part_key].items()})
    elapsed = time.monotonic() - t0
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "tools"],
                                capture_output=True, text=True).stdout.strip())
    record = {"label": "SYNTHETIC worlds permuted from a panel of unknown provenance; evidence about the gate only",
              "commit": commit, "src_dirty": dirty, "kind": args.kind,
              "rule": ("x2 on top-half equal weight vs all-name equal weight" if args.kind == "cross_section"
                       else "x2: B vs exposure-matched B&H, CI_low > 0, mean >= 1%/yr"),
              "panel": {"path": args.panel, "sha256": digest, "symbols_complete": len(panel),
                        "symbols_total": int(closes.shape[1]), "days": int(closes.shape[0]),
                        "first": str(closes.index[0].date()), "last": str(closes.index[-1].date())},
              "n_values": n_values, "rebalance_every": cadences, "runs": args.breadth_runs, "elapsed_seconds": round(elapsed, 1), **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = date.today().isoformat() + (args.suffix or ("-xsection" if args.kind == "cross_section" else "-breadth"))
    (out / f"{stem}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    lines = [f"# Gate power by breadth {stem} (synthetic; see gate_calibration.breadth_run)", "",
             f"commit `{commit}`{' (src dirty)' if dirty else ''}, {elapsed:.0f} s, panel sha256 `{digest[:16]}`, "
             f"{len(panel)} symbols with complete history", "",
             "| N | " + " | ".join(scenarios) + " |", "|---:|" + "---|" * len(scenarios)]
    if cadences != [1]:
        lines[-2] = "| N, every | " + " | ".join(scenarios) + " |"
    for n in n_values:
        for k in cadences:
            suffix = "" if cadences == [1] else f"@every={k}"
            cells = []
            for name in scenarios:
                e = result["summary"][f"{name}@N={n}{suffix}"]
                lo, hi = e["wilson_95"]
                cells.append(f"{e['rate']:.0%} [{lo:.0%}, {hi:.0%}], excess {e['mean_annual_excess']:+.2%}/yr")
            label = f"{n}" if cadences == [1] else f"{n}, {k}"
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
    (out / f"{stem}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


_BARS: dict = {}


def _members_worker(day):
    from autopoiesis.research.universe import members_at

    return day, members_at(day, _BARS, top_n=_TOP_N[0])


_TOP_N = [500]


def _universe(args: argparse.Namespace) -> int:
    """x2 power of a placebo selection on the real survivorship-free universe, by window length."""
    import multiprocessing
    import os

    global _BARS
    cutoff = date.fromisoformat(args.cutoff)
    udir = Path(args.universe)
    manifest = json.loads((udir / "manifest.json").read_text())
    set_sha = manifest["set_sha256"]
    t0 = time.monotonic()
    _BARS = {s: [b for b in load_bars(udir / f"{s}.json") if b.timestamp.date() < cutoff] for s in manifest["symbols"]}
    _BARS = {s: b for s, b in _BARS.items() if b}
    load_s = time.monotonic() - t0
    calendar = sorted({b.timestamp.date() for bars in _BARS.values() for b in bars})
    grid = calendar[::args.grid_step]
    cache = udir.parent / f"universe-membership-{set_sha[:12]}-{args.cutoff}-top{args.top_n}-step{args.grid_step}.json"
    if cache.exists():
        members = {date.fromisoformat(d): m for d, m in json.loads(cache.read_text())["members"].items()}
    else:
        _TOP_N[0] = args.top_n
        with multiprocessing.get_context("fork").Pool(args.workers) as pool:
            members = dict(pool.map(_members_worker, grid, chunksize=1))
        cache.write_text(json.dumps({"set_sha256": set_sha, "cutoff": args.cutoff, "top_n": args.top_n,
                                     "grid_step": args.grid_step,
                                     "members": {d.isoformat(): m for d, m in sorted(members.items())}}) + "\n")
        os.chmod(cache, 0o600)
    members = {d: m for d, m in members.items() if m}
    member_s = time.monotonic() - t0 - load_s
    panel = gc.universe_panel(_BARS, members, cutoff)
    _BARS = {}
    lengths = [int(x) for x in args.lengths.split(",")]
    everys = [int(x) for x in args.rebalance_every.split(",")]
    known = {"null": 0.0, "signal_2pct": 0.02, "signal_5pct": 0.05, "signal_10pct": 0.10}
    scenarios = {name: known[name] for name in args.scenarios.split(",")}
    result = gc.universe_power(panel, lengths, everys, scenarios, args.breadth_runs, workers=args.workers)
    elapsed = time.monotonic() - t0
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "tools"],
                                capture_output=True, text=True).stdout.strip())
    sizes = [len(m) for m in panel["members"]]
    record = {"label": "SYNTHETIC injection on REAL returns (placebo selection); evidence about the gate only",
              "commit": commit, "src_dirty": dirty, "rule": "x2: placebo half vs all members equal weight, Holm p<0.05, mean>=1%/yr, CI_low>0",
              "universe": {"dir": str(udir), "set_sha256": set_sha, "cutoff": args.cutoff, "top_n": args.top_n,
                           "grid_step": args.grid_step, "sessions": len(panel["calendar"]),
                           "first_session": panel["calendar"][0].isoformat(), "last_session": panel["calendar"][-1].isoformat(),
                           "membership_dates": len(panel["grid"]), "first_membership": calendar[panel["grid"][0]].isoformat(),
                           "members_min": min(sizes), "members_max": max(sizes), "ever_members": len(panel["returns"]),
                           "membership_cache": cache.name},
              "lengths": lengths, "rebalance_every_grid_points": everys, "runs": args.breadth_runs,
              "seconds": {"load": round(load_s, 1), "membership": round(member_s, 1), "total": round(elapsed, 1)}, **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = date.today().isoformat() + (args.suffix or "-universe-placebo")
    (out / f"{stem}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    lines = [f"# Gate power on the real universe {stem} (placebo selection; see gate_calibration.placebo_series)", "",
             f"commit `{commit}`{' (src dirty)' if dirty else ''}, {elapsed:.0f} s, universe set_sha256 `{set_sha[:16]}`, "
             f"sessions before {args.cutoff}, top {args.top_n} ({min(sizes)}-{max(sizes)} members), "
             f"rebalance every k x {args.grid_step} sessions", "",
             "| length, k | " + " | ".join(scenarios) + " |", "|---|" + "---|" * len(scenarios)]
    for n in lengths:
        for k in everys:
            cells = []
            for name in scenarios:
                e = result["summary"][f"{name}@L={n}@every={k}"]
                lo, hi = e["wilson_95"]
                cells.append(f"{e['rate']:.1%} [{lo:.1%}, {hi:.1%}], excess {e['mean_annual_excess']:+.2%}/yr")
            lines.append(f"| {n}, {k * args.grid_step} | " + " | ".join(cells) + " |")
    (out / f"{stem}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
