"""The universe the Guardian will trade: the allowlist (default), the account, or any tradable US stock.

Widening it is the account holder's decision, made in the env file. These tests pin what widening
changes - which symbols pass - and, as important, everything it leaves alone."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.guardian import Guardian
from autopoiesis.models import AccountSnapshot, DataSnapshot, PositionSnapshot, TradeDecision

NOW = datetime.now(timezone.utc)


def _snap(symbol="ZZZ", price=50.0, positions=(), market_open=True, timestamp=None):
    return DataSnapshot(
        symbol=symbol, timestamp=timestamp or NOW, market_open=market_open, last_price=price, source="alpaca",
        account=AccountSnapshot(equity=100_000, cash=100_000, buying_power=300_000, portfolio_value=100_000, daily_loss=0),
        positions=positions, open_orders=(),
    )


def _buy(symbol="ZZZ", action="BUY", qty=2):
    return TradeDecision(symbol=symbol, action=action, quantity=qty, confidence=0.7, rationale="t")


def _g(universe="allowlist", tradable=None, **kw):
    return Guardian(allowlist={"SPY"}, max_position_value=25_000, max_daily_loss=500, max_total_exposure=kw.pop("cap", None),
                    universe=universe, tradable=tradable, **kw)


def test_the_default_universe_is_the_allowlist_and_nothing_else_passes():
    r = _g().review(_buy(), _snap(), mode="paper", agent_position_quantity=0)
    assert not r.approved and r.reason == "symbol is outside the allowlist"
    assert _g().review(_buy("SPY"), _snap("SPY"), mode="paper", agent_position_quantity=0).approved


def test_the_account_universe_adds_what_is_held_and_only_that():
    held = (PositionSnapshot(symbol="ZZZ", quantity=10, market_value=500.0),)
    g = _g("account")
    assert g.review(_buy(), _snap(positions=held), mode="paper", agent_position_quantity=10).approved
    r = g.review(_buy("QQQ"), _snap("QQQ", positions=held), mode="paper", agent_position_quantity=0)
    assert not r.approved and r.reason == "symbol is outside the allowlist"


def test_the_tradable_universe_admits_a_listed_stock_and_refuses_the_rest():
    tradable = frozenset({"ZZZ", "WWWWW", "ABCDW"})
    g = _g("tradable", tradable)
    assert g.review(_buy(), _snap(), mode="paper", agent_position_quantity=0).approved
    assert g.review(_buy("QQQ"), _snap("QQQ"), mode="paper", agent_position_quantity=0).reason == "symbol is not a tradable US equity on a major exchange"
    assert g.review(_buy("ABCDW"), _snap("ABCDW"), mode="paper", agent_position_quantity=0).reason == "symbol is a warrant, unit or right, not a stock"
    assert g.review(_buy(), _snap(price=3.0), mode="paper", agent_position_quantity=0).reason == "symbol is priced below the minimum"


def test_an_unknown_universe_refuses_rather_than_guess_and_a_provider_is_asked_each_time():
    assert _g("tradable", None).review(_buy(), _snap(), mode="paper", agent_position_quantity=0).reason.startswith("the tradable universe is unknown")
    calls = []
    sets = iter([None, frozenset({"ZZZ"})])
    g = _g("tradable", lambda: calls.append(1) or next(sets))
    assert not g.review(_buy(), _snap(), mode="paper", agent_position_quantity=0).approved
    assert g.review(_buy(), _snap(), mode="paper", agent_position_quantity=0).approved and len(calls) == 2


@pytest.mark.parametrize("universe", ["account", "tradable"])
def test_widening_loosens_the_universe_and_nothing_else(universe):
    held = (PositionSnapshot(symbol="ZZZ", quantity=10, market_value=500.0),)
    g = _g(universe, frozenset({"ZZZ"}))
    # live mode, a closed market, a position past the cap, and a stale snapshot are all still refused
    assert g.review(_buy(), _snap(positions=held), mode="live", agent_position_quantity=10).reason == "only paper mode is allowed"
    assert g.review(_buy(), _snap(positions=held, market_open=False), mode="paper", agent_position_quantity=10).reason == "market is closed"
    big = _buy(qty=600)  # 600 x 50 = 30,000 against a 25,000 position cap
    assert "max_position_value" in g.review(big, _snap(positions=held), mode="paper", agent_position_quantity=10).reason
    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert g.review(_buy(), _snap(positions=held, timestamp=old), mode="paper", agent_position_quantity=10).reason == "data snapshot is stale"


def test_the_exposure_cap_measures_the_whole_account_once_the_universe_is_wider():
    other = (PositionSnapshot(symbol="TLT", quantity=100, market_value=20_000.0),)   # held, not on the allowlist
    narrow = _g("allowlist", cap=20_500).review(_buy("SPY", qty=20), _snap("SPY", price=100.0, positions=other), mode="paper", agent_position_quantity=0)
    wide = _g("account", cap=20_500).review(_buy("SPY", qty=20), _snap("SPY", price=100.0, positions=other), mode="paper", agent_position_quantity=0)
    assert narrow.approved and not wide.approved and "exposure" in wide.reason


def test_selling_what_the_account_holds_was_always_allowed_and_stays_so():
    held = (PositionSnapshot(symbol="ZZZ", quantity=10, market_value=500.0),)
    assert _g("allowlist").review(_buy(action="SELL", qty=5), _snap(positions=held), mode="paper", agent_position_quantity=10).approved
