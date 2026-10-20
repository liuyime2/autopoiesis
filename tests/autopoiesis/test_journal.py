from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest

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


def test_reader_discovers_generations_after_locking(tmp_path, monkeypatch):
    """Synthetic rotation at lock acquisition must not hide the previously live cycle."""
    path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(path)
    journal.append(make_record("before"))

    @contextmanager
    def rotate_then_lock(**kwargs):
        path.rename(tmp_path / "journal.jsonl.1")
        path.write_text(make_record("after").model_dump_json() + "\n")
        yield

    monkeypatch.setattr(journal, "_read_lock", rotate_then_lock)
    assert [record.cycle_id for record in journal.read_all()] == ["before", "after"]


def test_the_read_lock_is_released_before_records_are_parsed(tmp_path, monkeypatch):
    """A shared lock held for the length of a parse starves the writer.

    Measured 2026-10-20 on the live journal: `_parsed` held the shared lock through every pydantic
    validation of 25,271 events and 3,892 cycles. `file_lock` gives a writer ten seconds; the parse
    took longer, so the daemon recorded "TimeoutError: could not acquire lock", backed off with a
    BACKING_OFF heartbeat, and the readiness check that was verifying the session then reported the
    daemon as not running - the check broke the thing it was checking.
    """
    from autopoiesis import journal as journal_module

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(make_event())

    held = {"now": False}
    parsed_while_held: list[str] = []

    @contextmanager
    def tracking_lock(*, exclusive: bool = False):
        held["now"] = True
        try:
            yield
        finally:
            held["now"] = False

    original = journal_module.JournalEvent.model_validate_json

    @classmethod
    def watching(cls, *args, **kwargs):
        if held["now"]:
            parsed_while_held.append(str(args[0])[:40])
        return original(*args, **kwargs)

    monkeypatch.setattr(journal, "_read_lock", tracking_lock)
    monkeypatch.setattr(journal_module.JournalEvent, "model_validate_json", watching)

    events = journal.read_events()

    assert [event.event_type for event in events] == ["CURRICULUM_PROPOSED"]
    assert not parsed_while_held, (
        "a record was parsed while the shared lock was held, and that is what blocks the daemon's "
        "append for the length of a full scan"
    )


def test_a_writer_appends_while_a_reader_is_parsing(tmp_path, monkeypatch):
    """The property the parse-outside-the-lock change exists for, tested at the worst moment.

    Measured 2026-10-20: a readiness check scanning 3,892 cycles held the shared lock through every
    pydantic validation, so the daemon could not take the exclusive lock within its ten seconds and
    recorded `TimeoutError: could not acquire lock`. Here the append is attempted from inside a record
    validator - if the lock were still held, it would block for its timeout and raise.
    """
    from autopoiesis import journal as journal_module

    path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(path)
    journal.append(make_event())
    writer = JsonlJournal(path)

    real_lock = journal_module.file_lock

    @contextmanager
    def short_lock(lock_path, *, exclusive=True, timeout=10.0):
        # One second for a writer: long enough for a released lock, short enough that a held one
        # fails this test in a second rather than ten.
        with real_lock(lock_path, exclusive=exclusive, timeout=1.0):
            yield

    monkeypatch.setattr(journal_module, "file_lock", short_lock)

    outcome: dict[str, str] = {}
    original = journal_module.JournalEvent.model_validate_json

    @classmethod
    def appending(cls, *args, **kwargs):
        if not outcome:
            try:
                writer.append_event(make_event("CURRICULUM_FAILED"))
                outcome["append"] = "ok"
            except Exception as exc:  # the failure IS the finding, so it is reported not raised
                outcome["append"] = f"{type(exc).__name__}: {exc}"
        return original(*args, **kwargs)

    monkeypatch.setattr(journal_module.JournalEvent, "model_validate_json", appending)

    journal.read_events()

    assert outcome.get("append") == "ok", (
        f"an append attempted while a reader was parsing did not succeed: {outcome}. "
        "That is the read lock being held across the parse, which starves the daemon"
    )
    assert len(journal.read_events()) == 2


