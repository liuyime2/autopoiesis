"""A fresh clock with a stale price must still refuse an order.

Measured defect, not hypothetical: `DataSnapshot.timestamp` comes from the broker's *clock*, and
`AlpacaDataGateway._latest_price` keeps the price and throws the trade's own timestamp away. So a
cycle taken while the market is open - the clock says now - against a quote that has not printed for
an hour looks perfectly fresh, and the Guardian's staleness check passes it.

When does that happen in practice? The latest trade for a thinly traded symbol under a widened
universe can be much older than the clock: a $6 stock that trades twice an hour yields a last price
from forty minutes ago, and every risk figure computed from it - position value, exposure, the
`min_price` floor, the volatility forecast's input - is computed against a price the market has
left behind. The refusal has to be the same one a stale snapshot gets, because it is the same
hazard.

The fix records the price's own observation time on the snapshot and makes `_is_stale` consider both
it and the clock. Nothing here changes a limit or adds a bypass; it closes a path through which a
stale price could reach the order path.

Usage: pytest tests/autopoiesis/test_guardian_trade_freshness.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autopoiesis.guardian import Guardian
from autopoiesis.models import AccountSnapshot, DataSnapshot, TradeDecision

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def _snapshot(price_time: datetime | None, *, symbol: str = "SPY") -> DataSnapshot:
    return DataSnapshot(
        symbol=symbol,
        timestamp=NOW,
        market_open=True,
        last_price=700.0,
        source="alpaca",
        account=AccountSnapshot(
            equity=100000.0, cash=50000.0, buying_power=150000.0, portfolio_value=100000.0, daily_loss=0.0
        ),
        quote_time=price_time,
    )


def _decision(action: str = "BUY") -> TradeDecision:
    return TradeDecision(symbol="SPY", action=action, quantity=1, confidence=0.9, rationale="t")


def _guardian() -> Guardian:
    return Guardian(
        allowlist=frozenset({"SPY"}),
        max_position_value=100000.0,
        max_daily_loss=100000.0,
        max_total_exposure=1000000.0,
        max_trades_per_day=100,
        min_confidence=0.5,
        max_snapshot_age_seconds=120,
    )


def test_a_fresh_clock_with_an_hour_old_price_is_refused():
    """The defect, directly. The clock is now; the price is from an hour ago.

    `now` is passed explicitly because `NOW` is a fixed date and the wall clock has moved past it:
    without that, the *snapshot's* clock is the stale one and the test passes for the wrong reason -
    which is exactly how the first version of this test behaved.
    """
    result = _guardian().review(_decision(), _snapshot(NOW - timedelta(hours=1)), now=NOW)
    assert result.approved is False
    # The reason must name the *price*, not the snapshot: the clock is fresh here, and a refusal
    # that says "data snapshot is stale" against a clock reading now is unactionable.
    assert "price quote" in result.reason.lower()


def test_a_fresh_price_with_a_fresh_clock_is_approved():
    """The guard must not become a permanent disable. A quote from seconds ago trades."""
    result = _guardian().review(_decision(), _snapshot(NOW - timedelta(seconds=5)), now=NOW)
    assert result.approved is True


def test_a_snapshot_without_a_price_time_is_not_refused_for_it():
    """Brokers that do not report one must not stop trading - the clock check still guards them.

    `quote_time` is optional precisely because not every source carries it, so an absent value is
    "nothing to check", not "stale". What it must not become is a silent pass when the value *is*
    present and old.
    """
    result = _guardian().review(_decision(), _snapshot(None), now=NOW)
    assert result.approved is True


def test_the_refusal_names_the_price_not_the_clock():
    """An operator reading "data snapshot is stale" with a fresh clock has no way to act on it."""
    result = _guardian().review(_decision(), _snapshot(NOW - timedelta(hours=1)))
    assert "price" in result.reason.lower()


def test_a_stale_price_is_refused_for_a_sell_too():
    """An exit sized against a stale price is the same hazard, in the direction that sells."""
    result = _guardian().review(_decision("SELL"), _snapshot(NOW - timedelta(hours=1)), now=NOW)
    assert result.approved is False


def test_the_price_time_in_the_future_is_refused_rather_than_trusted():
    """A clock skew that puts the quote ahead of now is not a reason to be more confident in it."""
    result = _guardian().review(_decision(), _snapshot(NOW + timedelta(hours=2)), now=NOW)
    assert result.approved is False
