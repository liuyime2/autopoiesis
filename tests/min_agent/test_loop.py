from datetime import datetime, timezone

from min_agent.data_gateway import BrokerDataUnavailable
from min_agent.loop import TradingLoop
from min_agent.models import (
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

    def review(self, decision, snapshot, mode="paper", trades_today=0):
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
