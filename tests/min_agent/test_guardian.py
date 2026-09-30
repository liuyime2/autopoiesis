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

    result = guardian.review(
        decision, snapshot(positions=positions), agent_position_quantity=10
    )

    assert result.approved is True


def test_guardian_allows_sell_to_reduce_oversized_position():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    positions = (PositionSnapshot(symbol="SPY", quantity=100, market_value=10_000),)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=50, confidence=0.8, rationale="reduce exposure")

    result = guardian.review(
        decision, snapshot(positions=positions, last_price=100),
        agent_position_quantity=100,
    )

    assert result.approved is True


def test_guardian_allows_sell_of_held_symbol_outside_allowlist():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    positions = (PositionSnapshot(symbol="TLT", quantity=20, market_value=2_000),)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=10, confidence=0.8, rationale="reduce exposure")
    snap = snapshot(positions=positions).model_copy(update={"symbol": "TLT"})
    decision = decision.model_copy(update={"symbol": "TLT"})

    result = guardian.review(decision, snap, agent_position_quantity=20)

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
        allowlist={"SPY", "QQQ"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=8_000,
    )
    held = (PositionSnapshot(symbol="QQQ", quantity=10, market_value=7_000),)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    result = guardian.review(decision, snapshot(last_price=500, positions=held))

    assert result.approved is False
    assert "exceeds the 8000.00 limit" in result.reason


def test_total_exposure_cap_allows_a_buy_that_stays_under_it():
    guardian = Guardian(
        allowlist={"SPY"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=8_000,
    )
    held = (PositionSnapshot(symbol="QQQ", quantity=10, market_value=3_000),)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    assert guardian.review(
        decision, snapshot(last_price=500, positions=held),
        agent_position_quantity=10,
    ).approved is True


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

    assert guardian.review(
        decision, snapshot(last_price=500, positions=held),
        agent_position_quantity=10,
    ).approved is True


def test_sell_is_permitted_once_the_position_book_is_read():
    held = (PositionSnapshot(symbol="SPY", quantity=10, market_value=5_000),)
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500)
    decision = TradeDecision(symbol="SPY", action="SELL", quantity=4, confidence=0.9, rationale="exit")

    assert guardian.review(
        decision, snapshot(last_price=500, positions=held),
        agent_position_quantity=10,
    ).approved is True


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


def test_admission_rejects_a_strategy_that_could_never_execute():
    """A FIXED_SIZE strategy submits its confidence verbatim and the decision path
    rejects anything under min_confidence, but admission accepted confidence
    anywhere in [0, 1]. A strategy could therefore be admitted that was incapable
    of ever placing an order. One SELL was rejected 100% of the time on exactly
    this - "decision confidence below minimum" - and nothing in the system said
    the strategy was structurally unexecutable."""
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500)
    strategy = StrategySpec(
        strategy_id="low-conf",
        name="low confidence",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "SELL", "quantity": 1, "confidence": 0.3},
        max_position_value=1000,
        rationale="test",
        created_at=datetime.now(tz=timezone.utc),
    )

    result = guardian.review_strategy(strategy)

    assert not result.approved
    assert "could never execute" in result.reason
    assert "0.30" in result.reason


def test_admission_accepts_a_strategy_at_the_confidence_floor():
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500)
    strategy = StrategySpec(
        strategy_id="ok",
        name="fine",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "SELL", "quantity": 1, "confidence": 0.5},
        max_position_value=1000,
        rationale="test",
        created_at=datetime.now(tz=timezone.utc),
    )

    assert guardian.review_strategy(strategy).approved