def test_a_held_lock_fails_loudly_instead_of_hanging(tmp_path):
    """A writer that cannot get the lock must say so, not wait forever.

    The daemon appends under this lock; a silent hang there is a stalled cycle, and a timeout with the
    lock path in it is what the 2026-10-20 diagnosis was built on - the heartbeat carried
    `TimeoutError: could not acquire lock on .../journal.jsonl.lock`, which is why the cause was
    findable at all.
    """
    from autopoiesis.atomicio import file_lock

    lock_path = tmp_path / "x.lock"
    with file_lock(lock_path, exclusive=True), pytest.raises(TimeoutError) as caught:
        with file_lock(lock_path, exclusive=True, timeout=0.2):
            pass
    assert str(lock_path.name) in str(caught.value), "the message must name the lock that could not be taken"


def test_daily_attempt_counter_discovers_generations_after_locking(tmp_path, monkeypatch):
    """Synthetic submitted orders before and after rotation count toward the same hard cap."""
    path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(path)
    record = make_record("before").model_copy(update={
        "decision": TradeDecision(symbol="SPY", action="BUY", quantity=1,
                                  confidence=0.6, rationale="synthetic"),
        "execution": ExecutionResult(status="SUBMITTED", order_id="order-before",
                                     filled_quantity=0, message="synthetic"),
    })
    journal.append(record)

    @contextmanager
    def rotate_then_lock(**kwargs):
        path.rename(tmp_path / "journal.jsonl.1")
        after = record.model_copy(update={
            "cycle_id": "after",
            "execution": record.execution.model_copy(update={"order_id": "order-after"}),
        })
        path.write_text(after.model_dump_json() + "\n")
        yield

    monkeypatch.setattr(journal, "_read_lock", rotate_then_lock)
    assert journal.count_submitted_on(date(2026, 6, 3)) == 2


def test_reader_retention_setting_does_not_hide_actual_generations(tmp_path):
    """Synthetic imported generations remain readable beyond the writer's retention budget."""
    path = tmp_path / "journal.jsonl"
    path.write_text(make_record("newest").model_dump_json() + "\n")
    (tmp_path / "journal.jsonl.21").write_text(make_record("oldest").model_dump_json() + "\n")
    assert [record.cycle_id for record in JsonlJournal(path, backups=1).read_all()] == ["oldest", "newest"]


def test_deep_generation_is_not_attested_as_an_empty_fresh_install(tmp_path):
    """Synthetic imported history cannot gain a fabricated empty-journal baseline."""
    (tmp_path / "journal.jsonl.21").write_text(make_record("oldest").model_dump_json() + "\n")
    status = JsonlJournal(tmp_path / "journal.jsonl", backups=1).archive_manifest_status()
    assert not status["trusted"]


@pytest.mark.parametrize("backups", [0, 1, 3])
def test_deep_imported_generations_keep_their_order_after_rotation(tmp_path, backups):
    """Synthetic .21 is older than .3 and must enter the archive first."""
    path = tmp_path / "journal.jsonl"
    generations = [(21, "oldest"), (3, "third"), (2, "second"), (1, "first")]
    for index, cycle_id in generations:
        (tmp_path / f"journal.jsonl.{index}").write_text(make_record(cycle_id).model_dump_json() + "\n")
    path.write_text(make_record("live").model_dump_json() + "\n")
    journal = JsonlJournal(path, max_bytes=1, backups=backups)
    before = [r.cycle_id for r in journal.read_all()]
    journal.append(make_record("after"))
    assert [r.cycle_id for r in journal.read_all()] == [*before, "after"]
    assert not (tmp_path / "journal.jsonl.21").exists()
    assert journal.archive_manifest_status()["valid"]


def test_gapped_deep_import_refuses_rotation_before_moving_history(tmp_path):
    """Synthetic incomplete retained layout needs operator repair, not chronology guesses."""
    paths = [tmp_path / name for name in ("journal.jsonl.21", "journal.jsonl.3", "journal.jsonl")]
    for number, path in enumerate(paths):
        path.write_text(make_record(f"existing-{number}").model_dump_json() + "\n")
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError, match="not contiguous"):
        JsonlJournal(paths[-1], max_bytes=1).append(make_record("refused"))
    assert {path: path.read_bytes() for path in paths} == before
    assert not list((tmp_path / "journal-archive").glob("*.jsonl"))


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


