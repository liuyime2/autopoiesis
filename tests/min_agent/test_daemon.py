import os
from dataclasses import replace
from datetime import datetime, timezone

from min_agent.config import AgentConfig
from min_agent.curriculum import StructuredCurriculumAgent
from min_agent.daemon import AgentDaemon, DaemonAlreadyRunningError
from min_agent.guardian import Guardian
from min_agent.health import HealthMonitor
from min_agent.journal import JsonlJournal
from min_agent.knowledge_admission import KnowledgeAdmission
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import AccountSnapshot, BrokerEvidenceBatch, CycleRecord, DataSnapshot, ExecutionResult, GuardianResult, PortfolioHistoryPoint, StrategySpec, TradeDecision
from min_agent.order_reconciler import ReconciliationReport
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_admission import StrategyAdmission
from min_agent.strategy_engine import StrategyLibrary, StrategyLifecycleManager


def config(tmp_path, *, symbols=("SPY",), interval=1):
    return AgentConfig(
        mode="paper",
        symbols=symbols,
        allowlist=frozenset(symbols),
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        alpaca_base_url="https://paper-api.alpaca.markets",
        ollama_base_url="http://localhost:11434",
        model="deepseek-r1:8b",
        gpu_devices="0",
        journal_path=tmp_path / "journal.jsonl",
        max_position_value=1000,
        max_daily_loss=500,
        daemon_interval_seconds=interval,
        max_trades_per_day=10,
        max_daily_cycles=100,
        heartbeat_path=tmp_path / "heartbeat.json",
        pidfile_path=tmp_path / "daemon.pid",
        strategy_dir=tmp_path / "strategies",
        reflection_window=10,
        curriculum_enabled=False,
    )


class FakeLoop:
    def __init__(self, journal=None, strategy_id=None, action="HOLD", quantity=0, status="SKIPPED", confidence=0.1):
        self.symbols = []
        self.journal = journal
        self.strategy_id = strategy_id
        self.action = action
        self.quantity = quantity
        self.status = status
        self.confidence = confidence

    def run_once(self, symbol):
        self.symbols.append(symbol)
        snapshot = DataSnapshot(
            symbol=symbol,
            timestamp=datetime.now(tz=timezone.utc),
            market_open=True,
            last_price=100,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
            ),
        )
        record = CycleRecord(
            cycle_id=f"cycle-{len(self.symbols)}",
            snapshot=snapshot,
            decision=TradeDecision(symbol=symbol, action=self.action, quantity=self.quantity, confidence=self.confidence, rationale="test", strategy_id=self.strategy_id),
            guardian=GuardianResult(approved=True, reason="approved"),
            execution=ExecutionResult(status=self.status, order_id=None, filled_quantity=0, message="test"),
            strategy_id=self.strategy_id,
        )
        if self.journal is not None:
            self.journal.append(record)
        return record


class FailingLoop:
    def run_once(self, symbol):
        raise RuntimeError("boom")


class FailingReflectionMemory(ReflectionMemory):
    def reflect(self, records, evidence=None, fills=None):
        raise RuntimeError("reflection boom")


class FailingCurriculum:
    def propose(self, **kwargs):
        raise RuntimeError("curriculum boom")


class CapturingCurriculum:
    def __init__(self):
        self.kwargs = None

    def propose(self, **kwargs):
        self.kwargs = kwargs
        return StructuredCurriculumAgent().propose(**kwargs)


class FakeScheduler:
    def __init__(self, open_: bool):
        self.open = open_

    def should_trade_now(self):
        return self.open


class FakeEvidenceProvider:
    def __init__(self):
        self.calls = 0

    def ingest(self, *, window_start, window_end):
        self.calls += 1
        return BrokerEvidenceBatch(
            generated_at=window_end,
            window_start=window_start,
            window_end=window_end,
            portfolio_history=(
                PortfolioHistoryPoint(
                    timestamp=window_end,
                    equity=100_500,
                    profit_loss=500,
                    profit_loss_pct=0.005,
                ),
            ),
        )


