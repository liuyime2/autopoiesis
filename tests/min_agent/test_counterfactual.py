"""A decision is only right or wrong relative to what happened next.

864 of 920 journaled cycles are HOLD and until this existed nothing recorded what
happened next, so the system could count its holds but never learn whether they
were right. These tests pin the properties that make the measurement honest rather
than merely available.
"""

from datetime import datetime, timedelta, timezone

import pytest

from min_agent import counterfactual as cf
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)


def _record(cycle_id, when, price, action="HOLD", symbol="SPY"):
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=DataSnapshot(
            symbol=symbol,
            timestamp=when,
            market_open=True,
            last_price=price,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=50_000, buying_power=50_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol=symbol, action=action, quantity=0 if action == "HOLD" else 1,
            confidence=0.6, rationale="r",
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, message="hold", filled_quantity=0.0),
    )


T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def test_a_hold_before_a_fall_is_a_good_hold():
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=12), 99.0),
        _record("c", T0 + timedelta(hours=26), 95.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    row = report.rows[0]
    assert row.verdict == cf.GOOD_HOLD
    assert row.price_at_decision == 100.0
    assert row.price_at_horizon == 95.0
    assert row.gross_return_pct == pytest.approx(-5.0)
    assert row.net_return_pct == pytest.approx(-5.05), "cost is charged on both paths"


def test_a_hold_before_a_rally_is_missed_alpha():
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=12), 102.0),
        _record("c", T0 + timedelta(hours=26), 110.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    row = report.rows[0]
    assert row.verdict == cf.MISSED_ALPHA
    assert row.net_return_pct == pytest.approx(9.95)


def test_a_move_inside_the_dead_band_is_neutral_not_a_verdict():
    """Charging 0.05% of cost against a 0.02% move must not be scored as a good
    hold, or the count is decided by rounding."""
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=26), 100.02),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    assert report.rows[0].verdict == cf.NEUTRAL


def test_a_decision_with_no_later_quote_stays_pending_and_is_never_guessed():
    records = [_record("a", T0, 100.0)]
    report = cf.evaluate(records, horizon_hours=24.0)

    assert report.rows[0].verdict == "PENDING"
    assert report.rows[0].price_at_horizon is None
    assert report.rows[0].pending is True
    assert report.scored == [], "an unscoreable decision must not enter the counts"
    assert report.hold_quality() is None, "no scored holds means no quality figure"


def test_a_horizon_beyond_a_long_outage_is_refused_rather_than_scored():
    """The system was dead for 97 days. Scoring a 24h decision against a quote 97
    days later is confident and wrong, so the row is dropped as a gap."""
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(days=97), 140.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0, max_gap_hours=72.0)

    assert report.rows[0].verdict == "GAP"
    assert report.gap_count == 1
    assert report.scored == []


