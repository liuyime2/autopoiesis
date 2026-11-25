"""Write the v6 forward basket's targets for the next session (research side; sends no order).

    python tools/basket_targets.py [--replay-root DIR] [--out-dir DIR] [--registry DIR] [--no-update]

The selection is the registered one, not a copy: `research.universe.members_at` (top 500)
and `research.xsection.selector("MOM_12_1", keep=V6_KEEP)` on a panel of returns before the next
session. The trading path cannot import the research package (`make verify` enforces it), so the
answer crosses over as a file. `src/autopoiesis/basket.py` reads it, validates it, and trades it
through Guardian like any other decision.

Steps:
1. Verify spec cf9529... against the registry.
2. Seed the forward universe cache from the frozen look-1 set if it is absent; the frozen set is
   never written.
3. Extend the cache to the last settled session (`fetch_universe_bars.update_cache`).
4. Ask the broker calendar for the next session.
5. Write `targets-<next session>.json`, owner-only and exclusive-create. A body sha256 lets the
   reader refuse an edited file.

Run weekly by `basket-targets.timer`. The account holder authorised forward paper trading of v6 on
2026-11-24 (docs/research/specs/v6-forward-paper.md).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from autopoiesis.basket import BASKET_STRATEGY_ID as STRATEGY_ID
from autopoiesis.basket import body_sha256

NY = ZoneInfo("America/New_York")
SPEC = ROOT / "docs" / "research" / "specs" / "v6-mom-top10.spec"
FROZEN = "universe-1Day-adjall"
FORWARD = "universe-forward-1Day-adjall"


def select_targets(bars_by_symbol: dict, next_session: date) -> tuple[list[str], list[str]]:
    """(members, held tenth) for `next_session`, from bars strictly before it."""
    from autopoiesis.research import xsection
    from autopoiesis.research.gate_calibration import universe_panel
    from autopoiesis.research.universe import members_at

    members = members_at(next_session, bars_by_symbol, top_n=500)
    calendar_last = max(b.timestamp.date() for bars in bars_by_symbol.values() for b in bars
                        if b.timestamp.date() < next_session)
    # `universe_panel` keys members by a calendar session; the selection reads returns before
    # t = len(calendar), the next session, which is not in the calendar yet.
    panel = universe_panel(bars_by_symbol, {calendar_last: members}, cutoff=next_session)
    held = xsection.selector("MOM_12_1", panel, keep=xsection.V6_KEEP)(len(panel["calendar"]), members)
    return members, held


def settled_end(now: datetime) -> date:
    """The last date whose daily bar is settled: today after 17:00 New York, else yesterday."""
    local = now.astimezone(NY)
    return local.date() if local.hour >= 17 else local.date() - timedelta(days=1)


def next_session_after(client, day: date) -> date:
    rows = client.get_calendar(start=(day + timedelta(days=1)).isoformat(),
                               end=(day + timedelta(days=10)).isoformat())
    days = sorted(r.date.date() if isinstance(r.date, datetime) else date.fromisoformat(str(r.date)[:10])
                  for r in rows)
    later = [d for d in days if d > day]
    if not later:
        raise SystemExit(f"the broker calendar has no session in the 10 days after {day}")
    return later[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--replay-root", type=Path, default=ROOT / "runtime" / "autopoiesis" / "replay")
    p.add_argument("--out-dir", type=Path, default=ROOT / "runtime" / "autopoiesis" / "basket")
    p.add_argument("--registry", type=Path, default=None)
    p.add_argument("--no-update", action="store_true", help="use the forward cache as it is")
    args = p.parse_args(argv)

    from autopoiesis.atomicio import narrow_private
    from autopoiesis.replay import load_bars
    from autopoiesis.research import prereg
    from fetch_replay_bars import client_from_env
    from fetch_universe_bars import update_cache

    ok, problems = prereg.verify(SPEC, args.registry)
    if not ok:
        raise SystemExit("the v6 spec does not match its registration:\n  " + "\n  ".join(problems))
    t0 = time.monotonic()
    forward = args.replay_root / FORWARD
    if not (forward / "manifest.json").exists():
        shutil.copytree(args.replay_root / FROZEN, forward)
        narrow_private(forward, mode=0o700)
        print(f"seeded {forward} from {FROZEN}")
    client = client_from_env()
    if not args.no_update:
        update_cache(client, forward, settled_end(datetime.now(NY)).isoformat())
    manifest = json.loads((forward / "manifest.json").read_text())
    bars = {s: b for s in manifest["symbols"] if (b := load_bars(forward / f"{s}.json"))}
    as_of = max(b[-1].timestamp.date() for b in bars.values())
    effective = next_session_after(client, as_of)
    members, held = select_targets(bars, effective)

    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    body = {"strategy_id": STRATEGY_ID, "as_of": as_of.isoformat(), "effective": effective.isoformat(),
            "symbols": held, "members": len(members), "spec_sha256": prereg.spec_hash(SPEC),
            "universe": {"dir": FORWARD, "set_sha256": manifest["set_sha256"], "end": manifest["end"]},
            "commit": commit, "created_at": datetime.now(NY).isoformat()}
    body["sha256"] = body_sha256(body)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    narrow_private(args.out_dir, mode=0o700)
    path = args.out_dir / f"targets-{effective.isoformat()}.json"
    if path.exists():
        old = json.loads(path.read_text())
        same = old.get("symbols") == held
        print(f"{path.name} exists ({'same' if same else 'DIFFERENT'} symbols); not rewritten")
        return 0 if same else 1
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(body, indent=1, sort_keys=True) + "\n")
    path.chmod(0o600)
    print(f"{path}: {len(held)} of {len(members)} members, as of {as_of}, effective {effective}, "
          f"{time.monotonic() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