class FakeReconciler:
    def __init__(self, matched: bool):
        self.matched = matched
        self.calls = 0

    def reconcile(self):
        self.calls += 1
        return ReconciliationReport(
            matched=self.matched,
            submitted_order_ids=(),
            broker_open_order_ids=("broker-extra",) if not self.matched else (),
            extra_broker_orders=("broker-extra",) if not self.matched else (),
            message="matched" if self.matched else "broker has open orders not found in journal",
        )


def test_daemon_runs_cycles_and_writes_heartbeat(tmp_path):
    cfg = config(tmp_path, symbols=("SPY", "QQQ"), interval=0)
    loop = FakeLoop()
    daemon = AgentDaemon(config=cfg, loop=loop, sleep=lambda seconds: None)

    exit_code = daemon.run(max_cycles=2)

    assert exit_code == 0
    assert loop.symbols == ["SPY", "QQQ"]
    assert not cfg.pidfile_path.exists()
    heartbeat = HealthMonitor(cfg.heartbeat_path).read()
    assert heartbeat is not None
    assert heartbeat.status == "STOPPED"
    assert heartbeat.cycle_count == 2


def test_daemon_prevents_double_start(tmp_path):
    cfg = config(tmp_path)
    cfg.pidfile_path.write_text(str(os.getpid()), encoding="utf-8")
    daemon = AgentDaemon(config=cfg, loop=FakeLoop(), sleep=lambda seconds: None)

    try:
        daemon.run(max_cycles=1)
    except DaemonAlreadyRunningError:
        pass
    else:
        raise AssertionError("expected DaemonAlreadyRunningError")


def test_daemon_backoff_on_cycle_error(tmp_path):
    cfg = config(tmp_path, interval=0)
    daemon = AgentDaemon(config=cfg, loop=FailingLoop(), sleep=lambda seconds: None)

    daemon.run(max_cycles=1)

    heartbeat = HealthMonitor(cfg.heartbeat_path).read()
    assert heartbeat is not None
    assert heartbeat.error_count >= 1


def test_daemon_daily_cycle_limit_resets_on_new_day(tmp_path):
    cfg = replace(config(tmp_path, interval=0), max_daily_cycles=1)
    loop = FakeLoop()
    dates = [
        datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
    ]

    def now():
        return dates[-1]

    def sleep(seconds):
        dates.append(datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc))

    daemon = AgentDaemon(config=cfg, loop=loop, sleep=sleep, now=now)

    daemon.run(max_cycles=2)

    assert loop.symbols == ["SPY", "SPY"]
    assert daemon.cycle_count == 2
    assert daemon.daily_cycle_count == 1


def test_daemon_market_closed_does_not_consume_cycle(tmp_path):
    cfg = config(tmp_path, interval=0)
    loop = FakeLoop()
    daemon = AgentDaemon(config=cfg, loop=loop, sleep=lambda seconds: None, scheduler=FakeScheduler(False))

    daemon.run(max_cycles=1)

    assert loop.symbols == []
    assert daemon.cycle_count == 0
    assert daemon.daily_cycle_count == 0


def test_daemon_market_closed_runs_maintenance_without_trading(tmp_path):
    cfg = replace(config(tmp_path, interval=0), maintenance_interval_seconds=1, evidence_interval_seconds=1, reflection_interval_seconds=1)
    journal = JsonlJournal(cfg.journal_path)
    evidence_provider = FakeEvidenceProvider()
    reflection_memory = ReflectionMemory(tmp_path / "reflection.json")
    daemon = AgentDaemon(
        config=cfg,
        loop=FakeLoop(journal=journal),
        sleep=lambda seconds: None,
        scheduler=FakeScheduler(False),
        journal=journal,
        broker_evidence_provider=evidence_provider,
        reflection_memory=reflection_memory,
    )

    daemon.run(max_cycles=1)

    assert daemon.loop.symbols == []
    assert evidence_provider.calls == 1
    event_types = [event.event_type for event in journal.read_events()]
    assert "BROKER_EVIDENCE_INGESTED" in event_types
    assert "PNL_EVIDENCE_RECORDED" in event_types
    assert "PROFIT_TARGET_CHECKED" in event_types
    assert reflection_memory.load().evaluation["pnl_evidence"] == "broker_portfolio_history_verified"


