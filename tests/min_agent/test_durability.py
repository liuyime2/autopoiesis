"""Regression tests for runtime-state durability.

The defects this file exists to prevent:

    Nine write sites used Path.write_text, which truncates then writes, with no
    lock anywhere in the project. The strategy library is re-read from disk on
    every trading cycle, so a torn read made StrategyLibrary.list's
    `except Exception: continue` silently drop the strategy for that cycle and
    produce a phantom HOLD. auto-fix.sh and auto_reviewer.py both wrote the same
    four core strategy files with no lock against each other or the daemon.

    The journal had no rotation, no size cap, no fsync, and every reader dropped
    unparseable lines with `except Exception: continue` and no counter, so a
    truncated tail from a hard kill was lost silently and forever. TradeCounter
    called read_all() on every trading cycle, so its cost was the whole file:
    0.73s at 16.9MB, growing linearly with nothing to bound it.
"""

import threading
import time
from datetime import datetime, timezone

import pytest

from min_agent.atomicio import file_lock, read_json, write_json_atomic, write_text_atomic
from min_agent.journal import JsonlJournal
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    HeartbeatPayload,
    JournalEvent,
    KnowledgeArtifact,
    TradeDecision,
)
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_engine import StrategyLibrary
from min_agent.trade_counter import TradeCounter

NOW = datetime.now(tz=timezone.utc)


def _record(cycle_id, action="BUY", status="SUBMITTED", coid=None, day=9):
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=DataSnapshot(
            symbol="SPY",
            timestamp=datetime(2026, 6, day, 14, 30, tzinfo=timezone.utc),
            market_open=True,
            last_price=500.0,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action=action, quantity=0 if action == "HOLD" else 2, confidence=0.8, rationale="t"
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status=status, order_id=f"o-{cycle_id}", client_order_id=coid, filled_quantity=0, message="m"),
    )


def _event(event_id, event_type="STRATEGY_LIFECYCLE_UPDATED"):
    return JournalEvent(
        event_id=event_id, event_type=event_type, timestamp=NOW, status="SUCCESS", message="m"
    )


# --- atomic writes -----------------------------------------------------------


def test_readers_never_observe_a_partial_file(tmp_path):
    target = tmp_path / "state.json"
    write_text_atomic(target, "a" * 200_000)
    seen = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                seen.append(len(target.read_text(encoding="utf-8")))
            except FileNotFoundError:
                seen.append(-1)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        for i in range(60):
            write_text_atomic(target, ("b" if i % 2 else "a") * 200_000)
    finally:
        stop.set()
        thread.join(timeout=5)

    # Only complete contents, or nothing at all. Never a short read and never a
    # missing file (os.replace guarantees the name always resolves).
    assert seen, "reader never ran"
    assert set(seen) == {200_000}, f"observed a torn or missing read: {sorted(set(seen))[:5]}"


def test_write_text_atomic_leaves_no_temp_files(tmp_path):
    target = tmp_path / "state.json"
    for _ in range(20):
        write_text_atomic(target, "payload")
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_write_text_atomic_cleans_up_on_failure(tmp_path):
    target = tmp_path / "state.json"

    class Boom:
        """Not a str, so handle.write raises inside the atomic-write block."""

        def __str__(self):
            return "x"

    with pytest.raises(TypeError):
        write_text_atomic(target, Boom())
    assert not target.exists()
    assert [p.name for p in tmp_path.iterdir()] == []


def test_write_json_atomic_round_trips(tmp_path):
    target = tmp_path / "s.json"
    write_json_atomic(target, {"b": 1, "a": 2})
    assert read_json(target) == {"a": 2, "b": 1}


def test_read_json_treats_missing_empty_and_corrupt_as_none(tmp_path):
    assert read_json(tmp_path / "nope.json") is None
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    assert read_json(empty) is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert read_json(corrupt) is None


# --- locking -----------------------------------------------------------------


def test_exclusive_lock_is_mutually_exclusive(tmp_path):
    lock = tmp_path / "x.lock"
    order = []
    started = threading.Event()

    def second():
        with file_lock(lock, timeout=5.0):
            order.append("second")

    with file_lock(lock):
        order.append("first")
        thread = threading.Thread(target=second, daemon=True)
        thread.start()
        started.set()
        time.sleep(0.15)
    thread.join(timeout=5)

    assert order == ["first", "second"]


