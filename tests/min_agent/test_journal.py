from datetime import datetime, timezone

from min_agent.journal import JsonlJournal
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    TradeDecision,
)


def make_record(cycle_id="cycle-1"):
    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        market_open=True,
        last_price=100,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000,
            cash=100_000,
            buying_power=100_000,
            portfolio_value=100_000,
            daily_loss=0,
        ),
    )
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=snapshot,
        decision=TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold"),
    )


def make_event(event_type="CURRICULUM_PROPOSED"):
    return JournalEvent(
        event_id="event-1",
        event_type=event_type,
        timestamp=datetime(2026, 6, 3, 14, 31, tzinfo=timezone.utc),
        status="SUCCESS",
        message="event",
        cycle_id="cycle-1",
        payload={"task_type": "EVALUATE"},
    )


def test_jsonl_journal_appends_and_reads_records(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    record = make_record()

    journal.append(record)

    records = journal.read_all()
    assert len(records) == 1
    assert records[0].cycle_id == "cycle-1"
    assert records[0].decision.action == "HOLD"


def test_jsonl_journal_reads_mixed_records_and_events(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")

    journal.append(make_record("cycle-1"))
    journal.append_event(make_event("CURRICULUM_PROPOSED"))
    journal.append(make_record("cycle-2"))
    journal.append_event(make_event("STRATEGY_EVALUATION_RECORDED"))

    assert [record.cycle_id for record in journal.read_all()] == ["cycle-1", "cycle-2"]
    assert [record.cycle_id for record in journal.last_n(1)] == ["cycle-2"]
    assert [event.event_type for event in journal.read_events()] == [
        "CURRICULUM_PROPOSED",
        "STRATEGY_EVALUATION_RECORDED",
    ]
    assert [event.event_type for event in journal.read_events(event_type="curriculum_proposed")] == [
        "CURRICULUM_PROPOSED"
    ]
    assert [event.event_type for event in journal.read_events(limit=1)] == ["STRATEGY_EVALUATION_RECORDED"]


def test_jsonl_journal_skips_invalid_lines(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(path)
    journal.append(make_record("cycle-1"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    journal.append_event(make_event())

    assert [record.cycle_id for record in journal.read_all()] == ["cycle-1"]
    assert len(journal.read_events()) == 1
