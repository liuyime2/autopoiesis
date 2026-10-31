"""The first real-data open under the 2026-10-31 build, as one read-only command.

Supersedes goal-2026-10-24/monday_check.py (kept for history). Reads the heartbeat, the journal, the
strategy library and the deployment record; places and cancels nothing. Each line is PASS, FAIL or INFO
with the figure it rests on.

Usage: PYTHONPATH=src python first_open_check.py [--date YYYY-MM-DD]   (New York date; default today)
"""
from __future__ import annotations

import collections
import json
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from autopoiesis.config import AgentConfig
from autopoiesis.equity_curve import cohort_days, cohort_start, daily_marks
from autopoiesis.journal import JsonlJournal, unreal_reason
from autopoiesis.signal_ledger import read_rows, refuted_lookbacks
from autopoiesis.sleeve import SLEEVE_STRATEGY_ID

NY = ZoneInfo("America/New_York")
CASH = {"BIL", "SHV", "SGOV", "USFR", "TFLO", "BOXX", "ICSH", "MINT", "JPST", "GBIL"}
REPO = __import__("pathlib").Path(__file__).resolve().parents[3]


def line(status: str, name: str, detail: str) -> None:
    print(f"[{status:4}] {name:36} {detail}")


def main() -> None:
    args = sys.argv[1:]
    day = date.fromisoformat(args[args.index("--date") + 1]) if "--date" in args else datetime.now(NY).date()
    config = AgentConfig.from_env()
    root = config.journal_path.parent
    started = cohort_start(REPO / "docs" / "evidence" / "deployments.jsonl")

    beat = json.loads((root / "heartbeat.json").read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(beat["timestamp"].replace("Z", "+00:00"))).total_seconds()
    line("PASS" if age < 900 and beat["status"] in {"RUNNING", "BACKING_OFF"} else "FAIL", "daemon heartbeat",
         f"{beat['status']}, {age:.0f}s old, build {beat['source_fingerprint']}")
    line("PASS" if started and started[1] == beat["source_fingerprint"] else "FAIL", "running build is the recorded build",
         f"running {beat['source_fingerprint']}, last deployment record {started[1] if started else None}")
    line("PASS" if beat["error_count"] == 0 else "FAIL", "daemon errors", f"{beat['error_count']}, last {beat.get('last_error')!r}")

    journal = JsonlJournal(config.journal_path)
    everything = journal.read_all()
    unreal = [r.cycle_id for r in everything if unreal_reason(r)]
    line("PASS" if not unreal else "FAIL", "no fabricated cycle in the journal", f"{len(everything)} retained, unreal and unacknowledged: {unreal[:3]}")

    todays = sorted((r for r in everything if r.snapshot.timestamp.astimezone(NY).date() == day), key=lambda r: r.snapshot.timestamp)
    if not todays:
        line("FAIL", "cycles today", f"none journalled on {day}")
    else:
        first = todays[0].snapshot.timestamp.astimezone(NY)
        errors = sum(1 for r in todays if r.error)
        line("PASS" if (first.hour, first.minute) <= (9, 50) else "FAIL", "first cycle at the open", f"{first:%H:%M} NY, {len(todays)} cycles")
        line("PASS" if errors == 0 else "FAIL", "cycle errors", f"{errors} of {len(todays)}")
        # Order behaviour is judged on the cycles that ran under this build only: a date from before
        # the deployment would otherwise report the behaviour the build was installed to stop.
        mine = [r for r in todays if started is None or r.snapshot.timestamp >= started[0]]
        line("INFO", "cycles under this build", f"{len(mine)} of {len(todays)} on {day}")
        sleeve = [r for r in mine if r.decision.strategy_id == SLEEVE_STRATEGY_ID and r.decision.action != "HOLD"]
        tally = collections.Counter(f"{r.decision.action}/{r.execution.status}" for r in sleeve)
        line("INFO", "core sleeve orders", f"{dict(tally)}" if sleeve else "none (inside its band; expected on a quiet day)")
        spy_other = [r for r in mine if r.snapshot.symbol == "SPY" and r.decision.action != "HOLD" and r.decision.strategy_id != SLEEVE_STRATEGY_ID]
        line("PASS" if not spy_other else "FAIL", "only the sleeve trades SPY", f"{len(spy_other)} other SPY order(s)")
        tlt_buys = [r for r in mine if r.snapshot.symbol == "TLT" and r.decision.action == "BUY" and r.execution.status in {"SUBMITTED", "REPLAYED"}]
        line("PASS" if not tlt_buys else "FAIL", "TLT is not being added to", f"{len(tlt_buys)} BUY(s): {sorted({r.decision.strategy_id for r in tlt_buys})}")

    strategies = [json.loads(p.read_text()) for p in (root / "strategies").glob("*.json")]
    since = started[0] if started else datetime.now(timezone.utc)
    fresh = [s for s in strategies if datetime.fromisoformat(s["created_at"].replace("Z", "+00:00")) >= since]
    refuted = refuted_lookbacks(read_rows(root / "signal_trials.jsonl"))
    bad = [s["strategy_id"] for s in fresh if s["kind"] == "TREND_FOLLOW" or CASH & set(s["symbols"])
           or (s["kind"] == "RULE" and s["parameters"].get("signal") in refuted
               and int(s["parameters"].get("lookback", 0)) <= refuted[s["parameters"]["signal"]][0])]
    line("PASS" if not bad else "FAIL", "no refused strategy admitted", f"{len(fresh)} new under this build; offenders {bad}")
    live_paused = [s["strategy_id"] for s in strategies if s["strategy_id"] == "trend-follow-tlt-buy-001" and s["lifecycle"] not in {"PAUSED", "RETIRED"}]
    line("PASS" if not live_paused else "FAIL", "trend-follow-tlt-buy-001 stays paused", f"lifecycle {live_paused or 'PAUSED'}")

    if started:
        line("INFO", "cohort under this build", f"{cohort_days(daily_marks(everything), started[0])} of 60 trading days since {started[0]:%Y-%m-%d %H:%M}")


if __name__ == "__main__":
    main()