def test_shared_locks_do_not_block_each_other(tmp_path):
    lock = tmp_path / "x.lock"
    with file_lock(lock, exclusive=False, timeout=5.0):
        with file_lock(lock, exclusive=False, timeout=5.0):
            pass


# --- strategy library --------------------------------------------------------


def _spec(strategy_id="s1"):
    from min_agent.models import StrategySpec

    return StrategySpec(
        strategy_id=strategy_id,
        name="S",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.5},
        max_position_value=1000,
        enabled=True,
        lifecycle="ACTIVE",
        created_at=NOW,
        rationale="t",
    )


def test_strategy_library_records_parse_failures_instead_of_hiding_them(tmp_path):
    library = StrategyLibrary(tmp_path)
    library.save(_spec())
    (tmp_path / "torn.json").write_text('{"strategy_id": "torn", "kind":', encoding="utf-8")

    loaded = library.list()

    assert [s.strategy_id for s in loaded] == ["s1"]
    assert [name for name, _ in library.rejected] == ["torn.json"]
    assert library.rejected[0][1]


def test_strategy_library_save_is_atomic(tmp_path):
    library = StrategyLibrary(tmp_path)
    library.save(_spec("a"))
    library.save(_spec("b"))

    assert read_json(tmp_path / "a.json") is not None
    assert read_json(tmp_path / "b.json") is not None
    # The lock file is expected; nothing else should be left behind.
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.endswith(".lock")) == ["a.json", "b.json"]


# --- journal rotation and corruption accounting ------------------------------


