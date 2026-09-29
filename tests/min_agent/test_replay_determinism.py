"""The same recorded inputs must produce the same outputs, twice.

This is what makes an offline validation result mean anything. If replaying a
recorded decision cycle can produce a different verdict than the live run did, then
"validated offline" and "working live" are two different systems and no promotion
decision is evidence of anything.

Determinism also is what makes a failure reproducible: a bug found at 03:00 has to
still be there at 09:00 when you try to look at it.
"""

from datetime import datetime, timedelta, timezone
import json

from min_agent import counterfactual as cf
from min_agent.journal import JsonlJournal
from min_agent.models import (
    AccountSnapshot, CycleRecord, DataSnapshot, ExecutionResult,
    GuardianResult, TradeDecision,
)

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _record(cycle_id, when, price, action="HOLD"):
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=when, market_open=True, last_price=price,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=50_000, buying_power=50_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action=action,
            quantity=0 if action == "HOLD" else 1,
            confidence=0.6, rationale="r",
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status="SKIPPED", order_id=None, message="hold", filled_quantity=0.0,
        ),
    )


def _sample():
    records = []
    for i in range(40):
        records.append(
            _record(f"c{i:03d}", T0 + timedelta(minutes=30 * i), 100.0 + i * 0.3)
        )
    return records


def test_the_same_records_score_identically_twice():
    records = _sample()
    first = cf.evaluate(records, horizon_hours=4.0)
    second = cf.evaluate(records, horizon_hours=4.0)

    assert first.counts() == second.counts()
    assert first.hold_quality() == second.hold_quality()
    assert [r.verdict for r in first.rows] == [r.verdict for r in second.rows]
    assert [r.net_return_pct for r in first.rows] == [
        r.net_return_pct for r in second.rows
    ]


def test_evaluating_does_not_mutate_its_input():
    """A replay that quietly rewrites the record it is reading is not a replay."""
    records = _sample()
    before = [r.snapshot.last_price for r in records]
    before_actions = [r.decision.action for r in records]

    cf.evaluate(records, horizon_hours=4.0)

    assert [r.snapshot.last_price for r in records] == before
    assert [r.decision.action for r in records] == before_actions


def test_the_result_survives_a_json_round_trip():
    """The rows are journaled as JSON, so what is written and what is recomputed
    from the journal must be the same numbers."""
    records = _sample()
    report = cf.evaluate(records, horizon_hours=4.0)

    written = json.loads(json.dumps(
        [
            {
                "cycle_id": r.cycle_id,
                "verdict": r.verdict,
                "net": r.net_return_pct,
                "cost": r.assumed_cost_pct,
            }
            for r in report.rows
        ]
    ))
    replayed = cf.evaluate(records, horizon_hours=4.0)

    assert written == [
        {
            "cycle_id": r.cycle_id,
            "verdict": r.verdict,
            "net": r.net_return_pct,
            "cost": r.assumed_cost_pct,
        }
        for r in replayed.rows
    ]


def test_replaying_the_live_journal_reproduces_the_recorded_verdicts():
    """The strongest statement available: the verdicts the daemon wrote to the
    journal can be recomputed from that same journal."""
    events = JsonlJournal("runtime/min_agent/journal.jsonl").read_events(
        "COUNTERFACTUAL_EVALUATED"
    )
    if not events:
        return  # nothing recorded yet; nothing to contradict

    recorded = {}
    for event in events:
        for row in event.payload.get("rows", []):
            recorded[row["cycle_id"]] = (row["verdict"], row["net_return_pct"])

    records = JsonlJournal("runtime/min_agent/journal.jsonl").read_all()
    report = cf.evaluate(records, symbol="SPY", horizon_hours=24.0)

    mismatches = []
    for row in report.rows:
        prior = recorded.get(row.cycle_id)
        if prior is None:
            continue
        if prior[0] != row.verdict or prior[1] != row.net_return_pct:
            mismatches.append((row.cycle_id, prior, row.verdict, row.net_return_pct))

    assert not mismatches, (
        f"{len(mismatches)} journalled verdicts did not reproduce: {mismatches[:3]}"
    )
