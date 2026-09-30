#!/usr/bin/env python3
"""Re-adjudicate strategies retired by a lifecycle rule that was reading bad data.

`fixed-size-sell-005` was retired on 2026-09-30 with the reason "severe operational
failure rate 1.00". The three cycles behind that figure are real and the decision was
correct given the data it was handed: every order it submitted was refused, three out
of three, which is above the 0.75 threshold.

The data was wrong. All three refusals read "cannot sell 1 SPY; the agent holds 0",
and that figure came from `loop._agent_holding`, which was summing SELLs as a
commutative total. Given 33 BUY fills and one 52-share SELL it produced -19, floored
to zero, so the Guardian refused every exit from shares the agent had bought. The
holding bug is fixed and `_agent_holding` now reports the agent's true position.

A second rule compounded it. Guardian reasons that interpolate the offending quantity
interpolate the quantity into the message, so `strategy_fault_rejections` - which
compared reasons by exact equality against a literal `frozenset` - classified "the agent
holds 0" as a fault of the strategy. A strategy was removed from the library for
obeying the system. `evaluator.is_system_rejection` now classifies by stable prefix.

Together these left the library with no sellable SELL strategy at all: `fixed-size-sell-001`
retired earlier, `fixed-size-sell-005` retired here, `trend-follow-sell-002` PAUSED. The
objective's outstanding requirement is a round trip, and a round trip needs an exit.

What this tool does, and deliberately does not do:

- It re-runs the real `StrategyLifecycleManager` over the journal's own records and
  applies whatever verdict that produces now. It does not assert the strategy "should" be
  PROBATION. If the current rules still say RETIRED, the strategy stays retired.
- A retirement caused by bad data is a fact about the past, not something to erase. The
  original `STRATEGY_LIFECYCLE_UPDATED` events stay in the journal untouched, and this
  adds new ones. Both the decision and its application are journalled, in that order,
  matching `AgentDaemon._manage_strategy_lifecycle`: an event for a transition that did
  not happen is visible and harmless, while a silent file change is not.
- It does not touch fills, PnL or any historical attribution. The 29 shares the agent
  sold out of the account owner's position remain what the record says they are.

Usage: python tools/readmit_misattributed.py [--apply]
Without --apply it reports what the current rules decide and changes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from min_agent.journal import JsonlJournal  # noqa: E402
from min_agent.models import CycleRecord, JournalEvent, StrategyResult  # noqa: E402
from min_agent.reflection_memory import ReflectionMemory  # noqa: E402
from min_agent.evaluator import DeterministicEvaluator, is_system_rejection  # noqa: E402
from min_agent.strategy_engine import StrategyLibrary, StrategyLifecycleManager  # noqa: E402


def _restore_candidates(journal: JsonlJournal) -> list[str]:
    """Strategies retired by the superseded rule *and* refused only for system reasons.

    Both conditions matter. "severe operational failure rate" alone is not sufficient:
    five strategies carry that reason, and for some of them the refusals were genuinely
    their own fault - a strategy asking for a position past the hard limit really has
    faulted. The distinguishing evidence is the Guardian reason recorded against each of
    its refusals, so a strategy is a candidate only when *every* one of them is a reason
    the system could not avoid.

    Selected from the journal rather than named, so a second strategy retired the same way
    is picked up too, and one retired for any other reason is not.
    """
    retired_for: dict[str, list[str]] = {}
    for event in journal.read_events("STRATEGY_LIFECYCLE_UPDATED"):
        payload = event.payload or {}
        if payload.get("new_lifecycle") != "RETIRED":
            continue
        strategy_id = event.strategy_id
        if strategy_id:
            retired_for.setdefault(strategy_id, []).append(str(payload.get("reason", "")))

    refusals: dict[str, set[str]] = {}
    for line in journal.last_n(2000):
        strategy_id = line.decision.strategy_id
        if strategy_id and line.execution.status == "REJECTED":
            refusals.setdefault(strategy_id, set()).add(line.guardian.reason)

    # A strategy the admission gate refused is not this tool's business. Restoring one
    # would override a real decision for an unrelated reason - a behavioural duplicate,
    # say - and doctor's admission-provenance check correctly fails when a file in the
    # library has no verdict the gate can stand behind. `fixed-size-sell-001` was refused
    # three times as behaviourally identical to `trend-follow-sell-002`, and an early
    # version of this tool restored it to PROBATION on the strength of the retirement
    # reason alone. The gate was right and this tool was wrong.
    refused_at_admission = set()
    for event in journal.read_events("STRATEGY_ADMISSION_REVIEWED"):
        strategy_id = event.strategy_id
        if not strategy_id:
            continue
        status = str((event.payload or {}).get("status") or "").upper()
        if status and status != "ACCEPTED":
            refused_at_admission.add(strategy_id)

    candidates = []
    for strategy_id, reasons in retired_for.items():
        if not all("severe operational failure rate" in reason for reason in reasons):
            continue
        if strategy_id in refused_at_admission:
            continue
        seen = refusals.get(strategy_id)
        if seen and all(is_system_rejection(reason) for reason in seen):
            candidates.append(strategy_id)
    return sorted(candidates)


def _records_for(journal: JsonlJournal, strategy_id: str) -> list[CycleRecord]:
    records = []
    for line in journal.last_n(2000):
        if line.decision.strategy_id == strategy_id:
            records.append(line)
    return records


def _lifecycle_before_retirement(journal: JsonlJournal, strategy_id: str) -> str | None:
    """The lifecycle this strategy held before the superseded retirement."""
    for event in reversed(journal.read_events("STRATEGY_LIFECYCLE_UPDATED")):
        if event.strategy_id != strategy_id:
            continue
        payload = event.payload or {}
        if payload.get("new_lifecycle") != "RETIRED":
            continue
        if "severe operational failure rate" not in str(payload.get("reason", "")):
            continue
        previous = payload.get("old_lifecycle")
        if previous:
            return str(previous)
    return None


def _results_from(reports: list, candidates: list[str]) -> list:
    """Build StrategyResults the way ReflectionMemory does, from fresh evaluations.

    Deliberately not `record.strategy_metrics`: this exists to avoid trusting a snapshot
    the pre-fix evaluator produced. `strategy_metrics` holds typed StrategyEvaluation
    objects rather than dicts, so this reads attributes.
    """
    results = []
    for report in reports:
        for evaluation in report.strategy_metrics.values():
            if evaluation.strategy_id not in candidates:
                continue
            results.append(StrategyResult(
                strategy_id=evaluation.strategy_id,
                cycles=evaluation.cycles,
                submitted_orders=evaluation.submitted_orders,
                rejected_orders=evaluation.rejected_orders,
                errors=evaluation.errors,
                score=evaluation.score,
                evaluated_at=report.generated_at,
                skipped_orders=evaluation.skipped_orders,
                action_counts=evaluation.action_counts,
                guardian_rejections=evaluation.guardian_rejections,
                intended_notional=evaluation.intended_notional,
                filled_quantity=evaluation.filled_quantity,
                realized_pnl=evaluation.realized_pnl,
                fees=evaluation.fees,
                pnl_evidence=evaluation.pnl_evidence,
                trade_attempts=evaluation.trade_attempts,
                strategy_fault_rejections=evaluation.strategy_fault_rejections,
            ))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the new lifecycle decisions.")
    args = parser.parse_args(argv)

    journal = JsonlJournal(ROOT / "runtime" / "min_agent" / "journal.jsonl")
    library = StrategyLibrary(ROOT / "runtime" / "min_agent" / "strategies")
    manager = StrategyLifecycleManager()
    reflection = ReflectionMemory(ROOT / "runtime" / "min_agent" / "reflection.json")
    record = reflection.load()

    candidates = _restore_candidates(journal)
    if not candidates:
        print(json.dumps({"candidates": [], "note": "nothing was retired by the superseded rule"}, indent=2))
        return 0

    # The same inputs AgentDaemon passes to the lifecycle review, so the verdict is the
    # one production would reach now rather than one this script asserts.
    # The reflection record is a periodic snapshot, and reading it would mean asking a
    # question that was already answered by the code as it was at the time. The store
    # still carries strategy_fault_rejections=3 for fixed-size-sell-005 from a window
    # evaluated before the classification fix, so the rules would re-retire the strategy
    # on a number nothing has re-derived since. Recomputing from the journal with the
    # current evaluator is the whole point: the verdict has to reflect the rules as they
    # are now, applied to records that have not changed.
    evaluator = DeterministicEvaluator()
    reports = [
        evaluator.evaluate(_records_for(journal, strategy_id))
        for strategy_id in candidates
    ]
    recomputed = _results_from(reports, candidates)
    if record is not None:
        # Everything the snapshot knows about a strategy this window does not cover,
        # carried over untouched.
        known = {r.strategy_id: r for r in reflection.strategy_results(record)}
        for strategy_id, result in known.items():
            if all(r.strategy_id != strategy_id for r in recomputed):
                recomputed.append(result)
    results = recomputed
    by_id = {r.strategy_id: r for r in results}

    # `_review_one` returns None for a strategy already in RETIRED, so a lifecycle
    # review can never un-retire one. That is right for normal operation - retirement
    # is meant to be durable - and it means the verdict has to be asked while the
    # strategy is still PROBATION. Each candidate is therefore reviewed as a copy in
    # PROBATION. Nothing is written from that copy unless --apply, and the value
    # actually stored is the decision's own new_lifecycle.
    specs = []
    for strategy_id in candidates:
        strategy = library.try_load(strategy_id)
        if strategy is not None:
            specs.append(strategy.model_copy(update={"lifecycle": "PROBATION"}))
    decisions = {d.strategy.strategy_id: d for d in manager.review(specs, results, None)}

    verdicts = []
    for strategy_id in candidates:
        strategy = library.try_load(strategy_id)
        if strategy is None:
            print(f"{strategy_id}: no spec on disk, skipping", file=sys.stderr)
            continue
        result = by_id.get(strategy_id)
        decision = decisions.get(strategy_id)
        if decision is not None:
            target, reason = decision.new_lifecycle, decision.reason
        else:
            # No decision means the current rules no longer object. The state to go back
            # to is the one the journal recorded as the `old_lifecycle` of the retirement
            # being undone - read, not assumed, so this cannot drift from the record.
            target = _lifecycle_before_retirement(journal, strategy_id)
            reason = (
                "current rules raise no objection; restoring the lifecycle this "
                "strategy held before the retirement caused by misattributed rejections"
            )
        verdicts.append(
            {
                "strategy_id": strategy_id,
                "current_lifecycle": strategy.lifecycle,
                "cycles": result.cycles if result else 0,
                "fault_rejections": (
                    result.strategy_fault_rejections if result and result.strategy_fault_rejections is not None else 0
                ),
                "rejected_orders": result.rejected_orders if result else 0,
                "new_lifecycle": target,
                "reason": reason,
            }
        )

    print(json.dumps({"mode": "apply" if args.apply else "report", "verdicts": verdicts}, indent=2))

    if not args.apply:
        return 0

    applied = []
    for verdict in verdicts:
        if verdict["new_lifecycle"] is None or verdict["new_lifecycle"] == verdict["current_lifecycle"]:
            continue
        strategy = library.try_load(verdict["strategy_id"])
        payload = {
            "old_lifecycle": verdict["current_lifecycle"],
            "new_lifecycle": verdict["new_lifecycle"],
            "reason": verdict["reason"],
            "re_adjudication": True,
            "cause": "guardian reasons carrying interpolated quantities were charged to the strategy",
        }
        now = datetime.now(tz=timezone.utc)
        # JournalEvent is built before it is appended so its id is in hand:
        # `JsonlJournal.append_event` returns None, and the applied event has to be able
        # to cite the decided one. An earlier version of this tool read the id off the
        # return value, got None, and died between the file write and the applied event -
        # leaving the traceable half-state the daemon's own comment describes, caused
        # rather than discovered.
        decided = JournalEvent(
            event_id=str(uuid.uuid4()),
            event_type="STRATEGY_LIFECYCLE_UPDATED",
            timestamp=now,
            status="SUCCESS",
            message=verdict["reason"],
            strategy_id=verdict["strategy_id"],
            payload={**payload, "phase": "decided"},
        )
        journal.append_event(decided)
        library.save(strategy.model_copy(update={"lifecycle": verdict["new_lifecycle"]}))
        journal.append_event(JournalEvent(
            event_id=str(uuid.uuid4()),
            event_type="STRATEGY_LIFECYCLE_UPDATED",
            timestamp=now,
            status="SUCCESS",
            message=verdict["reason"],
            strategy_id=verdict["strategy_id"],
            payload={**payload, "phase": "applied", "decided_event_id": decided.event_id},
        ))
        applied.append(verdict["strategy_id"])

    print(json.dumps({"applied": applied}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
