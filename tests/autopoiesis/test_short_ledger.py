"""Short lots in the account ledger.

A short opens only from a SELL whose order was a SHORT decision; a BUY covers shorts before
it opens a long; a SELL that was not a SHORT and has no long to match stays unmatched - those
are the account owner's shares, never a short the agent took.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis.evaluator import _apply_activity, _LotLedger, _open_lots
from autopoiesis.models import BrokerFillActivity

T0 = datetime(2026, 10, 7, 14, tzinfo=timezone.utc)


def _fill(n, side, quantity, price, minutes=0):
    return BrokerFillActivity(
        activity_id=f"f{n}", order_id=f"o{n}", client_order_id=f"c{n}", symbol="SPY", side=side,
        quantity=quantity, price=price, transaction_time=T0 + timedelta(minutes=minutes),
    )


def test_a_short_decision_opens_a_short_and_a_buy_covers_it_for_a_gain():
    ledger = _LotLedger()
    _apply_activity(ledger, _fill(1, "SELL", 3, 100.0), strategy_id="s", opens_short=True)
    assert ledger.short_lots["SPY"][0].quantity == 3
    _apply_activity(ledger, _fill(2, "BUY", 3, 95.0, 30), strategy_id="s")
    (lot,) = ledger.tracker("s").closed_lots
    assert (lot.direction, lot.sell_price, lot.buy_price) == ("SHORT", 100.0, 95.0)
    assert lot.realized_pnl == pytest.approx(15.0)
    assert not ledger.short_lots["SPY"] and not ledger.lots.get("SPY")


def test_a_sell_that_was_not_a_short_never_opens_one():
    """The 29-share case: selling beyond the agent's longs is the owner's shares, not a short."""
    ledger = _LotLedger()
    _apply_activity(ledger, _fill(1, "SELL", 29, 766.57), strategy_id="s")
    assert not ledger.short_lots.get("SPY")
    assert ledger.tracker("s").unmatched_sell_quantity == 29


def test_a_buy_covers_shorts_first_and_the_remainder_opens_a_long():
    ledger = _LotLedger()
    _apply_activity(ledger, _fill(1, "SELL", 2, 100.0), strategy_id="s", opens_short=True)
    _apply_activity(ledger, _fill(2, "BUY", 5, 98.0, 10), strategy_id="s")
    assert ledger.tracker("s").realized_pnl == pytest.approx(4.0)
    assert [lot.quantity for lot in ledger.lots["SPY"]] == [3]


def test_an_open_short_is_marked_to_market_in_the_short_s_favour_when_price_falls():
    ledger = _LotLedger()
    _apply_activity(ledger, _fill(1, "SELL", 3, 100.0), strategy_id="s", opens_short=True)
    (lot,) = _open_lots(ledger, {"SPY": 90.0}, T0)
    assert (lot.direction, lot.unrealized_pnl, lot.unrealized_pnl_pct) == ("SHORT", 30.0, 0.1)


def test_a_short_sell_first_closes_the_agent_s_own_long():
    ledger = _LotLedger()
    _apply_activity(ledger, _fill(1, "BUY", 2, 100.0), strategy_id="s")
    _apply_activity(ledger, _fill(2, "SELL", 5, 110.0, 10), strategy_id="s", opens_short=True)
    assert ledger.tracker("s").realized_pnl == pytest.approx(20.0)
    assert ledger.short_lots["SPY"][0].quantity == 3