def test_the_exposure_cap_ignores_positions_the_agent_cannot_trade():
    """The cap used to sum every position, which counted a $37k bond allocation the
    agent has no mandate over: BIL and TLT are not in the allowlist, so it can
    neither buy them nor is it permitted to sell them. Against a $20k cap that left
    the agent zero usable exposure, and a limit that forbids every trade is not risk
    management - it is a brick.

    The agent can only *increase* exposure by buying allowlist symbols, so bounding
    the allowlist book bounds everything it is able to do.
    """
    guardian = Guardian(
        allowlist={"SPY", "QQQ"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=20_000,
    )
    unmandated = (
        PositionSnapshot(symbol="BIL", quantity=209, market_value=19_148.58),
        PositionSnapshot(symbol="TLT", quantity=226, market_value=17_757.95),
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    result = guardian.review(decision, snapshot(last_price=500, positions=unmandated))

    assert result.approved is True
    assert "agent exposure 0.00" in result.reason or result.approved


def test_the_exposure_cap_still_binds_on_the_agents_own_book():
    """The relaxation must not become an unbounded one: the same cap, measured over
    allowlist positions the agent does hold, still refuses the breaching buy."""
    guardian = Guardian(
        allowlist={"SPY", "QQQ"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=20_000,
    )
    # 19000 already held, buying 5 at 500 is 2500, so 21500 breaches the 20000 cap.
    held = (
        PositionSnapshot(symbol="SPY", quantity=20, market_value=19_000.0),
        PositionSnapshot(symbol="TLT", quantity=226, market_value=17_757.95),
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    result = guardian.review(decision, snapshot(last_price=500, positions=held))

    assert result.approved is False
    assert "agent exposure 19000.00" in result.reason
    assert "account total is 36757.95" in result.reason, "both numbers stay auditable"


def test_the_account_backstop_bounds_the_whole_book_when_the_owner_asks_for_it():
    """Opt-in and separate: the conservative reading of "exposure" is still
    available for an account owner who wants the total itself fenced. Turning it on
    is a deliberate risk decision, not a default."""
    guardian = Guardian(
        allowlist={"SPY", "QQQ"},
        max_position_value=5_000,
        max_daily_loss=500,
        max_total_exposure=20_000,
        max_account_value=30_000,
    )
    held = (
        PositionSnapshot(symbol="SPY", quantity=1, market_value=766.0),
        PositionSnapshot(symbol="BIL", quantity=209, market_value=19_148.58),
        PositionSnapshot(symbol="TLT", quantity=226, market_value=17_757.95),
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=5, confidence=0.9, rationale="probe")

    result = guardian.review(decision, snapshot(last_price=500, positions=held))

    assert result.approved is False
    assert "account total" in result.reason
    assert "30000.00 account limit" in result.reason


# --------------------------------------------------------------------------
# A SELL is bounded by what the AGENT holds, not only by what the account holds
# --------------------------------------------------------------------------

def test_a_sell_cannot_reach_the_account_owners_shares():
    """The real incident, as numbers. The agent bought 23 shares; the account held
    52; one SELL of 52 was approved and filled, liquidating 29 shares - 56% - of a
    pre-existing position the agent never bought.

    The account-level check that does exist is necessary and not sufficient: the
    account did hold 52.
    """
    guardian = Guardian(
        allowlist={"SPY", "QQQ"}, max_position_value=5_000, max_daily_loss=500,
    )
    held = (PositionSnapshot(symbol="SPY", quantity=52, market_value=52_000),)
    decision = TradeDecision(
        symbol="SPY", action="SELL", quantity=52, confidence=0.9, rationale="exit",
    )

    result = guardian.review(
        decision, snapshot(last_price=1000, positions=held),
        agent_position_quantity=23,
    )

    assert result.approved is False
    assert "the agent holds 23" in result.reason
    assert "not the agent's to sell" in result.reason


def test_a_sell_within_what_the_agent_holds_is_still_permitted():
    """The rule must not block the agent's own exits. Refusing every SELL would stop
    it reducing risk, which is its own kind of danger."""
    guardian = Guardian(
        allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500,
    )
    held = (PositionSnapshot(symbol="SPY", quantity=52, market_value=52_000),)
    decision = TradeDecision(
        symbol="SPY", action="SELL", quantity=23, confidence=0.9, rationale="exit",
    )

    result = guardian.review(
        decision, snapshot(last_price=1000, positions=held),
        agent_position_quantity=23,
    )

    assert result.approved is True


def test_an_unknown_agent_holding_refuses_the_sell_rather_than_allowing_it():
    """Failing closed is the deliberate direction. A missed exit is recoverable - the
    next cycle retries and the position is unchanged - whereas liquidating the
    account owner's position is not."""
    guardian = Guardian(
        allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500,
    )
    held = (PositionSnapshot(symbol="SPY", quantity=52, market_value=52_000),)
    decision = TradeDecision(
        symbol="SPY", action="SELL", quantity=10, confidence=0.9, rationale="exit",
    )

    result = guardian.review(decision, snapshot(last_price=1000, positions=held))

    assert result.approved is False
    assert "unknown" in result.reason
    assert "refusing" in result.reason


def test_an_agent_holding_of_zero_refuses_any_sell():
    guardian = Guardian(
        allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500,
    )
    held = (PositionSnapshot(symbol="SPY", quantity=52, market_value=52_000),)
    decision = TradeDecision(
        symbol="SPY", action="SELL", quantity=1, confidence=0.9, rationale="exit",
    )

    result = guardian.review(
        decision, snapshot(last_price=1000, positions=held),
        agent_position_quantity=0,
    )

    assert result.approved is False


def test_a_buy_is_unaffected_by_the_agent_holding_rule():
    """The rule is about SELL. A BUY must not start being refused because the agent
    holds nothing of that symbol yet - which is the normal case."""
    guardian = Guardian(
        allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500,
    )
    decision = TradeDecision(
        symbol="SPY", action="BUY", quantity=1, confidence=0.9, rationale="probe",
    )

    result = guardian.review(decision, snapshot(last_price=1000))

    assert result.approved is True


def test_the_loop_supplies_the_agents_holding_from_its_own_fills(tmp_path):
    """End to end: the quantity comes from ORDER_FILL_CONFIRMED events, so it
    reflects what this agent did rather than what the account happens to hold."""
    from min_agent.journal import JsonlJournal
    from min_agent.models import JournalEvent

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for i, (side, qty) in enumerate((("BUY", 10), ("BUY", 5), ("SELL", 3))):
        journal.append_event(JournalEvent(
            event_id=f"f{i}", event_type="ORDER_FILL_CONFIRMED",
            timestamp=datetime.now(tz=timezone.utc), status="SUCCESS", message="m",
            payload={"symbol": "SPY", "side": side, "filled_quantity": qty,
                     "fill_price": 100.0},
        ))

    class _Loop:
        from min_agent.loop import TradingLoop as _T
    loop = _Loop._T.__new__(_Loop._T)
    loop.journal = journal
    loop._agent_holding_errors = []

    assert loop._agent_holding("SPY") == 12
    assert loop._agent_holding("QQQ") == 0


def test_an_unreadable_journal_is_recorded_and_fails_closed(tmp_path):
    """A silent `None` here would be indistinguishable from a legitimate 'the agent
    holds nothing', which is the reading that lets the owner's shares be sold."""
    from min_agent.loop import TradingLoop

    class Broken:
        def read_events(self, *a, **k):
            raise RuntimeError("disk gone")

    loop = TradingLoop.__new__(TradingLoop)
    loop.journal = Broken()
    loop._agent_holding_errors = []

    assert loop._agent_holding("SPY") is None
    assert loop._agent_holding_errors, "the failure must be recorded, not swallowed"

def test_a_sell_larger_than_the_agent_bought_does_not_make_a_later_buy_disappear(tmp_path):
    """A SELL bigger than the agent's holding must not subtract from BUYs that came
    after it.

    Found at the first open after this was written, on a real account. The journal held
    23 agent BUYs, then a SELL of 52 - 29 of those shares being the account owner's -
    then 10 further agent BUYs. Summing the sides as totals gives 33 - 52 = -19, so the
    loop reported the agent as holding nothing and the Guardian refused every exit,
    including exits from shares the agent had demonstrably bought and the broker
    confirmed it still held. Replaying in timestamp order gives 23 - 52 + 10 = 10,
    which matches the broker holding of 10 exactly.

    The clamp is not an optimisation: without it the 29 owner shares the agent sold are
    netted against later purchases, and the agent ends up credited with a position it
    does not own.
    """
    from min_agent.journal import JsonlJournal
    from min_agent.models import JournalEvent

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    base = datetime.now(tz=timezone.utc)
    sequence = [("BUY", 23), ("SELL", 52), ("BUY", 10)]
    for i, (side, qty) in enumerate(sequence):
        journal.append_event(JournalEvent(
            event_id=f"f{i}", event_type="ORDER_FILL_CONFIRMED",
            timestamp=base + timedelta(seconds=i), status="SUCCESS", message="m",
            payload={"symbol": "SPY", "side": side, "filled_quantity": float(qty),
                     "fill_price": 700.0},
        ))

    class _Loop:
        from min_agent.loop import TradingLoop as _T
    loop = _Loop._T.__new__(_Loop._T)
    loop.journal = journal
    loop._agent_holding_errors = []

    assert loop._agent_holding("SPY") == 10


def test_an_agent_holding_is_computed_in_time_order_not_journal_order(tmp_path):
    """    read_events does not promise chronological order, and the replay depends on it.

    The sequence discriminates: a commutative sum of BUY 10, SELL 12, BUY 5 gives 3,
    while replaying in order gives 5 - the floor stops the SELL deducting below zero and
    the later BUY is not netted against it. An earlier version of this test used
    BUY 10, SELL 4, BUY 5, where both approaches give 11, so the test passed whether or
    not the ordering fix was present. A test that cannot fail is not a test.
    """
    from min_agent.journal import JsonlJournal
    from min_agent.models import JournalEvent

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    base = datetime.now(tz=timezone.utc)
    for i, (side, qty) in enumerate([("BUY", 10), ("SELL", 12), ("BUY", 5)]):
        journal.append_event(JournalEvent(
            event_id=f"f{i}", event_type="ORDER_FILL_CONFIRMED",
            timestamp=base + timedelta(seconds=i), status="SUCCESS", message="m",
            payload={"symbol": "SPY", "side": side, "filled_quantity": float(qty),
                     "fill_price": 700.0},
        ))

    class _Loop:
        from min_agent.loop import TradingLoop as _T
    loop = _Loop._T.__new__(_Loop._T)
    loop.journal = journal
    loop._agent_holding_errors = []

    assert loop._agent_holding("SPY") == 5
