#!/usr/bin/env python3
"""Adjudicate strategy files that entered the library without an admission record.

Five files were written into the strategy library after the admission gate was
already journalled, and three of them placed 33 broker-confirmed paper fills. Two
are ACTIVE and therefore selectable. The doctor reports this as a blocking FAIL
and the gate is red until each has a recorded disposition.

This is the operator-chosen resolution: run the real gate over each spec and
record a real, dated verdict. It is deliberately *not* a rubber stamp:

- `StrategyAdmission.admit()` short-circuits with "strategy_id already exists" for
  anything already in the library, so feeding these files to it unchanged would
  record a verdict that evaluates nothing. Each spec is therefore judged against a
  library that excludes itself - exactly the view it would have met if it had been
  proposed fresh - so the same rules decide it now as would have decided it then.
- An accepted spec lands on the lifecycle the gate itself assigns a newly admitted
  trading skill. A refused spec is RETIRED, so it stops being selectable. Neither
  outcome is special-cased.

The historical fills are unaffected either way. Thirty-three broker-confirmed fills
stay attributable to a strategy that was never approved at the time; re-adjudicating
today cannot rewrite that, and this tool does not pretend to.

Usage: python tools/adjudicate_unadmitted.py [--apply]
Without --apply it reports the verdicts and changes nothing.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timezone

from min_agent.config import AgentConfig
from min_agent.guardian import Guardian
from min_agent.journal import JsonlJournal
from min_agent.models import JournalEvent, StrategySpec
from min_agent.strategy_admission import StrategyAdmission
from min_agent.strategy_engine import StrategyLibrary


def unadmitted(library: StrategyLibrary, journal: JsonlJournal) -> list[StrategySpec]:
    reviews = journal.read_events("STRATEGY_ADMISSION_REVIEWED")
    accepted = {e.strategy_id for e in reviews if e.status == "ACCEPTED" and e.strategy_id}
    return [s for s in library.list() if s.strategy_id not in accepted]


def judge(config: AgentConfig, spec: StrategySpec, others: list[StrategySpec],
          price: float | None, symbol: str) -> tuple[bool, str]:
    """Run the real gate over one spec, with the library holding everything else."""
    staging = Path(tempfile.mkdtemp(prefix="adjudicate-"))
    try:
        for other in others:
            (staging / f"{other.strategy_id}.json").write_text(
                other.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
        guardian = Guardian(
            allowlist=config.allowlist,
            max_position_value=config.max_position_value,
            max_daily_loss=config.max_daily_loss,
            max_trades_per_day=config.max_trades_per_day,
            max_total_exposure=config.max_total_exposure,
            min_confidence=config.min_confidence,
            max_account_value=config.max_account_value or None,
            max_snapshot_age_seconds=config.stale_after_seconds,
        )
        admission = StrategyAdmission(
            guardian=guardian,
            strategy_library=StrategyLibrary(staging),
        )
        # TREND_FOLLOW carries a reference_price that admission compares against
        # live market data. The last price the journal actually observed is used,
        # with its recorded source, rather than a number invented here: a gate
        # that decides on a fabricated price is not a gate.
        if price is not None:
            admission.set_market_prices({symbol: price})
        result = admission.admit(spec)
        return result.accepted, result.reason
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write the verdicts; without it, report only")
    args = parser.parse_args()

    config = AgentConfig.from_env()
    library = StrategyLibrary(config.strategy_dir)
    journal = JsonlJournal(config.journal_path)
    targets = unadmitted(library, journal)
    if not targets:
        print("every library file traces to an accepted admission; nothing to do")
        return 0

    all_specs = library.list()
    # Real last observed price, with the source the journal recorded for it.
    last_records = journal.last_n(1)
    price = None
    symbol = config.symbols[0] if config.symbols else "SPY"
    price_source = "unavailable - reference-price rule skipped"
    if last_records:
        snap = last_records[0].snapshot
        price = snap.last_price
        symbol = snap.symbol
        price_source = f"{snap.source} at {snap.timestamp:%Y-%m-%d %H:%M:%S%z}"
    print(f"reference price for the TREND_FOLLOW rule: {symbol}={price} ({price_source})\n")
    fills: dict[str, int] = {}
    for event in journal.read_events("ORDER_FILL_CONFIRMED"):
        if event.strategy_id:
            fills[event.strategy_id] = fills.get(event.strategy_id, 0) + 1

    print(f"{len(targets)} library file(s) with no accepted admission\n")
    changed = 0
    for spec in targets:
        others = [s for s in all_specs if s.strategy_id != spec.strategy_id]
        accepted, reason = judge(config, spec, others, price, symbol)
        lifecycle = spec.lifecycle
        if not accepted:
            # A refused spec must stop being selectable. RETIRED is the gate's own
            # terminal state; deletion is not, because it destroys the file the 33
            # fills' provenance points at.
            lifecycle = "RETIRED"
        elif lifecycle == "PAUSED":
            # Approval makes a spec legitimate; it does not retract a risk decision.
            # PROBATION is selectable and PAUSED is not, so promoting a PAUSED
            # strategy here would silently put a strategy back into trading because
            # a paperwork gap was closed. An admitted strategy stays admitted and
            # paused until something promotes it on purpose.
            lifecycle = "PAUSED"
        else:
            # What the gate assigns a newly admitted trading skill, unchanged.
            lifecycle = "BASELINE" if spec.kind == "HOLD_BASELINE" else "PROBATION"
        mark = "APPROVE" if accepted else "REFUSE "
        print(f"  {mark} {spec.strategy_id:24s} {spec.kind:13s} "
              f"fills={fills.get(spec.strategy_id, 0):2d}  lifecycle {spec.lifecycle} -> {lifecycle}")
        print(f"         {reason}")
        if not args.apply:
            continue
        now = datetime.now(timezone.utc)
        # Re-runnable: the same strategy adjudicated twice must not collide on
        # event_id, or the second verdict would be indistinguishable from the
        # first in any dedup.
        admission_event_id = f"adjudicate-{spec.strategy_id}-{now:%Y%m%dT%H%M%SZ}"
        journal.append_event(JournalEvent(
            # Re-runnable: the same strategy adjudicated twice must not collide on
            # event_id, or the second verdict would be indistinguishable from the
            # first in any dedup.
            event_id=admission_event_id,
            event_type="STRATEGY_ADMISSION_REVIEWED",
            timestamp=now,
            strategy_id=spec.strategy_id,
            status="ACCEPTED" if accepted else "REJECTED",
            message=f"re-adjudicated {now:%Y-%m-%d} against current rules: {reason}",
            payload={"accepted": accepted, "reason": reason,
                     "re_adjudication": True,
                     "historical_fills": fills.get(spec.strategy_id, 0)},
        ))
        # Record the decision whenever the current lifecycle is not already backed
        # by one, not merely when the file changes. The first --apply run wrote the
        # lifecycle without the event, and a re-run that only compared old != new
        # would have found them equal and left the gap in place forever.
        decided: dict[str, str] = {}
        for event in journal.read_events("STRATEGY_LIFECYCLE_UPDATED"):
            if event.strategy_id and isinstance(event.payload.get("new_lifecycle"), str):
                decided[event.strategy_id] = event.payload["new_lifecycle"]
        if decided.get(spec.strategy_id) != lifecycle:
            # A lifecycle change without a STRATEGY_LIFECYCLE_UPDATED event is
            # itself an unaccounted state change: the doctor's lifecycle-provenance
            # check exists precisely to catch a strategy that reached a gated state
            # with no recorded decision behind it, and writing the file without the
            # event trips it immediately. The first --apply run did exactly that.
            journal.append_event(JournalEvent(
                event_id=f"adjudicate-lifecycle-{spec.strategy_id}-{now:%Y%m%dT%H%M%SZ}",
                event_type="STRATEGY_LIFECYCLE_UPDATED",
                timestamp=now,
                status="SUCCESS",
                strategy_id=spec.strategy_id,
                message=(f"re-adjudication moved {spec.lifecycle} -> {lifecycle}: {reason}"),
                payload={"old_lifecycle": spec.lifecycle, "new_lifecycle": lifecycle,
                         "reason": reason, "phase": "applied",
                         "decided_event_id": admission_event_id,
                         "re_adjudication": True},
            ))
        library.save(spec.model_copy(update={"lifecycle": lifecycle}))
        changed += 1

    if not args.apply:
        print("\ndry run; pass --apply to record these verdicts")
        return 0
    print(f"\nrecorded {changed} verdict(s)")

    # Second pass. A strategy this tool has re-adjudicated may have had its
    # lifecycle moved while its *admission* verdict was already on record, so it
    # no longer appears in `unadmitted()` and would never get a lifecycle decision
    # written. That is exactly what happened to the three approved strategies on
    # the first run: their files say PROBATION, the last lifecycle decision on
    # record is from June, and the doctor's lifecycle check cannot see the
    # discrepancy because PROBATION is one of the default states it exempts.
    re_adjudicated = {
        e.strategy_id
        for e in journal.read_events("STRATEGY_ADMISSION_REVIEWED")
        if e.strategy_id and e.payload.get("re_adjudication")
    }
    decided: dict[str, str] = {}
    for event in journal.read_events("STRATEGY_LIFECYCLE_UPDATED"):
        if event.strategy_id and isinstance(event.payload.get("new_lifecycle"), str):
            decided[event.strategy_id] = event.payload["new_lifecycle"]
    backfilled = 0
    for spec in library.list():
        if spec.strategy_id not in re_adjudicated:
            continue
        if decided.get(spec.strategy_id) == spec.lifecycle:
            continue
        now = datetime.now(timezone.utc)
        journal.append_event(JournalEvent(
            event_id=f"adjudicate-lifecycle-{spec.strategy_id}-{now:%Y%m%dT%H%M%SZ}",
            event_type="STRATEGY_LIFECYCLE_UPDATED",
            timestamp=now, status="SUCCESS", strategy_id=spec.strategy_id,
            message=(f"re-adjudication recorded the lifecycle this run left in place: "
                     f"{spec.lifecycle}"),
            payload={"old_lifecycle": decided.get(spec.strategy_id),
                     "new_lifecycle": spec.lifecycle,
                     "reason": "lifecycle decision written for a re-adjudicated strategy",
                     "phase": "applied", "re_adjudication": True},
        ))
        backfilled += 1
        print(f"  backfilled lifecycle decision: {spec.strategy_id:24s} -> {spec.lifecycle}")
    if backfilled:
        print(f"\nbackfilled {backfilled} lifecycle decision(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
