"""A windowed journal read must return exactly what a full read would, filtered.

Why this is a file of its own. Measured 2026-10-22 on the live journal: `read_events()` with no
bound cost 15.78 s for 26,673 events, and the in-flight duplicate-order check called it every cycle
to answer a 30-minute question - 22.1 s of a 75.1 s cycle, because the daemon's own append changed
the file signature so the parse cache never hit. The fix reads the tail of the file instead. The
risk of that fix is not speed, it is silently answering a narrower question than the caller asked,
so what is pinned here is **equality with the unfiltered read**, not a spot check.
"""

from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis import journal as journal_module
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    TradeDecision,
)

BASE = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def _event(index: int, *, minutes: int | None = None, kind: str = "CURRICULUM_PROPOSED") -> JournalEvent:
    return JournalEvent(
        event_id=f"event-{index}",
        event_type=kind,
        timestamp=BASE + timedelta(minutes=(minutes if minutes is not None else index)),
        status="SUCCESS",
        message=f"event {index}",
        cycle_id=f"cycle-{index}",
        payload={"index": index},
    )


def _record(index: int, *, minutes: int | None = None) -> CycleRecord:
    when = BASE + timedelta(minutes=(minutes if minutes is not None else index))
    return CycleRecord(
        cycle_id=f"cycle-{index}",
        snapshot=DataSnapshot(
            symbol="SPY",
            timestamp=when,
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
        ),
        decision=TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold"),
    )


def _filled(tmp_path, count: int = 400) -> tuple[JsonlJournal, datetime]:
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for index in range(count):
        journal.append_event(_event(index))
        if index % 4 == 0:
            journal.append(_record(index, minutes=index))
    since = BASE + timedelta(minutes=count - 40)
    return journal, since


def test_a_windowed_read_equals_a_full_read_filtered_by_the_same_bound(tmp_path):
    journal, since = _filled(tmp_path)
    everything = journal.read_events()
    assert len(everything) == 400

    expected = [event for event in everything if event.timestamp >= since]
    assert expected, "the fixture must actually have records inside the window"

    assert [e.event_id for e in journal.read_events(since=since)] == [e.event_id for e in expected]


def test_a_windowed_read_keeps_the_event_filter_and_the_limit_semantics(tmp_path):
    journal, since = _filled(tmp_path)
    journal.append_event(_event(900, minutes=399 + 5, kind="ORDER_FILL_CONFIRMED"))

    expected = [e for e in journal.read_events() if e.timestamp >= since]
    assert [e.event_id for e in journal.read_events("ORDER_FILL_CONFIRMED", since=since)] == [
        e.event_id for e in expected if e.event_type == "ORDER_FILL_CONFIRMED"
    ]
    # `limit` means the newest N, as it does on the unbounded path.
    assert [e.event_id for e in journal.read_events(limit=3, since=since)] == [
        e.event_id for e in expected[-3:]
    ]


def test_a_windowed_record_read_equals_a_full_read_filtered(tmp_path):
    journal, since = _filled(tmp_path)
    everything = journal.read_all()
    expected = [record for record in everything if record.snapshot.timestamp >= since]
    assert expected

    assert [r.cycle_id for r in journal.read_all(since=since)] == [r.cycle_id for r in expected]


def test_a_window_that_spans_a_rotation_reads_both_generations(tmp_path):
    # A small cap rotates every few records, so the window straddles `.1` and `.jsonl`.
    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=4_000, backups=3)
    for index in range(120):
        journal.append_event(_event(index))
    assert len(journal.history_paths()) > 1, "the fixture must actually rotate"

    since = BASE + timedelta(minutes=60)
    expected = [e for e in journal.read_events() if e.timestamp >= since]
    assert expected
    assert [e.event_id for e in journal.read_events(since=since)] == [e.event_id for e in expected]


def test_a_torn_last_line_does_not_hide_the_records_before_it(tmp_path):
    journal, since = _filled(tmp_path, count=60)
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"event_id":"torn","event_type":"CURRIC')  # a hard kill mid-append

    expected = [e for e in journal.read_events() if e.timestamp >= since]
    assert expected
    assert [e.event_id for e in journal.read_events(since=since)] == [e.event_id for e in expected]


def test_an_unbounded_window_falls_back_instead_of_dropping_the_oldest_part(tmp_path, monkeypatch):
    """The fail-safe, stated as a property: when the tail cannot establish the bound inside the
    byte ceiling, the answer is still the full one. A window that quietly dropped its own oldest
    records would be a duplicate-order check reporting "nothing in flight" while an unconfirmed
    order sat just outside the bytes it read."""
    journal, _ = _filled(tmp_path, count=200)
    monkeypatch.setattr(journal_module, "_TAIL_BLOCK_BYTES", 2_000)
    monkeypatch.setattr(journal_module, "_TAIL_LIMIT_BYTES", 4_000)

    since = BASE - timedelta(days=1)  # older than everything, so the tail can never bound it
    expected = [e for e in journal.read_events() if e.timestamp >= since]
    assert expected and len(expected) == 200
    assert [e.event_id for e in journal.read_events(since=since)] == [e.event_id for e in expected]


def test_a_windowed_read_of_the_empty_journal_is_empty_not_an_error(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    assert journal.read_events(since=BASE) == []
    assert journal.read_all(since=BASE) == []
