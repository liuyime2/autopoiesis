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


def test_rotation_does_not_hide_history_from_read_all(tmp_path):
    """Rotation moves records to `.1`; `read_all` must still return every one of them.

    This is a real defect, not a hypothetical. The journal on this host crossed its size cap
    during the refactor and rotated: `journal.jsonl` restarted with 17 lines while
    `journal.jsonl.1` held 10,587. Every reader that opened only the live file - `read_all`,
    `read_events`, `replay_audit.py`, `check_runtime_integrity.py` - then concluded the agent
    had never run a cycle, and the gate reported fabricated-looking data provenance
    ("reported={'alpaca': 0}") about 1,048 broker-sourced cycles sitting in the rotated file.

    The journal is append-only and generations are ordered by age, so reading oldest-first
    reconstructs the original sequence.
    """
    # Four generations retained, a cap small enough that rotation happens repeatedly, and a
    # record count chosen to stay inside that retention. `backups` is a bound, not a
    # guarantee: a fifth rotation deletes `.3`, and an earlier version of this test wrote 40
    # records against backups=3 and failed with "wrote 40, read 8" - which is the retention
    # policy working correctly, not a bug. The number of records is now derived from the
    # cap so the test exercises rotation without overflowing it.
    cap = 4096
    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=cap, backups=4)
    sample = make_record("cycle-0")
    per_record = len(sample.model_dump_json().encode("utf-8")) + 1
    per_generation = max(1, cap // per_record)
    total = per_generation * 3  # three full generations, comfortably inside backups=4

    for index in range(total):
        journal.append(make_record(f"cycle-{index}"))

    generations = [p.name for p in journal.history_paths()]
    assert len(generations) > 1, f"expected rotation to produce backups, got {generations}"

    records = journal.read_all()
    assert len(records) == total, (
        f"rotation lost records: wrote {total}, read {len(records)}; "
        f"generations present: {generations}"
    )
    assert [r.cycle_id for r in records] == [f"cycle-{i}" for i in range(total)], (
        "rotated history must read back in the original append order"
    )


def test_history_paths_orders_oldest_generation_first(tmp_path):
    """`.N` is the oldest generation, so history_paths must descend to `.1` then the live file.

    Order is not cosmetic: `last_n` keeps a bounded deque, so reading newest-first would
    return the wrong window.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl", backups=3)
    (tmp_path / "journal.jsonl.3").write_text("", encoding="utf-8")
    (tmp_path / "journal.jsonl.2").write_text("", encoding="utf-8")
    (tmp_path / "journal.jsonl.1").write_text("", encoding="utf-8")
    (tmp_path / "journal.jsonl").write_text("", encoding="utf-8")

    assert [p.name for p in journal.history_paths()] == [
        "journal.jsonl.3",
        "journal.jsonl.2",
        "journal.jsonl.1",
        "journal.jsonl",
    ]


def test_history_paths_skips_absent_generations(tmp_path):
    """A missing backup must not appear, and must not stop the live file from being read."""
    journal = JsonlJournal(tmp_path / "journal.jsonl", backups=3)
    (tmp_path / "journal.jsonl.1").write_text("", encoding="utf-8")
    (tmp_path / "journal.jsonl").write_text("", encoding="utf-8")

    assert [p.name for p in journal.history_paths()] == ["journal.jsonl.1", "journal.jsonl"]


def test_history_paths_is_empty_when_nothing_has_run(tmp_path):
    """No journal and no backups must read as an empty history, not raise."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    assert journal.history_paths() == []
    assert journal.read_all() == []
