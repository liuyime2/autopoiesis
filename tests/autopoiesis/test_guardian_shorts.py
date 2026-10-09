"""The short-selling rule: every refusal path, and proof that it relaxes nothing else.

See docs/history/plans/2026-10-07-bounded-short-and-rule-dsl.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.guardian import Guardian
from autopoiesis.models import AccountSnapshot, DataSnapshot, PositionSnapshot, TradeDecision


def snapshot(*, market_open=True, daily_loss=0, positions=()):
    return DataSnapshot(
        symbol="SPY", timestamp=datetime.now(timezone.utc), market_open=market_open,
        last_price=100, source="alpaca",
        account=AccountSnapshot(equity=100_000, cash=100_000, buying_power=100_000,
                                portfolio_value=100_000, daily_loss=daily_loss),
        positions=positions,
    )


def _guardian(shorts="paper", **limits):
    return Guardian(
        allowlist={"SPY"}, max_position_value=limits.get("max_position_value", 1_000),
        max_daily_loss=500, max_total_exposure=limits.get("max_total_exposure"), shorts=shorts,
    )


def _decision(action, quantity=3, symbol="SPY"):
    return TradeDecision(symbol=symbol, action=action, quantity=quantity, confidence=0.65, rationale="r")


def _long(quantity, price=100):
    return (PositionSnapshot(symbol="SPY", quantity=quantity, market_value=quantity * price),)


def test_a_bounded_short_on_a_flat_account_is_approved():
    result = _guardian().review(_decision("SHORT"), snapshot(), agent_short_quantity=0)
    assert result.approved, result.reason


@pytest.mark.parametrize("action", ["SHORT", "COVER"])
def test_shorts_off_by_default_refuse_both_actions(action):
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    result = guardian.review(_decision(action), snapshot(), agent_short_quantity=5)
    assert not result.approved and "short selling is off" in result.reason


def test_a_short_against_any_long_is_refused():
    """Alpaca nets long and short: the 'short' would sell the owner's shares first."""
    result = _guardian().review(_decision("SHORT"), snapshot(positions=_long(6)), agent_short_quantity=0)
    assert not result.approved and "holds 6 SPY long" in result.reason


def test_a_short_is_capped_by_the_position_limit():
    result = _guardian().review(_decision("SHORT", 8), snapshot(), agent_short_quantity=3)
    assert not result.approved and "short against a max_position_value" in result.reason


def test_an_unknown_agent_short_is_bounded_by_the_account_s_own_short():
    short_8 = (PositionSnapshot(symbol="SPY", quantity=-8, market_value=-800),)
    result = _guardian().review(_decision("SHORT", 3), snapshot(positions=short_8), agent_short_quantity=None)
    assert not result.approved and "max_position_value" in result.reason


def test_a_short_counts_toward_total_exposure_gross():
    short_8 = (PositionSnapshot(symbol="SPY", quantity=-8, market_value=-800),)
    guardian = _guardian(max_position_value=5_000, max_total_exposure=1_000)
    result = guardian.review(_decision("SHORT", 3), snapshot(positions=short_8), agent_short_quantity=8)
    assert not result.approved and "exceeds the 1000.00 limit" in result.reason


def test_a_short_outside_the_allowlist_is_refused():
    result = _guardian().review(_decision("SHORT", symbol="QQQ"), snapshot().model_copy(update={"symbol": "QQQ"}),
                                agent_short_quantity=0)
    assert not result.approved and "allowlist" in result.reason


def test_cover_is_bounded_by_what_the_agent_shorted():
    result = _guardian().review(_decision("COVER", 5), snapshot(), agent_short_quantity=3)
    assert not result.approved and "the agent is short 3" in result.reason


def test_cover_with_an_unknown_short_is_refused():
    result = _guardian().review(_decision("COVER", 1), snapshot(), agent_short_quantity=None)
    assert not result.approved and "unknown" in result.reason


def test_cover_within_the_agent_s_short_is_approved():
    short_3 = (PositionSnapshot(symbol="SPY", quantity=-3, market_value=-300),)
    result = _guardian().review(_decision("COVER", 3), snapshot(positions=short_3), agent_short_quantity=3)
    assert result.approved, result.reason


def test_existing_order_gates_still_apply_to_shorts():
    result = _guardian().review(_decision("SHORT"), snapshot(market_open=False), agent_short_quantity=0)
    assert not result.approved and result.reason == "market is closed"
    result = _guardian().review(_decision("SHORT"), snapshot(daily_loss=600), agent_short_quantity=0)
    assert not result.approved and result.reason == "daily loss limit reached"


def test_enabling_shorts_relaxes_no_sell_rule():
    """SELL still needs the agent's own long; a SELL beyond it is not reinterpreted as a short."""
    result = _guardian().review(_decision("SELL", 5), snapshot(positions=_long(5)),
                                agent_position_quantity=2, agent_short_quantity=0)
    assert not result.approved and "the agent holds 2" in result.reason


def test_shadow_shorts_route_only_shorts_to_the_shadow():
    from autopoiesis.models import GuardianResult
    from autopoiesis.shadow import ShadowShorts

    class _Sink:
        def __init__(self, name):
            self.name, self.seen = name, []

        def execute(self, decision, guardian_result, *, cycle_id=None):
            self.seen.append(decision.action)
            return self.name

    real, shadow = _Sink("real"), _Sink("shadow")
    router = ShadowShorts(real=real, shadow=shadow)
    ok = GuardianResult(approved=True, reason="approved")
    assert [router.execute(_decision(a), ok) for a in ("BUY", "SHORT", "SELL", "COVER")] == [
        "real", "shadow", "real", "shadow"]


def test_short_and_cover_are_submitted_as_sell_and_buy():
    from autopoiesis.models import BROKER_SIDE

    assert BROKER_SIDE == {"BUY": "buy", "SELL": "sell", "SHORT": "sell", "COVER": "buy"}


def test_the_shorts_setting_refuses_a_value_it_does_not_know(monkeypatch):
    from autopoiesis.config import _choice

    monkeypatch.setenv("MIN_AGENT_SHORTS", "yes")
    with pytest.raises(ValueError, match="off, shadow, paper"):
        _choice("MIN_AGENT_SHORTS", ("off", "shadow", "paper"), "off")


def test_a_short_position_from_the_broker_parses():
    """market_value was constrained >= 0: the first real short would have failed every snapshot."""
    position = PositionSnapshot(symbol="spy", quantity=-3, market_value=-2320.5)
    assert (position.symbol, position.quantity) == ("SPY", -3)
    with pytest.raises(ValueError, match="sign of quantity"):
        PositionSnapshot(symbol="SPY", quantity=3, market_value=-2320.5)
