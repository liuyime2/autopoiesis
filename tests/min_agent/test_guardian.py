from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from min_agent.guardian import Guardian
from min_agent.models import (
    AccountSnapshot,
    DataSnapshot,
    OpenOrderSnapshot,
    PositionSnapshot,
    StrategySpec,
    TradeDecision,
)


def snapshot(
    *,
    market_open=True,
    equity=100_000,
    last_price=100,
    daily_loss=0,
    buying_power=None,
    positions=None,
    open_orders=None,
    timestamp=None,
):
    return DataSnapshot(
        symbol="SPY",
        timestamp=timestamp or datetime.now(timezone.utc),
        market_open=market_open,
        last_price=last_price,
        source="alpaca",
        account=AccountSnapshot(
            equity=equity,
            cash=equity,
            buying_power=buying_power if buying_power is not None else equity,
            portfolio_value=equity,
            daily_loss=daily_loss,
        ),
        positions=positions or (),
        open_orders=open_orders or (),
    )


def test_guardian_approves_small_paper_buy_on_real_open_market():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=3, confidence=0.65, rationale="trend")

    result = guardian.review(decision, snapshot())

    assert result.approved is True
    assert result.reason == "approved"


def test_guardian_rejects_live_mode():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot(), mode="live")

    assert result.approved is False
    assert "paper" in result.reason


def test_guardian_rejects_closed_market_trade():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot(market_open=False))

    assert result.approved is False
    assert "market" in result.reason


def test_guardian_allows_hold_when_market_is_closed():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.4, rationale="wait")

    result = guardian.review(decision, snapshot(market_open=False))

    assert result.approved is True


def test_guardian_rejects_position_over_hard_limit():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=200, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=3, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot(last_price=100))

    assert result.approved is False
    assert "position" in result.reason


def test_guardian_rejects_low_confidence():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500, min_confidence=0.6)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.4, rationale="guess")

    result = guardian.review(decision, snapshot())

    assert result.approved is False
    assert "confidence" in result.reason


def test_guardian_still_rejects_buy_outside_allowlist():
    guardian = Guardian(allowlist={"QQQ"}, max_position_value=1_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot())

    assert result.approved is False
    assert "allowlist" in result.reason


def test_guardian_rejects_stale_snapshot():
    old_timestamp = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    now = old_timestamp + timedelta(minutes=20)
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500, max_snapshot_age_seconds=600)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot(timestamp=old_timestamp), now=now)

    assert result.approved is False
    assert "stale" in result.reason


def test_guardian_rejects_max_trades_per_day():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500, max_trades_per_day=3)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot(), trades_today=3)

    assert result.approved is False
    assert "max trades" in result.reason


def test_guardian_rejects_sell_without_holdings():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=5, confidence=0.8, rationale="exit")

    result = guardian.review(decision, snapshot(positions=()))

    assert result.approved is False
    assert "holdings" in result.reason


def test_guardian_approves_sell_with_sufficient_holdings():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    positions = (PositionSnapshot(symbol="SPY", quantity=10, market_value=1000),)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=5, confidence=0.8, rationale="exit")

    result = guardian.review(decision, snapshot(positions=positions))

    assert result.approved is True


def test_guardian_allows_sell_to_reduce_oversized_position():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    positions = (PositionSnapshot(symbol="SPY", quantity=100, market_value=10_000),)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=50, confidence=0.8, rationale="reduce exposure")

    result = guardian.review(decision, snapshot(positions=positions, last_price=100))

    assert result.approved is True


def test_guardian_allows_sell_of_held_symbol_outside_allowlist():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    positions = (PositionSnapshot(symbol="TLT", quantity=20, market_value=2_000),)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=10, confidence=0.8, rationale="reduce exposure")
    snap = snapshot(positions=positions).model_copy(update={"symbol": "TLT"})
    decision = decision.model_copy(update={"symbol": "TLT"})

    result = guardian.review(decision, snap)

    assert result.approved is True


def test_guardian_still_rejects_sell_more_than_holdings():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=10_000, max_daily_loss=500)
    positions = (PositionSnapshot(symbol="SPY", quantity=5, market_value=500),)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=6, confidence=0.8, rationale="exit")

    result = guardian.review(decision, snapshot(positions=positions))

    assert result.approved is False
    assert "holdings" in result.reason


def test_guardian_rejects_conflicting_open_order():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    open_orders = (OpenOrderSnapshot(symbol="SPY", side="BUY", quantity=5, status="pending"),)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=3, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot(open_orders=open_orders))

    assert result.approved is False
    assert "conflicting" in result.reason


def test_guardian_rejects_insufficient_buying_power():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=10_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=100, confidence=0.7, rationale="trend")

    result = guardian.review(decision, snapshot(buying_power=100, last_price=100))

    assert result.approved is False
    assert "buying power" in result.reason


def test_guardian_review_strategy_rejects_non_allowlisted_symbols():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    strategy = StrategySpec(
        strategy_id="test-strat",
        name="Test",
        kind="HOLD_BASELINE",
        symbols=["TSLA"],
        max_position_value=500,
        created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        rationale="test",
    )

    result = guardian.review_strategy(strategy)

    assert result.approved is False
    assert "allowlist" in result.reason


