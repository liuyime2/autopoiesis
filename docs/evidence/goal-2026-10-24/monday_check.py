"""The first-open checklist of SUMMARY_2026-10-24.md section 5, as one read-only command.

Reads the heartbeat, today's journalled cycles and the strategy library. Places and cancels nothing.
Each line is PASS, FAIL or INFO with the figure it rests on; nothing is inferred.

Usage: PYTHONPATH=src python monday_check.py [--date YYYY-MM-DD]   (New York date; default today)
"""
from __future__ import annotations

import collections
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from autopoiesis.config import AgentConfig
from autopoiesis.journal import JsonlJournal
from autopoiesis.sleeve import SLEEVE_STRATEGY_ID

NY = ZoneInfo("America/New_York")
CASH = {"BIL", "SHV", "SGOV", "USFR", "TFLO", "BOXX", "ICSH", "MINT", "JPST", "GBIL"}
DEPLOYED_AFTER = datetime(2026, 10, 25, 1, 57, tzinfo=timezone.utc)  # first build with every rule


def line(status: str, name: str, detail: str) -> None:
    print(f"[{status:4}] {name:34} {detail}")


def main() -> None:
    args = sys.argv[1:]
    day = date.fromisoformat(args[args.index("--date") + 1]) if "--date" in args else datetime.now(NY).date()
    config = AgentConfig.from_env()
    root = config.journal_path.parent
    beat = json.loads((root / "heartbeat.json").read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(beat["timestamp"].replace("Z", "+00:00"))).total_seconds()
    line("PASS" if age < 900 and beat["status"] in {"RUNNING", "BACKING_OFF"} else "FAIL", "daemon heartbeat",
         f"{beat['status']}, {age:.0f}s old, build {beat['source_fingerprint']}")
    line("PASS" if beat["error_count"] == 0 else "FAIL", "daemon errors",
         f"error_count {beat['error_count']}, last_error {beat.get('last_error')!r}")

    records = [r for r in JsonlJournal(config.journal_path).last_n(6000)
               if r.snapshot.timestamp.astimezone(NY).date() == day]
    records.sort(key=lambda r: r.snapshot.timestamp)
    if not records:
        line("FAIL", "cycles today", f"no cycles journalled on {day}")
    else:
        first = records[0].snapshot.timestamp.astimezone(NY)
        errors = sum(1 for r in records if r.error)
        line("PASS" if (first.hour, first.minute) <= (9, 50) else "FAIL", "first cycle at the open", f"{first:%H:%M} NY, {len(records)} cycles")
        line("PASS" if errors == 0 else "FAIL", "cycle errors", f"{errors} of {len(records)} cycles carry an error")
        sleeve = [r for r in records if r.decision.strategy_id == SLEEVE_STRATEGY_ID]
        tally = collections.Counter(f"{r.decision.action}/{r.execution.status}" for r in sleeve)
        refused = [r for r in sleeve if r.execution.status in {"SKIPPED", "REJECTED"} and r.decision.action != "HOLD"]
        line("INFO", "core sleeve decisions", f"{len(sleeve)}: {dict(tally)}" if sleeve else "none (in band or no usable forecast; expected on a quiet day)")
        line("PASS" if not refused else "FAIL", "core sleeve refused by a gate", f"{len(refused)}" + (f", e.g. {refused[0].guardian.reason}" if refused else ""))

    strategies = [json.loads(p.read_text()) for p in (root / "strategies").glob("*.json")]
    fresh = [s for s in strategies if datetime.fromisoformat(s["created_at"].replace("Z", "+00:00")) >= DEPLOYED_AFTER]
    bad = [s["strategy_id"] for s in fresh if s["kind"] == "TREND_FOLLOW" or CASH & set(s["symbols"])]
    line("PASS" if not bad else "FAIL", "no refused strategy admitted", f"{len(fresh)} new since the build; offenders {bad}")
    live_bil = [s["strategy_id"] for s in strategies if s["symbols"] == ["BIL"] and s["lifecycle"] in {"PROBATION", "ACTIVE"}]
    line("PASS" if not live_bil else "FAIL", "BIL strategies stay paused", f"{len(live_bil)} live: {live_bil}")


if __name__ == "__main__":
    main()