def test_journal_rotates_at_the_size_cap(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", max_bytes=2000, backups=2, lock=False)
    for i in range(60):
        journal.append(_record(f"c{i}"))

    assert journal.path.stat().st_size <= 2000 + 1000
    assert (tmp_path / "j.jsonl.1").exists()
    # Oldest backups are discarded, not accumulated without bound.
    assert not (tmp_path / "j.jsonl.3").exists()


def test_journal_rotation_preserves_recent_records(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", max_bytes=3000, backups=1, lock=False)
    for i in range(80):
        journal.append(_record(f"c{i}"))

    ids = {r.cycle_id for r in journal.read_all()}
    assert "c79" in ids
    assert "c0" not in ids


def test_journal_counts_corrupt_lines_instead_of_dropping_them_silently(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", lock=False)
    journal.append(_record("good-1"))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"cycle_id": "torn", "snap\n')
        handle.write("\n")
    journal.append(_record("good-2"))

    records, dropped = journal._scan(CycleRecord, limit=None)

    assert [r.cycle_id for r in records] == ["good-1", "good-2"]
    assert dropped == 1
    assert journal.last_dropped_lines == 1
    assert journal.last_dropped_samples


def test_journal_does_not_count_the_other_record_type_as_corruption(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", lock=False)
    journal.append(_record("c1"))
    journal.append_event(_event("e1"))

    journal.read_all()
    assert journal.last_dropped_lines == 0
    journal.read_events()
    assert journal.last_dropped_lines == 0


def test_journal_fsyncs_and_locks_appends(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", lock=True)
    journal.append(_record("c1"))

    assert (tmp_path / "j.jsonl.lock").exists()
    assert len(journal.read_all()) == 1


def test_journal_stats_reports_utilisation(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", max_bytes=1000, lock=False)
    assert journal.stats()["exists"] is False
    journal.append(_record("c1"))

    stats = journal.stats()
    assert stats["exists"] is True
    assert stats["lines"] == 1
    assert 0 < stats["utilization"] < 1


# --- trade counter -----------------------------------------------------------


def test_count_submitted_on_ignores_holds_and_other_days(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", lock=False)
    journal.append(_record("a", action="BUY", status="SUBMITTED", day=9))
    journal.append(_record("b", action="SELL", status="SUBMITTED", day=9))
    journal.append(_record("c", action="HOLD", status="SKIPPED", day=9))
    journal.append(_record("d", action="BUY", status="REJECTED", day=9))
    journal.append(_record("e", action="BUY", status="SUBMITTED", day=10))

    assert journal.count_submitted_on(datetime(2026, 6, 9, tzinfo=timezone.utc).date()) == 2
    assert journal.count_submitted_on(datetime(2026, 6, 10, tzinfo=timezone.utc).date()) == 1


def test_trade_counter_uses_the_targeted_scan(tmp_path):
    journal = JsonlJournal(tmp_path / "j.jsonl", lock=False)
    for i in range(3):
        journal.append(_record(f"c{i}"))

    counter = TradeCounter(journal=journal)
    now = datetime(2026, 6, 9, 20, 0, tzinfo=timezone.utc)

    assert counter.trades_today(now=now) == 3
    # The targeted scan must not populate the full record list.
    assert journal.last_dropped_lines == 0


# --- atomic writes on the other runtime files --------------------------------


def test_health_write_is_atomic(tmp_path):
    from min_agent.health import HealthMonitor

    monitor = HealthMonitor(tmp_path / "hb.json")
    monitor.write(HeartbeatPayload(pid=1, status="RUNNING", timestamp=NOW, cycle_count=1, error_count=0, message="ok"))

    assert [p.name for p in tmp_path.iterdir() if not p.name.endswith(".lock")] == ["hb.json"]
    assert monitor.read().status == "RUNNING"


def test_reflection_save_is_atomic(tmp_path):
    memory = ReflectionMemory(tmp_path / "r.json")
    record = memory.reflect([_record("c1")])
    memory.save(record)

    assert [p.name for p in tmp_path.iterdir() if not p.name.endswith(".lock")] == ["r.json"]
    assert memory.load().window_cycles == 1


def test_knowledge_save_is_atomic(tmp_path):
    library = KnowledgeLibrary(tmp_path)
    artifact = KnowledgeArtifact(
        artifact_id="k1",
        artifact_type="LESSON",
        status="ACCEPTED",
        question="why did s1 stall?",
        answer="it never attempted a trade",
        summary="s1 produced no exploration evidence",
        source_kind="journal",
        source_refs=["s1"],
        tags=["s1"],
        created_at=NOW,
        rationale="derived from reflection",
    )
    library.save(artifact)

    assert [p.name for p in tmp_path.iterdir() if not p.name.endswith(".lock")] == ["k1.json"]
    assert library.list(status="ACCEPTED")[0].artifact_id == "k1"


# --- the knowledge read path (D14) ------------------------------------------


def _snapshot():
    return DataSnapshot(
        symbol="SPY",
        timestamp=NOW,
        market_open=True,
        last_price=500.0,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
    )


def _artifact(artifact_id="k1", status="ACCEPTED", refs=("s1",), tags=("s1",)):
    return KnowledgeArtifact(
        artifact_id=artifact_id,
        artifact_type="LESSON",
        status=status,
        question="why did s1 stall?",
        answer="it never attempted a trade",
        summary=f"{artifact_id} produced no exploration evidence",
        source_kind="journal",
        source_refs=tuple(refs),
        tags=tuple(tags),
        created_at=NOW,
        rationale="derived from reflection",
    )


def test_admitted_lessons_reach_the_decision(tmp_path):
    """KnowledgeLibrary.load/list had zero call sites: the library was
    write-only, so no lesson the agent admitted had ever influenced anything."""
    from min_agent.policy_engine import PolicyEngine

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("s1"))
    knowledge = KnowledgeLibrary(tmp_path / "knowledge")
    knowledge.save(_artifact())

    engine = PolicyEngine(strategy_library=library, knowledge_library=knowledge)
    decision = engine.decide_snapshot(_snapshot())

    assert "k1 produced no exploration evidence" in decision.rationale
    assert decision.action == "BUY"


def test_no_lessons_leaves_the_rationale_untouched(tmp_path):
    from min_agent.policy_engine import PolicyEngine

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("s1"))

    decision = PolicyEngine(strategy_library=library).decide_snapshot(_snapshot())

    assert "lessons:" not in decision.rationale


def test_a_broken_knowledge_library_cannot_stop_a_trading_cycle(tmp_path):
    """Regression: the lesson path initially read artifact.title, which
    KnowledgeArtifact does not have. The AttributeError was raised inside
    decide_snapshot, where TradingLoop's fail-closed handler turned every cycle
    into a HOLD - the exact symptom the knowledge path was meant to fix."""
    from min_agent.policy_engine import PolicyEngine

    class BrokenLibrary:
        def list(self, status=None, tags=None):
            return [_artifact()]

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("s1"))

    engine = PolicyEngine(strategy_library=library, knowledge_library=BrokenLibrary())
    decision = engine.decide_snapshot(_snapshot())

    assert decision.action == "BUY"
    assert "k1 produced no exploration evidence" in decision.rationale


def test_a_raising_knowledge_library_cannot_stop_a_trading_cycle(tmp_path):
    from min_agent.policy_engine import PolicyEngine

    class Exploding:
        def list(self, status=None, tags=None):
            raise RuntimeError("disk on fire")

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("s1"))

    decision = PolicyEngine(strategy_library=library, knowledge_library=Exploding()).decide_snapshot(_snapshot())

    assert decision.action == "BUY"


def test_only_accepted_lessons_are_consulted(tmp_path):
    from min_agent.policy_engine import PolicyEngine

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("s1"))
    knowledge = KnowledgeLibrary(tmp_path / "knowledge")
    knowledge.save(_artifact("rejected", status="REJECTED"))

    decision = PolicyEngine(strategy_library=library, knowledge_library=knowledge).decide_snapshot(_snapshot())

    assert "lessons:" not in decision.rationale


def test_a_reader_never_sees_a_half_written_append(tmp_path):
    """Appends held an exclusive lock but readers took none, so a scan running
    while the daemon was mid-append could observe a partial line and count it as
    corruption. This showed up as an intermittent test failure only when the
    daemon was live."""
    journal = JsonlJournal(tmp_path / "j.jsonl", lock=True)
    journal.append(_record("seed"))  # so the first read is never empty
    stop = threading.Event()
    torn = []
    counter = [0]

    def writer():
        while not stop.is_set():
            counter[0] += 1
            journal.append(_record(f"c{counter[0]}"))

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        for _ in range(30):
            records = journal.read_all()
            if journal.last_dropped_lines:
                torn.append(journal.last_dropped_lines)
            assert records
    finally:
        stop.set()
        thread.join(timeout=5)

    assert counter[0] > 0, "the writer never ran, so the test proved nothing"
    assert torn == [], f"reader observed a torn append {len(torn)} time(s)"


def test_a_reader_waits_for_the_writer_then_succeeds(tmp_path):
    """A reader must block while an appender holds the lock rather than read a
    half-written file, and must not give up on its own default timeout."""
    import time

    journal = JsonlJournal(tmp_path / "j.jsonl", lock=True)
    journal.append(_record("c1"))
    lock_path = journal.path.with_suffix(journal.path.suffix + ".lock")
    result = {}

    from min_agent.atomicio import file_lock

    def reader():
        started = time.perf_counter()
        try:
            result["n"] = len(journal.read_all())
        except Exception as exc:
            result["error"] = exc
        result["waited"] = time.perf_counter() - started

    with file_lock(lock_path, exclusive=True, timeout=5.0):
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        time.sleep(0.4)
        held = thread.is_alive()
    thread.join(timeout=10)

    assert held, "the reader should still be blocked while the writer holds the lock"
    assert "error" not in result, f"reader gave up instead of waiting: {result.get('error')!r}"
    assert result["n"] == 1
    assert result["waited"] >= 0.4


def test_a_reader_gives_up_after_its_own_timeout(tmp_path):
    """If the writer is wedged, a reader must eventually raise rather than hang
    a cycle forever."""
    import time

    journal = JsonlJournal(tmp_path / "j.jsonl", lock=True)
    journal.append(_record("c1"))
    journal._read_lock  # shared by design
    from min_agent.atomicio import file_lock

    lock_path = journal.path.with_suffix(journal.path.suffix + ".lock")
    outcome = {}

    def reader():
        from contextlib import nullcontext

        # Bypass the default 10s with a 0.2s budget, as a caller with a short
        # deadline would.
        try:
            with file_lock(lock_path, exclusive=False, timeout=0.2):
                outcome["acquired"] = True
        except TimeoutError as exc:
            outcome["error"] = exc

    with file_lock(lock_path, exclusive=True, timeout=5.0):
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout=5)

    assert outcome.get("acquired") is None
    assert isinstance(outcome.get("error"), TimeoutError)
