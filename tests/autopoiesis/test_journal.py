from datetime import date, datetime, timezone

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


def test_daily_trade_count_survives_rotation(tmp_path):
    """The daily trade cap must count orders that rotation moved to a backup.

    This one is safety-relevant rather than bookkeeping. `count_submitted_on` is what
    Guardian's `max_trades_per_day` is measured against, and it read only the live journal
    file. A rotation partway through a trading day moves the day's orders to `journal.jsonl.1`
    and the counter returns 0 - so the 10-trades-per-day limit silently stops limiting for the
    rest of the day, with nothing in any record to explain why. The report would still say the
    limit is in force.

    On this host the rotation that exposed it fell pre-market, so today's count was correct
    by luck: 6 both before and after the fix. The next mid-session rotation would have
    removed the cap entirely.
    """
    day = date(2026, 6, 3)
    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=4096, backups=4)

    # Fewer records than an earlier version used, deliberately. `backups` is a retention
    # bound, not a guarantee: the fifth rotation deletes `.4`. With 24 records and 4 backups
    # the count came back 20 of 24 - which is the retention policy working correctly, and the
    # test was asserting on discarded history. The count now forces several rotations while
    # staying inside what is retained.
    total = 12
    submitted = 0
    for index in range(total):
        # Rebuilt rather than mutated: CycleRecord is a frozen pydantic model, so assigning
        # to its fields raises, and `model_copy(update=...)` on a nested field needs the parent
        # to be reconstructed too. make_record already takes the cycle_id.
        base = make_record(f"cycle-{index}")
        journal.append(
            CycleRecord(
                cycle_id=base.cycle_id,
                snapshot=base.snapshot.model_copy(update={
                    "timestamp": datetime(2026, 6, 3, 14, 30 + index, tzinfo=timezone.utc),
                }),
                decision=base.decision.model_copy(update={"action": "BUY", "quantity": 1}),
                guardian=base.guardian,
                execution=ExecutionResult(
                    status="SUBMITTED", order_id=f"order-{index}",
                    client_order_id=f"coid-{index}", filled_quantity=1.0,
                    filled_avg_price=100.0, fees=0.0, filled_at=None, submitted_at=None,
                    broker_status="accepted", message="submitted",
                ),
                strategy_id=base.strategy_id,
                error=base.error,
            )
        )
        submitted += 1
        if (index + 1) % 3 == 0:
            journal._rotate_if_needed(0)  # force a rotation every three records

    assert len(journal.history_paths()) > 1, "the test must actually rotate"
    assert journal.count_submitted_on(day) == submitted, (
        f"the daily trade cap counted {journal.count_submitted_on(day)} of {submitted} "
        f"submitted orders across generations "
        f"{[p.name for p in journal.history_paths()]}"
    )


def test_daily_trade_count_ignores_holds_and_other_days(tmp_path):
    """HOLDs, non-SUBMITTED statuses and other dates must not consume the daily cap."""
    from datetime import date as _date

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    target = _date(2026, 6, 3)

    def at(base, when, action, execution=None):
        updates = {"action": action}
        # CycleRecord validates that a BUY or SELL carries a positive quantity; HOLD needs
        # none. Set both rather than guessing, so the fixture obeys the same invariant the
        # agent does.
        updates["quantity"] = 0 if action == "HOLD" else 1
        return CycleRecord(
            cycle_id=base.cycle_id,
            snapshot=base.snapshot.model_copy(update={"timestamp": when}),
            decision=base.decision.model_copy(update=updates),
            guardian=base.guardian,
            execution=execution or base.execution,
            strategy_id=base.strategy_id,
            error=base.error,
        )

    accepted = ExecutionResult(
        status="SUBMITTED", order_id="o1", client_order_id="c1", filled_quantity=1.0,
        filled_avg_price=100.0, fees=0.0, filled_at=None, submitted_at=None,
        broker_status="accepted", message="submitted",
    )
    journal.append(at(make_record("hold"), datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc), "HOLD"))
    journal.append(at(make_record("yesterday"), datetime(2026, 6, 2, 14, 30, tzinfo=timezone.utc), "BUY"))
    journal.append(at(make_record("today"), datetime(2026, 6, 3, 14, 31, tzinfo=timezone.utc), "BUY", accepted))

    assert journal.count_submitted_on(target) == 1


def test_retained_window_reports_the_span_and_says_when_older_cycles_are_gone(tmp_path):
    """Rotation makes the journal a fragment, and nothing used to say so.

    `stats()` reports bytes and lines for the live file alone, and the doctor reported
    `len(records) cycles` as if it were a total. Several analyses are recomputed from the
    journal on every pass - calibration, the experiment chain, champion history - so past
    the retention horizon they returned a confident number computed from a fragment, which
    is worse than returning nothing: silently wrong rather than loudly absent.
    """
    from datetime import timedelta

    path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(path)
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for index in range(3):
        record = make_record(cycle_id=f"c{index}")
        journal.append(
            record.model_copy(
                update={
                    "snapshot": record.snapshot.model_copy(
                        update={
                            "timestamp": base + timedelta(hours=index),
                            "last_price": 100.0 + index,
                        }
                    )
                }
            )
        )

    fresh = journal.retained_window()
    assert fresh["cycles"] == 3
    assert fresh["oldest"][:10] == "2026-10-01"
    assert fresh["newest"][:10] == "2026-10-01"
    assert fresh["truncated"] is None, "with no known_since there is nothing to judge against"

    # Already running before the oldest retained cycle: the gap was rotated away, and the
    # reader has to say so rather than imply the window is the whole story.
    assert journal.retained_window(known_since=base - timedelta(days=1))["truncated"] is True

    # Already running only since the oldest retained cycle: nothing was lost.
    assert journal.retained_window(known_since=base)["truncated"] is False