def test_repeated_quotes_are_not_new_observations():
    """49% of consecutive journaled quotes are byte-identical, because the gateway
    reports the same last trade when nothing traded. Counting them made half the
    horizons a move of exactly zero - an artefact of the polling interval."""
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=1), 100.0),
        _record("c", T0 + timedelta(hours=2), 100.0),
        _record("d", T0 + timedelta(hours=26), 101.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    assert len(report.rows) == 2, "the two repeats collapse into the original quote"
    assert report.rows[0].verdict == cf.MISSED_ALPHA


def test_a_sell_that_lost_is_a_false_trade():
    """A decision that actually traded is scored with the sign of the side taken,
    so a losing sell is a false trade rather than a missed alpha."""
    records = [
        _record("a", T0, 100.0, action="SELL"),
        _record("b", T0 + timedelta(hours=12), 101.0),
        _record("c", T0 + timedelta(hours=26), 110.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    row = report.rows[0]
    assert row.action == "SELL"
    assert row.verdict == cf.FALSE_TRADE, "the price rose, so selling lost money"
    assert row.gross_return_pct == pytest.approx(10.0)


def test_hold_quality_counts_only_holds():
    """A false trade says the model should not have traded; it is not evidence that
    a hold was good."""
    records = [
        # Distinct prices throughout: a repeat would be collapsed as a
        # non-observation and the rows would not line up.
        _record("h", T0, 100.0, action="HOLD"),
        _record("x", T0 + timedelta(hours=12), 99.0, action="BUY"),
        _record("h2", T0 + timedelta(hours=26), 90.0),
        _record("y", T0 + timedelta(hours=37), 80.0),
        _record("h3", T0 + timedelta(hours=50), 120.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    assert report.rows[0].verdict == cf.GOOD_HOLD
    assert report.rows[1].verdict == cf.FALSE_TRADE
    # Two scored holds - h was good, h2 missed a rally - so 0.5. The false trade
    # is not in the denominator.
    assert [r.action for r in report.scored] == ["HOLD", "BUY", "HOLD"]
    assert report.hold_quality() == pytest.approx(0.5)


def test_the_assumed_cost_is_recorded_on_every_row():
    """The paper broker reports zero commission on all 24 fills, so a gross-only
    counterfactual would flatter the system by what it would really have paid."""
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=26), 101.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0, assumed_cost_pct=0.25)

    assert report.rows[0].assumed_cost_pct == 0.25
    assert report.rows[0].net_return_pct == pytest.approx(1.0 - 0.25)


def test_a_winning_fill_is_a_good_trade():
    """The missing primitive: a filled order that gained had no verdict of its own.

    The branch was `FALSE_TRADE if signed < -dead_band else NEUTRAL`, so a winning fill
    landed on NEUTRAL - the same label as a move too small to clear the cost - and NEUTRAL
    is excluded from the offline screen's `scored`. A strategy's scored count was therefore
    composed only of its mistakes, and the promotion gate rejected on any of them. On the
    live journal: 34 model-authored trades, 26 of them winners, all 26 invisible to the
    screen, and no trading strategy has ever passed it.
    """
    records = [
        _record("buy", T0, 100.0, action="BUY"),
        _record("later", T0 + timedelta(hours=5), 110.0),
    ]

    report = cf.evaluate(records, symbol="SPY", horizon_hours=4.0)

    trade = next(r for r in report.rows if r.action == "BUY")
    assert trade.verdict == cf.GOOD_TRADE
    # 10% gross, less the 0.05% assumed round-trip cost, which every row is charged.
    assert trade.net_return_pct == pytest.approx(9.95)


def test_a_losing_fill_is_still_a_false_trade():
    records = [
        _record("buy", T0, 100.0, action="BUY"),
        _record("later", T0 + timedelta(hours=5), 90.0),
    ]

    report = cf.evaluate(records, symbol="SPY", horizon_hours=4.0)

    trade = next(r for r in report.rows if r.action == "BUY")
    assert trade.verdict == cf.FALSE_TRADE


def test_a_fill_inside_the_dead_band_is_still_neutral():
    """Cost is still the bar: a move that did not clear it teaches nothing either way."""
    records = [
        _record("buy", T0, 100.0, action="BUY"),
        _record("later", T0 + timedelta(hours=5), 100.01),
    ]

    report = cf.evaluate(records, symbol="SPY", horizon_hours=4.0)

    trade = next(r for r in report.rows if r.action == "BUY")
    assert trade.verdict == cf.NEUTRAL


def test_a_short_that_gains_is_a_good_trade():
    """The side taken is signed, so a winning SELL is scored the same as a winning BUY."""
    records = [
        _record("sell", T0, 100.0, action="SELL"),
        _record("later", T0 + timedelta(hours=5), 90.0),
    ]

    report = cf.evaluate(records, symbol="SPY", horizon_hours=4.0)

    trade = next(r for r in report.rows if r.action == "SELL")
    assert trade.verdict == cf.GOOD_TRADE