def test_guardian_review_strategy_rejects_over_hard_position_limit():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    strategy = StrategySpec(
        strategy_id="test-strat",
        name="Test",
        kind="TREND_FOLLOW",
        symbols=["SPY"],
        parameters={"reference_price": 100, "threshold_pct": 0.02, "quantity": 1, "confidence": 0.8},
        max_position_value=5_000,
        created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        rationale="test",
    )

    result = guardian.review_strategy(strategy)

    assert result.approved is False
    assert "hard limit" in result.reason


def test_total_exposure_cap_blocks_a_buy_that_would_breach_it():
    """There was no aggregate exposure control at all: max_total_exposure was
    never passed by the CLI, and guardian.py only ever summed an always-empty
    position book."""
    guardian = Guardian(
        allowlist={"SPY"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=8_000,
    )
    held = (PositionSnapshot(symbol="QQQ", quantity=10, market_value=7_000),)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    result = guardian.review(decision, snapshot(last_price=500, positions=held))

    assert result.approved is False
    assert result.reason == "total exposure exceeds hard limit"


def test_total_exposure_cap_allows_a_buy_that_stays_under_it():
    guardian = Guardian(
        allowlist={"SPY"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=8_000,
    )
    held = (PositionSnapshot(symbol="QQQ", quantity=10, market_value=3_000),)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    assert guardian.review(decision, snapshot(last_price=500, positions=held)).approved is True


def test_per_order_cap_does_not_bound_aggregate_exposure_without_the_total_cap():
    """Documents why max_total_exposure must be configured: each BUY is under the
    per-order cap, so only the aggregate cap stops unbounded accumulation."""
    guardian = Guardian(
        allowlist={"SPY"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=None,
    )
    held = (PositionSnapshot(symbol="SPY", quantity=40, market_value=20_000),)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    assert guardian.review(decision, snapshot(last_price=500, positions=held)).approved is True


def test_sell_is_permitted_once_the_position_book_is_read():
    held = (PositionSnapshot(symbol="SPY", quantity=10, market_value=5_000),)
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=4, confidence=0.9, rationale="exit")

    assert guardian.review(decision, snapshot(last_price=500, positions=held)).approved is True


def test_unmeasured_day_start_equity_fails_closed_on_buy():
    """The old fallback computed equity - portfolio_value, always 0.0, which
    silently disabled the daily-loss kill switch."""
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500)
    unmeasured = snapshot(last_price=500).model_copy(
        update={
            "account": snapshot(last_price=500).account.model_copy(
                update={"daily_loss": 0.0, "day_start_equity_known": False}
            )
        }
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.9, rationale="probe")

    result = guardian.review(decision, unmeasured)

    assert result.approved is False
    assert result.reason == "account day-start equity unavailable"


def test_unmeasured_day_start_equity_still_permits_hold():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500)
    unmeasured = snapshot().model_copy(
        update={"account": snapshot().account.model_copy(update={"day_start_equity_known": False})}
    )
    decision = TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.0, rationale="wait")

    assert guardian.review(decision, unmeasured).approved is True


def test_daily_loss_limit_gates_orders_but_not_a_hold():
    """The daily-loss check used to sit above the HOLD branch, so a deliberate
    no-op was journalled as a risk REJECTION instead of a hold. It made the audit
    trail read as if the agent had been stopped by the risk system when in fact
    it had chosen not to trade."""
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500)
    breached = snapshot(daily_loss=600)

    hold = TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.0, rationale="no edge")
    assert guardian.review(hold, breached).approved is True

    buy = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.9, rationale="probe")
    assert guardian.review(buy, breached).reason == "daily loss limit reached"

    sell = TradeDecision(symbol="SPY", action="SELL", quantity=1, confidence=0.9, rationale="exit")
    assert guardian.review(sell, breached).reason == "daily loss limit reached"


def test_a_hold_is_skipped_not_rejected_after_the_daily_loss_limit_is_hit(tmp_path):
    """End to end through the loop: the journal should say SKIPPED for a hold."""
    from min_agent.executor import AlpacaPaperExecutor
    from min_agent.journal import JsonlJournal
    from min_agent.loop import TradingLoop
    from min_agent.models import AccountSnapshot, DataSnapshot

    class Broker:
        def submit_order(self, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("a HOLD must never reach the broker")

    journal = JsonlJournal(tmp_path / "journal.jsonl", lock=False)
    loop = TradingLoop(
        data_gateway=object(),
        decision_engine=type("E", (), {"decide": lambda self, ctx: _hold(ctx["symbol"])})(),
        guardian=Guardian(allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500),
        executor=AlpacaPaperExecutor(client=Broker(), base_url="https://paper-api.alpaca.markets"),
        journal=journal,
        mode="paper",
    )
    loop.data_gateway = type("G", (), {"snapshot": staticmethod(lambda s: DataSnapshot(
        symbol=s, timestamp=datetime.now(timezone.utc), market_open=True, last_price=500.0, source="alpaca",
        account=AccountSnapshot(equity=100_000, cash=100_000, buying_power=100_000,
                                portfolio_value=100_000, daily_loss=600),
    ))})()

    record = loop.run_once("SPY")

    assert record.decision.action == "HOLD"
    assert record.guardian.approved is True
    assert record.execution.status == "SKIPPED"


def _hold(symbol):
    from min_agent.models import TradeDecision

    return TradeDecision(symbol=symbol, action="HOLD", quantity=0, confidence=0.0, rationale="no edge")