def test_daemon_market_open_runs_cycle(tmp_path):
    cfg = config(tmp_path, interval=0)
    loop = FakeLoop()
    daemon = AgentDaemon(config=cfg, loop=loop, sleep=lambda seconds: None, scheduler=FakeScheduler(True))

    daemon.run(max_cycles=1)

    assert loop.symbols == ["SPY"]
    assert daemon.cycle_count == 1


def test_daemon_startup_reconciliation_failure_stops_before_cycle(tmp_path):
    cfg = config(tmp_path, interval=0)
    journal = JsonlJournal(cfg.journal_path)
    reconciler = FakeReconciler(False)
    loop = FakeLoop(journal=journal)
    daemon = AgentDaemon(
        config=cfg,
        loop=loop,
        sleep=lambda seconds: None,
        journal=journal,
        startup_reconciler=reconciler,
    )

    exit_code = daemon.run(max_cycles=1)

    assert exit_code == 1
    assert reconciler.calls == 1
    assert loop.symbols == []
    assert journal.read_events("ORDER_RECONCILIATION_REVIEWED")[0].status == "FAILED"


def test_daemon_startup_reconciliation_success_allows_cycle(tmp_path):
    cfg = config(tmp_path, interval=0)
    journal = JsonlJournal(cfg.journal_path)
    reconciler = FakeReconciler(True)
    loop = FakeLoop(journal=journal)
    daemon = AgentDaemon(
        config=cfg,
        loop=loop,
        sleep=lambda seconds: None,
        journal=journal,
        startup_reconciler=reconciler,
    )

    exit_code = daemon.run(max_cycles=1)

    assert exit_code == 0
    assert loop.symbols == ["SPY"]
    assert journal.read_events("ORDER_RECONCILIATION_REVIEWED")[0].status == "SUCCESS"


def make_daemon_with_learning(tmp_path, *, curriculum_agent=None, reflection_memory=None):
    cfg = config(tmp_path, interval=0)
    journal = JsonlJournal(cfg.journal_path)
    reflection_memory = reflection_memory or ReflectionMemory(tmp_path / "reflection.json")
    strategy_library = StrategyLibrary(cfg.strategy_dir)
    loop = FakeLoop(journal=journal)
    guardian = Guardian(allowlist=cfg.allowlist, max_position_value=cfg.max_position_value, max_daily_loss=cfg.max_daily_loss)
    daemon = AgentDaemon(
        config=cfg,
        loop=loop,
        sleep=lambda seconds: None,
        journal=journal,
        reflection_memory=reflection_memory,
        strategy_library=strategy_library,
        curriculum_agent=curriculum_agent or StructuredCurriculumAgent(),
        strategy_admission=StrategyAdmission(guardian=guardian, strategy_library=strategy_library),
        reflect_every=1,
        curriculum_every=1,
    )
    return daemon, journal, reflection_memory, strategy_library


def test_daemon_reflects_runs_curriculum_and_records_events(tmp_path):
    daemon, journal, reflection_memory, strategy_library = make_daemon_with_learning(tmp_path)

    daemon.run(max_cycles=1)

    reflection = reflection_memory.load()
    assert reflection is not None
    assert reflection.window_cycles == 1
    assert [strategy.strategy_id for strategy in strategy_library.list()] == ["fallback-tiny-fixed-size", "hold-baseline"]
    events = journal.read_events()
    event_types = [event.event_type for event in events]
    assert "REFLECTION_GENERATED" in event_types
    assert "STRATEGY_EVALUATION_RECORDED" in event_types
    assert "CURRICULUM_PROPOSED" in event_types
    admission = [event for event in events if event.event_type == "STRATEGY_ADMISSION_REVIEWED"][-1]
    assert admission.status == "ACCEPTED"
    assert admission.strategy_id == "fallback-tiny-fixed-size"


