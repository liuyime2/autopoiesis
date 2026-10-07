"""The RULE strategy kind: parameters only, validated, executed and backtested by one signal.

See docs/history/plans/2026-10-07-bounded-short-and-rule-dsl.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from min_agent.models import AccountSnapshot, DataSnapshot, StrategySpec
from min_agent.regime import Bar
from min_agent.research import backtest as bt
from min_agent.strategy_engine import StrategyExecutor, behavioural_signature, rule_signal

NOW = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)
BASE = {"signal": "return_over_n", "lookback": 3, "threshold_pct": 0.5, "when_above": "BUY",
        "when_below": "SELL", "quantity": 1, "confidence": 0.6}


def _spec(**overrides):
    return StrategySpec(
        strategy_id="r1", name="r", kind="RULE", symbols=("SPY",), parameters={**BASE, **overrides},
        max_position_value=1000, enabled=True, lifecycle="PROBATION", created_at=NOW, rationale="t",
    )


def _snap(price):
    return DataSnapshot(
        symbol="SPY", timestamp=NOW, market_open=True, last_price=price, source="alpaca",
        account=AccountSnapshot(equity=1e5, cash=1e5, buying_power=1e5, portfolio_value=1e5, daily_loss=0),
    )


def test_the_signals_are_percent_figures_over_lookback():
    assert rule_signal([100, 101, 102, 103], "return_over_n", 3) == pytest.approx(3.0)
    assert rule_signal([100, 98, 102, 101], "price_vs_sma", 3) == pytest.approx(1.0)
    assert rule_signal([100, 101], "return_over_n", 3) is None, "too little history is no signal"


@pytest.mark.parametrize("prices,last,action", [
    ([100, 100, 100], 101.0, "BUY"),    # +1% over 3 bars > 0.5
    ([100, 100, 100], 99.0, "SELL"),
    ([100, 100, 100], 100.2, "HOLD"),   # inside the band
    ([100], 101.0, "HOLD"),             # not enough history
])
def test_the_executor_maps_the_signal_to_the_declared_actions(prices, last, action):
    assert StrategyExecutor().decide(_spec(), _snap(last), prices).action == action


def test_an_entry_is_capped_by_the_position_limit_like_fixed_size():
    decision = StrategyExecutor().decide(_spec(quantity=50), _snap(101.0), [100, 100, 100])
    assert decision.quantity == 9  # 1000 // 101


@pytest.mark.parametrize("bad", [
    {"signal": "rsi"}, {"lookback": 1}, {"lookback": 79}, {"threshold_pct": 0},
    {"threshold_pct": 6}, {"when_above": "LEVERAGE"}, {"quantity": 0}, {"confidence": 1.5},
])
def test_admission_refuses_parameters_outside_the_dsl(bad):
    with pytest.raises(ValidationError):
        _spec(**bad)


def test_two_rules_with_the_same_parameters_are_one_behaviour():
    assert behavioural_signature(_spec()) == behavioural_signature(_spec(confidence=0.9))
    assert behavioural_signature(_spec()) != behavioural_signature(_spec(lookback=4))


def test_the_backtest_runs_the_same_signal_the_executor_does():
    t0 = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    prices = [100, 100, 100, 101, 102, 103, 102, 100, 99, 98]
    bars = [Bar(t0 + timedelta(minutes=5 * i), float(p)) for i, p in enumerate(prices)]
    result = bt.run_backtest(bars, strategy_id="r1", kind="RULE", parameters=BASE)
    assert not result.unsupported
    assert result.trades >= 1 and result.leakage_violations == 0
