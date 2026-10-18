"""The staleness gate measures the *quote*; the price's own age is reported, not gated.

Two measured defects, in order, and this file exists to keep the second from undoing the first.

The first: `DataSnapshot.timestamp` comes from the broker's *clock*, and `_latest_price` used to
keep the price and throw the trade's own timestamp away, so a cycle taken while the market was open
against a price the market had left behind looked perfectly fresh. The fix recorded the observation
time on the snapshot and made `_is_stale` consider it.

The second was created by the first: that field held the *trade*'s timestamp, so the gate refused a
symbol whose last print lagged its quote while its bid and ask were current. Measured 2026-10-18
across 2,495 journalled cycles: 82 refusals naming a stale price quote, 47 of them inside one hour
on 2026-10-15 with the reported age climbing from 1,172s to 4,834s for SPY of all things. SPY's last
print is never eighty minutes old during a session; the print *timestamp* had stopped advancing.
Refusing on it was refusing on the wrong fact.

So the snapshot now carries three separate facts - the clock (`timestamp`), when the quote was
observed (`quote_time`), and when the price printed (`price_time`) - and only the first two gate.
The account holder's decision of 2026-10-18 was to keep pricing decisions on the last trade rather
than the quote mid, which makes the price's age something the decision has to be *told*
(`last_trade_age_seconds` in the context) rather than something it can assume away.

Nothing here changes a limit, and nothing adds a bypass: the ceiling is the same, the check still
runs against a clock that cannot be skipped, and the fallback below is chosen so the gate can never
end up weaker than the one it replaces.

Usage: pytest tests/autopoiesis/test_guardian_trade_freshness.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autopoiesis.guardian import Guardian
from autopoiesis.models import AccountSnapshot, DataSnapshot, TradeDecision

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def _snapshot(
    quoted_at: datetime | None,
    *,
    price_time: datetime | None = None,
    symbol: str = "SPY",
) -> DataSnapshot:
    return DataSnapshot(
        symbol=symbol,
        timestamp=NOW,
        market_open=True,
        last_price=700.0,
        source="alpaca",
        account=AccountSnapshot(
            equity=100000.0, cash=50000.0, buying_power=150000.0, portfolio_value=100000.0, daily_loss=0.0
        ),
        quote_time=quoted_at,
        price_time=price_time,
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


def test_a_fresh_clock_with_an_hour_old_quote_is_refused():
    """The first defect, directly. The clock is now; the market stopped quoting an hour ago.

    `now` is passed explicitly because `NOW` is a fixed date and the wall clock has moved past it:
    without that, the *snapshot's* clock is the stale one and the test passes for the wrong reason -
    which is exactly how the first version of this test behaved.
    """
    result = _guardian().review(_decision(), _snapshot(NOW - timedelta(hours=1)), now=NOW)
    assert result.approved is False
    # The reason must name the *quote*, not the snapshot: the clock is fresh here, and a refusal
    # that says "data snapshot is stale" against a clock reading now is unactionable.
    assert "price quote" in result.reason.lower()


def test_a_fresh_quote_with_a_fresh_clock_is_approved():
    """The guard must not become a permanent disable. A quote from seconds ago trades."""
    result = _guardian().review(_decision(), _snapshot(NOW - timedelta(seconds=5)), now=NOW)
    assert result.approved is True


def test_a_snapshot_without_a_quote_time_is_not_refused_for_it():
    """Brokers that do not report one must not stop trading - the clock check still guards them.

    `quote_time` is optional precisely because not every source carries it, so an absent value is
    "nothing to check", not "stale". What it must not become is a silent pass when the value *is*
    present and old.
    """
    result = _guardian().review(_decision(), _snapshot(None), now=NOW)
    assert result.approved is True


def test_the_refusal_names_the_quote_not_the_clock():
    """An operator reading "data snapshot is stale" with a fresh clock has no way to act on it."""
    result = _guardian().review(_decision(), _snapshot(NOW - timedelta(hours=1)))
    assert "price" in result.reason.lower()


def test_a_stale_quote_is_refused_for_a_sell_too():
    """An exit sized against a dead market is the same hazard, in the direction that sells."""
    result = _guardian().review(_decision("SELL"), _snapshot(NOW - timedelta(hours=1)), now=NOW)
    assert result.approved is False


def test_the_quote_time_in_the_future_is_refused_rather_than_trusted():
    """A clock skew that puts the quote ahead of now is not a reason to be more confident in it."""
    result = _guardian().review(_decision(), _snapshot(NOW + timedelta(hours=2)), now=NOW)
    assert result.approved is False


def test_an_eighty_minute_old_print_beside_a_live_quote_trades():
    """The second defect, directly: the case all 82 measured refusals were.

    A live two-sided quote is what says the market for this symbol is open. The print behind the
    price can lag it - that is what a thin book looks like, and on 2026-10-15 what a frozen print
    timestamp looked like for every symbol at once - and refusing on the print was refusing on the
    wrong fact. What must not happen is the price's age disappearing: it is on the snapshot as
    `price_time`, and the decision context reports it.
    """
    result = _guardian().review(
        _decision(),
        _snapshot(NOW - timedelta(seconds=5), price_time=NOW - timedelta(seconds=4800)),
        now=NOW,
    )
    assert result.approved is True


def test_the_fallback_is_the_old_behaviour_not_a_disabled_check():
    """A quote outage must not turn the gate into "nothing to check".

    The gateway writes `quote_time = quoted_at or price_time`. Without the fallback a broker that
    stopped answering the quote endpoint would leave the field empty, and empty means "nothing to
    check" - strictly weaker than the check this replaces. This pins the composition the gateway
    performs, so the fallback cannot be dropped as redundant.
    """
    trade_time = NOW - timedelta(hours=1)
    quoted_at = None
    snapshot = _snapshot(quoted_at or trade_time, price_time=trade_time)
    assert snapshot.quote_time == trade_time
    assert _guardian().review(_decision(), snapshot, now=NOW).approved is False


def test_the_quote_being_live_does_not_hide_how_old_the_price_is():
    """The price's age is a fact on the snapshot, because the model is the one reader who needs it.

    The gate above deliberately lets this case through, so the age must survive somewhere a reader
    can find it - otherwise "the decision keeps using the last trade" becomes "the decision cannot
    tell how old its price is", which is the defect this whole file is about, wearing a new coat.
    """
    snapshot = _snapshot(NOW - timedelta(seconds=5), price_time=NOW - timedelta(seconds=4800))
    assert snapshot.price_time == NOW - timedelta(seconds=4800)
    assert snapshot.quote_time == NOW - timedelta(seconds=5)