def test_daemon_records_rejected_curriculum_strategy(tmp_path):
    from min_agent.models import CurriculumTask, StrategySpec

    class BadCurriculum:
        def propose(self, **kwargs):
            return CurriculumTask(
                task_id="bad-strategy",
                task_type="STRATEGY_SPEC",
                summary="try non-allowlisted strategy",
                strategy_spec=StrategySpec(
                    strategy_id="bad-tsla",
                    name="Bad TSLA",
                    kind="HOLD_BASELINE",
                    symbols=("TSLA",),
                    parameters={},
                    max_position_value=100,
                    created_at=datetime.now(tz=timezone.utc),
                    rationale="test rejection",
                ),
                rationale="test",
            )

    daemon, journal, _, strategy_library = make_daemon_with_learning(tmp_path, curriculum_agent=BadCurriculum())

    daemon.run(max_cycles=1)

    assert strategy_library.list() == []
    admission = [event for event in journal.read_events() if event.event_type == "STRATEGY_ADMISSION_REVIEWED"][-1]
    assert admission.status == "REJECTED"
    assert admission.strategy_id == "bad-tsla"
    assert admission.payload["accepted"] is False
    assert "allowlist" in admission.message


def test_daemon_records_proposal_when_transport_returns_unrelated_json(tmp_path):
    agent = StructuredCurriculumAgent(transport=lambda context: '{"temperature": 0.2}')
    daemon, journal, _, strategy_library = make_daemon_with_learning(tmp_path, curriculum_agent=agent)

    daemon.run(max_cycles=1)

    assert not journal.read_events("CURRICULUM_FAILED")
    assert journal.read_events("CURRICULUM_PROPOSED")
    assert [strategy.strategy_id for strategy in strategy_library.list()] == ["fallback-tiny-fixed-size", "hold-baseline"]


def test_daemon_records_reflection_failure(tmp_path):
    failing_memory = FailingReflectionMemory(tmp_path / "reflection.json")
    daemon, journal, _, _ = make_daemon_with_learning(tmp_path, reflection_memory=failing_memory)

    daemon.run(max_cycles=1)

    events = journal.read_events("REFLECTION_FAILED")
    assert events
    assert all(event.status == "FAILED" for event in events)
    assert events[-1].payload["error_type"] == "RuntimeError"


def test_daemon_records_curriculum_failure(tmp_path):
    daemon, journal, _, _ = make_daemon_with_learning(tmp_path, curriculum_agent=FailingCurriculum())

    daemon.run(max_cycles=1)

    events = journal.read_events("CURRICULUM_FAILED")
    assert events
    assert all(event.status == "FAILED" for event in events)
    assert events[-1].payload["error_type"] == "RuntimeError"


def test_daemon_computes_recent_no_exploration_summary(tmp_path):
    cfg = config(tmp_path, interval=0)
    journal = JsonlJournal(cfg.journal_path)
    loop = FakeLoop(journal=journal, strategy_id="hold-strategy")
    daemon = AgentDaemon(config=cfg, loop=loop, sleep=lambda seconds: None, journal=journal)

    daemon.run(max_cycles=1)

    summary = daemon._recent_exploration_summary()
    assert summary["no_exploration"] is True
    assert summary["market_open_cycles"] == 1
    assert summary["action_counts"] == {"HOLD": 1}
    assert summary["submitted_orders"] == 0
    assert summary["intended_notional"] == 0
    assert summary["strategy_ids"] == ["hold-strategy"]


