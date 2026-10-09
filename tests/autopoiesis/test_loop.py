from datetime import datetime, timezone

from autopoiesis.data_gateway import BrokerDataUnavailable
from autopoiesis.fill_reconciler import FILL_EVENT
from autopoiesis.loop import TradingLoop
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    OpenOrderSnapshot,
    PositionSnapshot,
    TradeDecision,
)


class FakeDataGateway:
    def snapshot(self, symbol):
        return DataSnapshot(
            symbol=symbol,
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


class FakeDecisionEngine:
    def decide(self, context):
        return TradeDecision(symbol=context["symbol"], action="HOLD", quantity=0, confidence=0.1, rationale="wait")


class StrategyDecisionEngine:
    def decide(self, context):
        return TradeDecision(
            symbol=context["symbol"],
            action="BUY",
            quantity=1,
            confidence=0.8,
            rationale="strategy decision",
            strategy_id="s1",
        )


class FailingDecisionEngine:
    def decide(self, context):
        raise ValueError("invalid confidence: medium")


class CapturingDecisionEngine:
    def __init__(self):
        self.context = None

    def decide(self, context):
        self.context = context
        return TradeDecision(symbol=context["symbol"], action="HOLD", quantity=0, confidence=0.1, rationale="wait")


class ContextDataGateway(FakeDataGateway):
    def snapshot(self, symbol):
        return super().snapshot(symbol).model_copy(
            update={
                "positions": (PositionSnapshot(symbol="SPY", quantity=29, market_value=21_000),),
                "open_orders": (OpenOrderSnapshot(symbol="SPY", side="SELL", quantity=1, status="new"),),
            }
        )


class FakeGuardian:
    def __init__(self):
        self.trades_today = None

    # `now` mirrors the real Guardian's signature. The loop passes it so a replay over
    # historical bars is judged for staleness against the replayed session's clock rather
    # than wall-clock; a fake that omitted it would hide that interface drift instead of
    # catching it.
    def review(self, decision, snapshot, mode="paper", trades_today=0,
               agent_position_quantity=None, now=None, agent_short_quantity=None):
        self.now = now
        self.trades_today = trades_today
        return GuardianResult(approved=True, reason="approved")


class FakeExecutor:
    def execute(self, decision, guardian_result, *, cycle_id=None):
        return ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold")


class FakeJournal:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


class FakeTradeCounter:
    def __init__(self, count=2, error=None):
        self.count = count
        self.error = error

    def trades_today(self):
        if self.error:
            raise self.error
        return self.count


def test_trading_loop_runs_one_cycle_and_journals_result():
    journal = FakeJournal()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=FakeDecisionEngine(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert isinstance(record, CycleRecord)
    assert record.symbol == "SPY"
    assert journal.records == [record]


def test_trading_loop_fail_closes_invalid_decision_to_hold():
    journal = FakeJournal()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=FailingDecisionEngine(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert record.decision.action == "HOLD"
    assert record.decision.quantity == 0
    assert record.strategy_id is None
    assert record.error is not None
    assert "decision_error" in record.error
    assert journal.records == [record]


def test_trading_loop_records_top_level_strategy_id():
    journal = FakeJournal()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=StrategyDecisionEngine(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert record.decision.strategy_id == "s1"
    assert record.strategy_id == "s1"
    assert journal.records == [record]


def test_trading_loop_passes_positions_and_open_orders_to_decision_engine():
    journal = FakeJournal()
    decision_engine = CapturingDecisionEngine()
    loop = TradingLoop(
        data_gateway=ContextDataGateway(),
        decision_engine=decision_engine,
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
    )

    loop.run_once("SPY")

    assert decision_engine.context["positions"] == [{"symbol": "SPY", "quantity": 29.0, "market_value": 21_000.0}]
    assert decision_engine.context["open_orders"] == [
        {"symbol": "SPY", "side": "SELL", "quantity": 1.0, "status": "new"}
    ]


def test_trading_loop_passes_trades_today_to_guardian():
    journal = FakeJournal()
    guardian = FakeGuardian()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=StrategyDecisionEngine(),
        guardian=guardian,
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
        trade_counter=FakeTradeCounter(count=4),
    )

    loop.run_once("SPY")

    assert guardian.trades_today == 4


def test_trading_loop_fail_closes_on_trade_counter_error():
    journal = FakeJournal()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=StrategyDecisionEngine(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
        trade_counter=FakeTradeCounter(error=RuntimeError("count failed")),
    )

    record = loop.run_once("SPY")

    assert record.decision.action == "HOLD"
    assert "trade_counter_error" in record.error


class BrokerOutageDataGateway:
    def snapshot(self, symbol):
        raise BrokerDataUnavailable("could not read broker positions: ConnectTimeout")


class EventCapturingJournal(FakeJournal):
    def __init__(self):
        super().__init__()
        self.events = []

    def append_event(self, event):
        self.events.append(event)


def test_broker_outage_is_journaled_instead_of_being_invisible():
    """The gateway used to be called outside the try-block, so a broker outage
    incremented an error counter and journaled nothing at all."""
    journal = EventCapturingJournal()
    loop = TradingLoop(
        data_gateway=BrokerOutageDataGateway(),
        decision_engine=FakeDecisionEngine(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert record is None
    assert journal.records == []
    assert len(journal.events) == 1
    event = journal.events[0]
    assert event.event_type == "CYCLE_FAILED"
    assert event.status == "FAILED"
    assert event.payload["symbol"] == "SPY"
    assert event.payload["error_type"] == "BrokerDataUnavailable"
    assert event.payload["paper_only"] is True


def test_broker_outage_does_not_invent_a_snapshot():
    """A CycleRecord requires a DataSnapshot. Synthesising one to record the
    failure would be fabricated market data (AGENTS.md 2)."""
    journal = EventCapturingJournal()
    loop = TradingLoop(
        data_gateway=BrokerOutageDataGateway(),
        decision_engine=FakeDecisionEngine(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
    )

    assert loop.run_once("SPY") is None
    assert journal.events[0].payload.get("last_price") is None
    assert journal.events[0].payload.get("equity") is None


def test_decision_and_trade_counter_errors_both_survive_in_the_record():
    class BothFail:
        def decide(self, context):
            raise ValueError("decision blew up")

        def trades_today(self):
            raise RuntimeError("counter blew up")

    journal = FakeJournal()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=BothFail(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=journal,
        mode="paper",
        trade_counter=BothFail(),
    )

    record = loop.run_once("SPY")

    assert "decision_error" in record.error
    assert "trade_counter_error" in record.error


def test_successful_cycle_has_no_error_field():
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=FakeDecisionEngine(),
        guardian=FakeGuardian(),
        executor=FakeExecutor(),
        journal=FakeJournal(),
        mode="paper",
    )

    assert loop.run_once("SPY").error is None


class RecordingExecutor:
    """Records every decision actually submitted, so a skip is observable."""

    def __init__(self, status="SUBMITTED"):
        self.status = status
        self.submitted = []

    def execute(self, decision, guardian_result, *, cycle_id=None):
        self.submitted.append(decision)
        if decision.action == "HOLD":
            return ExecutionResult(
                status="SKIPPED", order_id=None, message="hold", filled_quantity=0.0,
            )
        return ExecutionResult(
            status=self.status, order_id="o1", client_order_id=f"coid-{cycle_id}",
            filled_quantity=float(decision.quantity), message="submitted",
        )


class JournalWithHistory(FakeJournal):
    """A journal that can answer the two questions the in-flight guard asks."""

    def __init__(self, records=(), events=()):
        super().__init__()
        self.records = list(records)
        self._events = list(events)

    def read_events(self, event_type=None, limit=None):
        return list(self._events)

    def last_n(self, n):
        return list(self.records)[-n:]


def _submitted(symbol, action, quantity, strategy_id, minutes_ago, status="SUBMITTED"):
    from datetime import timedelta

    from autopoiesis.models import DataSnapshot

    base = _base_record()
    return CycleRecord(
        cycle_id=f"cycle-{minutes_ago}",
        snapshot=base.snapshot.model_copy(update={
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        }),
        decision=base.decision.model_copy(update={
            "symbol": symbol, "action": action, "quantity": quantity,
            "strategy_id": strategy_id,
        }),
        guardian=base.guardian,
        execution=ExecutionResult(
            status=status, order_id="o", client_order_id=f"coid-{minutes_ago}",
            filled_quantity=0.0, message="submitted",
        ),
        strategy_id=strategy_id,
        error=None,
    )


def test_an_unfilled_submission_blocks_an_identical_resubmission():
    """A lost broker response looks like a decision that was never acted on.

    `client_order_id` hashes the cycle id and every cycle gets a fresh uuid4, so a retry
    produces a different id and the broker cannot deduplicate it. The only backstop was
    Guardian's check against resting broker orders, which misses the common case: the order
    filled immediately and the response never arrived. Re-submitting then doubles the
    position.
    """
    journal = JournalWithHistory(records=[_submitted("SPY", "BUY", 1, "s1", minutes_ago=2)])
    executor = RecordingExecutor()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=_AlwaysBuy("SPY", 1, "s1"),
        guardian=FakeGuardian(),
        executor=executor,
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert record.decision.action == "HOLD", (
        f"re-submitted an order already in flight; got {record.decision.action!r}"
    )
    assert "skip:" in record.decision.rationale and "identical order" in record.decision.rationale, (
        f"the skip must be recorded on the cycle, not silently dropped; "
        f"rationale={record.decision.rationale!r}"
    )
    # It must not be recorded as an *error*. `record.error` is the numerator of the
    # lifecycle's failure rate, so charging the guard for firing retires the strategies
    # that trade most: on this journal every FIXED_SIZE carrying errors had
    # `in_flight_order` as its only error text, and all of them are now PAUSED or RETIRED.
    assert record.error is None, (
        f"a correctly-refused duplicate is not an operational failure; error={record.error!r}"
    )
    assert record.execution.status == "SKIPPED"
    assert [d.action for d in executor.submitted] == ["HOLD"], "nothing may reach the executor"


def test_a_confirmed_fill_does_not_block_the_next_order():
    """Once the fill is confirmed the guard must stand aside - it is not a cooldown."""
    from uuid import uuid4

    from autopoiesis.models import JournalEvent

    fill = JournalEvent(
        event_id=str(uuid4()),
        event_type=FILL_EVENT,
        timestamp=datetime.now(timezone.utc),
        status="SUCCESS",
        payload={"client_order_id": "coid-2", "filled_quantity": 1.0},
    )
    journal = JournalWithHistory(
        records=[_submitted("SPY", "BUY", 1, "s1", minutes_ago=2)],
        events=[fill],
    )
    executor = RecordingExecutor()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=_AlwaysBuy("SPY", 1, "s1"),
        guardian=FakeGuardian(),
        executor=executor,
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert record.decision.action == "BUY", "a confirmed fill must not block the next order"
    assert [d.action for d in executor.submitted] == ["BUY"]


def test_an_old_unfilled_submission_does_not_block_trading_forever():
    """Outside the window the order is a reconciliation problem, not a duplication one."""
    journal = JournalWithHistory(records=[_submitted("SPY", "BUY", 1, "s1", minutes_ago=600)])
    executor = RecordingExecutor()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=_AlwaysBuy("SPY", 1, "s1"),
        guardian=FakeGuardian(),
        executor=executor,
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert record.decision.action == "BUY", (
        "a ten-hour-old unfilled order must not block trading; reconciliation handles it"
    )


def test_a_different_quantity_is_not_treated_as_a_duplicate():
    """The guard matches the whole order, not just the symbol and side."""
    journal = JournalWithHistory(records=[_submitted("SPY", "BUY", 1, "s1", minutes_ago=2)])
    executor = RecordingExecutor()
    loop = TradingLoop(
        data_gateway=FakeDataGateway(),
        decision_engine=_AlwaysBuy("SPY", 5, "s1"),
        guardian=FakeGuardian(),
        executor=executor,
        journal=journal,
        mode="paper",
    )

    record = loop.run_once("SPY")

    assert record.decision.action == "BUY", "a different quantity is a different order"


class _AlwaysBuy:
    """A decision engine that always produces the same order, for the in-flight guard."""

    def __init__(self, symbol, quantity, strategy_id):
        self.symbol = symbol
        self.quantity = quantity
        self.strategy_id = strategy_id

    def decide(self, context):
        return TradeDecision(
            symbol=self.symbol, action="BUY", quantity=self.quantity,
            confidence=0.9, rationale="always buy", strategy_id=self.strategy_id,
        )


def _base_record():
    """One cycle with no execution, used as the starting point for a journal fixture."""
    return CycleRecord(
        cycle_id="base",
        snapshot=DataSnapshot(
            symbol="SPY",
            timestamp=datetime.now(timezone.utc),
            market_open=True,
            last_price=100,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action="HOLD", quantity=0, confidence=0.0, rationale="idle",
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status="SKIPPED", order_id=None, message="hold", filled_quantity=0.0,
        ),
        strategy_id=None,
        error=None,
    )


def test_the_guardian_clock_is_injectable_and_defaults_to_live_behaviour():
    """Replay over historical bars needs staleness judged in session time.

    The Guardian compares a snapshot's timestamp against the current time, so a replay over
    real bars from last week was refused on all 847 cycles as "data snapshot is stale" -
    correctly, because those bars really are stale. Pointing the Guardian at the replayed
    session's own clock runs the *same* check against the correct time base; relaxing
    staleness instead would be testing a system nobody ships.

    What matters is that the default is untouched: with no clock injected the Guardian must
    still receive `None` and fall back to wall-clock, so the live path cannot drift.
    """
    guardian = FakeGuardian()

    def build(**kwargs):
        return TradingLoop(
            data_gateway=FakeDataGateway(), decision_engine=FakeDecisionEngine(),
            guardian=guardian, executor=FakeExecutor(), journal=FakeJournal(),
            mode="paper", **kwargs,
        )

    build().run_once("SPY")
    assert guardian.now is None, "the live path must not have its clock overridden"

    injected = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)
    build(now=lambda: injected).run_once("SPY")
    assert guardian.now == injected