def test_daily_attempt_cap_counts_intents_without_cycle_and_deduplicates_cycle_projection(tmp_path):
    day = date(2026, 6, 3)
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    base = make_record("cycle-attempt")
    submitted = CycleRecord(
        cycle_id=base.cycle_id,
        snapshot=base.snapshot,
        decision=base.decision.model_copy(update={"action": "BUY", "quantity": 1}),
        guardian=base.guardian,
        execution=ExecutionResult(
            status="SUBMITTED", order_id="oid-1", client_order_id="cid-1",
            filled_quantity=0, message="submitted",
        ),
    )
    journal.append_event(JournalEvent(
        event_id="intent-cid-1", event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime(2026, 6, 3, 14, 31, tzinfo=timezone.utc), status="ACCEPTED",
        payload={"client_order_id": "cid-1", "symbol": "SPY", "action": "BUY", "quantity": 1},
    ))
    journal.append(submitted)
    journal.append_event(JournalEvent(
        event_id="intent-cid-2", event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime(2026, 6, 3, 14, 32, tzinfo=timezone.utc), status="ACCEPTED",
        payload={"client_order_id": "cid-2", "symbol": "QQQ", "action": "BUY", "quantity": 1},
    ))

    assert journal.count_submitted_on(day) == 2


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


