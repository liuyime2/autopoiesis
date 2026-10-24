"""The core sleeve sizes SPY to a volatility target inside a capped pool, or says nothing.

All fixtures are synthetic. Usage: pytest tests/autopoiesis/test_core_sleeve.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.config import AgentConfig
from autopoiesis.models import AccountSnapshot, DataSnapshot, OpenOrderSnapshot, PositionSnapshot
from autopoiesis.sleeve import SLEEVE_POOL_CAP, SLEEVE_STRATEGY_ID, CoreSleeve

PRICE = 500.0


def _snapshot(*, held: int = 0, symbol: str = "SPY", market_open: bool = True, orders=()) -> DataSnapshot:
    positions = (PositionSnapshot(symbol="SPY", quantity=held, market_value=held * PRICE),) if held else ()
    return DataSnapshot(
        symbol=symbol, timestamp=datetime(2026, 10, 26, 14, tzinfo=timezone.utc), market_open=market_open,
        last_price=PRICE, source="alpaca",
        account=AccountSnapshot(equity=100_000, cash=50_000, buying_power=50_000, portfolio_value=100_000, daily_loss=0),
        positions=positions, open_orders=tuple(orders),
    )


def _sleeve(vol: float | None = 10.0, status: str = "ACTIVE", pool: float = 20_000.0) -> CoreSleeve:
    block = None if vol is None else {"forecast_vol_pct": vol, "status": status}
    return CoreSleeve(symbol="SPY", pool=pool, target_vol_pct=10.0, risk_block=lambda _s: block)


def test_buys_up_to_the_full_pool_when_volatility_is_at_target():
    decision = _sleeve(vol=10.0).plan(_snapshot(held=0))
    assert decision is not None
    assert (decision.action, decision.quantity, decision.strategy_id) == ("BUY", 40, SLEEVE_STRATEGY_ID)


def test_sizes_down_when_forecast_volatility_doubles():
    decision = _sleeve(vol=20.0).plan(_snapshot(held=40))
    assert decision is not None
    assert (decision.action, decision.quantity) == ("SELL", 20)


def test_never_leverages_when_volatility_is_low():
    decision = _sleeve(vol=5.0).plan(_snapshot(held=0))
    assert decision is not None and decision.quantity == 40


def test_inside_the_band_it_says_nothing():
    assert _sleeve(vol=10.0).plan(_snapshot(held=38)) is None  # $1,000 off a $2,000 band


@pytest.mark.parametrize("kwargs", [{"vol": None}, {"status": "SUSPENDED"}, {"vol": 0.0}])
def test_without_a_usable_forecast_it_says_nothing(kwargs):
    assert _sleeve(**kwargs).plan(_snapshot(held=0)) is None


def test_other_symbols_a_closed_market_and_resting_orders_are_left_alone():
    sleeve = _sleeve()
    assert sleeve.plan(_snapshot(symbol="QQQ")) is None
    assert sleeve.plan(_snapshot(market_open=False)) is None
    resting = OpenOrderSnapshot(symbol="SPY", side="BUY", quantity=1, status="new")
    assert sleeve.plan(_snapshot(orders=[resting])) is None


def test_a_position_larger_than_the_pool_is_not_the_sleeves_to_size():
    assert _sleeve(vol=40.0).plan(_snapshot(held=60)) is None  # $30,000 held against a $20,000 pool


def test_the_pool_can_never_exceed_the_authorised_cap():
    with pytest.raises(ValueError):
        _sleeve(pool=SLEEVE_POOL_CAP + 1)
    with pytest.raises(ValueError):
        _sleeve(pool=0.0)


def test_it_is_off_by_default_and_the_env_is_capped():
    assert AgentConfig.from_env({}).sleeve_enabled is False
    on = AgentConfig.from_env({"AUTOPOIESIS_SLEEVE_SYMBOL": "spy", "AUTOPOIESIS_SLEEVE_POOL": "20000"})
    assert on.sleeve_enabled and on.sleeve_symbol == "SPY"
    with pytest.raises(ValueError):
        AgentConfig.from_env({"AUTOPOIESIS_SLEEVE_SYMBOL": "SPY", "AUTOPOIESIS_SLEEVE_POOL": "20001"})


def test_a_sleeve_order_carries_valid_attributable_provenance():
    """market-open-verify refuses an order without provenance (SESSION_PROVENANCE_INVALID)."""
    from autopoiesis.learning_evidence import valid_provenance

    decision = _sleeve(vol=10.0).plan(_snapshot(held=0))
    assert decision is not None
    assert valid_provenance(decision.strategy_provenance) is not None
    assert decision.strategy_provenance.strategy_id == decision.strategy_id


def test_the_loop_takes_the_sleeve_first_and_the_gate_admits_only_the_sleeve():
    from test_loop import (
        FakeDataGateway,
        FakeDecisionEngine,
        FakeExecutor,
        FakeGuardian,
        FakeJournal,
    )

    from autopoiesis.daemon import AgentDaemon
    from autopoiesis.loop import TradingLoop

    class Gateway(FakeDataGateway):
        def snapshot(self, symbol):
            return _snapshot(held=0).model_copy(update={"symbol": symbol})

    loop = TradingLoop(
        data_gateway=Gateway(), decision_engine=FakeDecisionEngine(), guardian=FakeGuardian(),
        executor=FakeExecutor(), journal=FakeJournal(), mode="paper",
    )
    loop.sleeve = _sleeve(vol=10.0)
    loop.accumulation_allowed = AgentDaemon._screen_passed.__get__(
        type("D", (), {"loop": loop, "_screen_verdicts": {}})()
    )
    record = loop.run_once("SPY")
    assert record.decision.strategy_id == SLEEVE_STRATEGY_ID
    assert record.decision.action in {"BUY", "HOLD"}
    assert "has not passed the screen" not in record.decision.rationale
    assert loop.accumulation_allowed("some-other-strategy") is False