def test_daemon_no_exploration_summary_turns_false_on_buy_intent(tmp_path):
    cfg = config(tmp_path, interval=0)
    journal = JsonlJournal(cfg.journal_path)
    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime.now(tz=timezone.utc),
        market_open=True,
        last_price=100,
        source="alpaca",
        account=AccountSnapshot(equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0),
    )
    journal.append(
        CycleRecord(
            cycle_id="buy-cycle",
            snapshot=snapshot,
            decision=TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.7, rationale="test", strategy_id="buy-strategy"),
            guardian=GuardianResult(approved=True, reason="approved"),
            execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="test"),
            strategy_id="buy-strategy",
        )
    )
    daemon = AgentDaemon(config=cfg, loop=FakeLoop(journal=journal), sleep=lambda seconds: None, journal=journal)

    summary = daemon._recent_exploration_summary()

    assert summary["no_exploration"] is False
    assert summary["buy_sell_intent_count"] == 1
    assert summary["intended_notional"] == 100


def test_daemon_passes_exploration_summary_to_curriculum(tmp_path):
    curriculum = CapturingCurriculum()
    daemon, journal, _, _ = make_daemon_with_learning(tmp_path, curriculum_agent=curriculum)

    daemon.run(max_cycles=1)

    assert curriculum.kwargs is not None
    summary = curriculum.kwargs["recent_exploration_summary"]
    assert summary["no_exploration"] is True
    assert summary["window_cycles"] == 1
    assert curriculum.kwargs["guardian_max_position_value"] == daemon.config.max_position_value
    event = journal.read_events("CURRICULUM_PROPOSED")[-1]
    assert event.payload["recent_exploration_summary"]["no_exploration"] is True


def test_daemon_records_no_exploration_lesson(tmp_path):
    cfg = config(tmp_path, interval=0)
    journal = JsonlJournal(cfg.journal_path)
    reflection_memory = ReflectionMemory(tmp_path / "reflection.json")
    knowledge_library = KnowledgeLibrary(tmp_path / "knowledge")
    daemon = AgentDaemon(
        config=cfg,
        loop=FakeLoop(journal=journal, strategy_id="hold-strategy"),
        sleep=lambda seconds: None,
        journal=journal,
        reflection_memory=reflection_memory,
        strategy_library=StrategyLibrary(cfg.strategy_dir),
        curriculum_agent=StructuredCurriculumAgent(),
        knowledge_admission=KnowledgeAdmission(knowledge_library=knowledge_library),
        reflect_every=1,
        curriculum_every=1,
    )

    daemon.run(max_cycles=1)

    assert knowledge_library.list(status="ACCEPTED", tags=("exploration",))
    assert journal.read_events("KNOWLEDGE_ARTIFACT_PROPOSED")
    reviewed = journal.read_events("KNOWLEDGE_ARTIFACT_ADMISSION_REVIEWED")[-1]
    assert reviewed.status == "ACCEPTED"


def test_daemon_persists_lifecycle_updates(tmp_path):
    cfg = config(tmp_path, interval=0)
    journal = JsonlJournal(cfg.journal_path)
    reflection_memory = ReflectionMemory(tmp_path / "reflection.json")
    strategy_library = StrategyLibrary(cfg.strategy_dir)
    strategy_library.save(
        StrategySpec(
            strategy_id="trial",
            name="Trial",
            kind="FIXED_SIZE",
            symbols=("SPY",),
            parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
            max_position_value=100,
            enabled=True,
            lifecycle="PROBATION",
            created_at=datetime.now(tz=timezone.utc),
            rationale="test",
        )
    )
    loop = FakeLoop(journal=journal, strategy_id="trial", action="BUY", quantity=1, status="SUBMITTED", confidence=0.8)
    daemon = AgentDaemon(
        config=cfg,
        loop=loop,
        sleep=lambda seconds: None,
        journal=journal,
        reflection_memory=reflection_memory,
        strategy_library=strategy_library,
        lifecycle_manager=StrategyLifecycleManager(min_active_cycles=1, min_active_submitted_orders=0),
        reflect_every=1,
    )

    daemon.run(max_cycles=1)

    assert strategy_library.load("trial").lifecycle == "ACTIVE"
    event = journal.read_events("STRATEGY_LIFECYCLE_UPDATED")[-1]
    assert event.strategy_id == "trial"
    assert event.payload["old_lifecycle"] == "PROBATION"
    assert event.payload["new_lifecycle"] == "ACTIVE"