def test_a_rotation_past_the_retention_horizon_archives_instead_of_deleting(tmp_path):
    """The generation leaving the rotation horizon is the beginning of the whole record.

    Rotation at 64 MiB x 3 holds about ten days of this host's record; the rotation after that
    unlinked the oldest generation, and every derived figure - model calibration, the experiment
    chain, the strategy ledger - silently recomputed itself from the fragment left behind.
    `retained_window` named the truncation only after it had happened.

    So past the horizon, the data must still be readable, and it must be readable *through the
    journal's own readers* - which is the only reason the archive counts. A reader that ignored
    the archive would reintroduce the defect that `history_paths` exists to prevent, in a place
    where nothing shows it: the record would shrink while the doctor kept reporting a window.
    """
    cap = 4096
    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=cap, backups=2)
    sample = make_record("cycle-0")
    per_record = len(sample.model_dump_json().encode("utf-8")) + 1
    per_generation = max(1, cap // per_record)

    # Keep appending until the rotation horizon is actually crossed, rather than assuming how
    # many records fill a generation. The horizon is three rotations with `backups=2`: the
    # first two shift `.1` to `.2` and create the backup set, the third is the one that used
    # to unlink the oldest generation and delete the beginning of the record.
    appended = 0
    for generation in range(per_generation * 3 + 1):
        for _ in range(per_generation):
            journal.append(make_record(f"cycle-{appended}"))
            appended += 1

    archived = [p for p in journal.history_paths() if p.parent.name == "journal-archive"]
    assert archived, (
        f"crossed the retention horizon ({appended} records, {per_generation} per generation) "
        "and nothing was archived"
    )

    records = journal.read_all()
    assert len(records) == appended, (
        f"past the retention horizon the journal lost records: wrote {appended}, "
        f"read {len(records)}"
    )
    assert [r.cycle_id for r in records] == [f"cycle-{i}" for i in range(appended)], (
        "the archived generation must be read first, so the original append order survives"
    )
    assert sum(p.stat().st_size for p in archived) == sum(
        p.stat().st_size for p in (tmp_path / "journal-archive").glob("*.jsonl")
    )


def test_an_unwritable_archive_fails_the_append_rather_than_deleting_the_history(tmp_path):
    """A rotation that cannot preserve what it is rotating must stop, not discard.

    The tempting implementation catches the archive error and unlinks the generation instead -
    "keep trading". That converts a loud "the disk is full" into a quiet "forty percent of the
    record is gone", and it is unrecoverable: the history exists nowhere, and nothing downstream
    can tell the difference between a short record and a destroyed one. A daemon that stops
    appending is a page its operator can read; a daemon that destroys its own history to keep
    running is not recoverable at all.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=200, backups=1)
    journal.append(make_record("cycle-0"))
    # The rotation horizon, crossed on the next append, with the archive directory unusable.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    journal.archive_dir = blocker

    with pytest.raises((OSError, ValueError)):
        for index in range(1, 60):
            journal.append(make_record(f"cycle-{index}"))

    # Whatever happened, the history must not have been destroyed on the way through.
    survivors = [p for p in journal.history_paths() if p.stat().st_size > 0]
    assert survivors, "the append failed and the record went with it"


def test_archive_manifest_requires_attestation_for_existing_history(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(make_record("cycle-existing"))
    status = journal.archive_manifest_status()
    assert status["valid"] is False
    assert status["trusted"] is False

    with pytest.raises(ValueError, match="attestation"):
        journal.attest_archive_baseline(operator_id=" ", evidence_reference="", reason=" ")
    journal.attest_archive_baseline(
        operator_id="test-operator",
        evidence_reference="test-evidence-1",
        reason="verified current runtime journal and archive set",
    )
    status = journal.archive_manifest_status()
    assert status["valid"] is True
    assert status["trusted"] is True


def test_fresh_empty_journal_persists_explicit_empty_baseline(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    status = journal.archive_manifest_status()

    assert status["valid"] is True
    assert status["trusted"] is True
    assert journal.archive_manifest_path.exists()
    journal.append(make_record("cycle-first"))
    assert journal.archive_manifest_status()["trusted"] is True


def test_archive_manifest_tracks_rotation_and_detects_deleted_or_changed_archive(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=200, backups=1)
    journal.append(make_record("cycle-0"))
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="test-evidence-2", reason="verified fresh test journal"
    )
    journal.append(make_record("cycle-1"))
    journal.append(make_record("cycle-2"))

    archives = sorted(journal.archive_dir.glob("r*.jsonl"))
    assert archives
    assert journal.archive_manifest_status()["valid"] is True
    original = archives[0].read_bytes()
    archives[0].write_bytes(b"X" + original[1:])
    status = journal.archive_manifest_status()
    assert status["valid"] is False
    assert "checksum" in status["reason"]
    with pytest.raises(ValueError, match="restore evidence"):
        journal.attest_archive_baseline(
            operator_id="test-operator", evidence_reference="repair-attempt", reason="do not hide corruption"
        )


def test_archive_manifest_detects_missing_generation(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=200, backups=1)
    journal.append(make_record("cycle-0"))
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="test-evidence-3", reason="verified fresh test journal"
    )
    for index in range(1, 6):
        journal.append(make_record(f"cycle-{index}"))

    archives = sorted(journal.archive_dir.glob("r*.jsonl"))
    assert len(archives) >= 2
    archives[0].unlink()
    status = journal.archive_manifest_status()
    assert status["valid"] is False
    assert "missing" in status["reason"]


def test_rotation_manifest_write_failure_leaves_history_fail_closed(tmp_path, monkeypatch):
    import autopoiesis.journal as journal_module

    journal = JsonlJournal(tmp_path / "journal.jsonl", max_bytes=200, backups=1)
    journal.append(make_record("cycle-0"))
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="test-evidence-4", reason="verified fresh test journal"
    )
    journal.append(make_record("cycle-1"))

    def fail_manifest_write(path, payload, *, indent=2):
        if path == journal.archive_manifest_path:
            raise OSError("simulated manifest persistence failure")
        return original_write(path, payload, indent=indent)

    original_write = journal_module.write_json_atomic
    monkeypatch.setattr(journal_module, "write_json_atomic", fail_manifest_write)
    with pytest.raises(OSError, match="manifest persistence"):
        journal.append(make_record("cycle-2"))

    status = journal.archive_manifest_status()
    assert status["valid"] is False
    assert "unmanifested" in status["reason"]
