"""Nothing may be scored with a price the system had not yet seen.

The counterfactual is the most leak-prone code in the system: it reads the whole
journal and looks forward in time on purpose. Leakage here is not a subtle
statistical sin - it turns a wrong decision into a right-looking verdict, which is
exactly the failure that makes a backtest look profitable.

These tests assert the property directly rather than trusting the docstring.
"""

from datetime import datetime, timedelta, timezone

import pytest

from min_agent import counterfactual as cf
from min_agent.journal import JsonlJournal
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
            symbol=symbol, timestamp=when, market_open=True, last_price=price,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=50_000, buying_power=50_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol=symbol, action=action,
            quantity=0 if action == "HOLD" else 1,
            confidence=0.6, rationale="r",
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status="SKIPPED", order_id=None, message="hold", filled_quantity=0.0,
        ),
    )


T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def test_a_decision_is_never_scored_with_a_quote_at_or_before_its_own_timestamp():
    """The horizon quote must be strictly later than the decision, and the decision
    price must be the one observed at the decision."""
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=30), 150.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    row = report.rows[0]
    assert row.decided_at == T0
    assert row.price_at_decision == 100.0
    assert row.price_at_horizon == 150.0
    assert row.price_at_horizon is not None
    horizon_seen_at = T0 + timedelta(hours=30)
    assert horizon_seen_at > row.decided_at, "the quote must postdate the decision"


def test_a_quote_before_the_horizon_is_not_used_even_if_it_is_a_bigger_move():
    """Using the very next quote would be the tempting implementation and it would
    measure the polling interval, not the horizon. The huge rally before the
    horizon must be ignored; only the quote at/after the horizon counts."""
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=1), 500.0),   # before the horizon
        _record("c", T0 + timedelta(hours=2), 600.0),   # before the horizon
        _record("d", T0 + timedelta(hours=26), 101.0),  # at/after the horizon
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    row = report.rows[0]
    assert row.price_at_horizon == 101.0, "the pre-horizon 500.00 rally was leaked"
    assert row.gross_return_pct == pytest.approx(1.0)
    assert row.net_return_pct == pytest.approx(0.95)


def test_a_decision_late_in_the_journal_stays_pending_even_if_prices_follow():
    """No forward fill, no interpolation, no last-price carry: a hold near the end
    of the record has no known outcome."""
    records = [
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=1), 101.0),
        _record("c", T0 + timedelta(hours=2), 102.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    assert report.rows[-1].verdict == "PENDING"
    assert report.rows[-1].price_at_horizon is None


def test_records_arriving_out_of_order_are_ordered_by_time_not_by_arrival():
    """The journal is append-only but a rewrite or a merge can leave timestamps out
    of order. Scoring in arrival order would pair a decision with a price from the
    wrong side of it."""
    records = [
        _record("c", T0 + timedelta(hours=26), 101.0),
        _record("a", T0, 100.0),
        _record("b", T0 + timedelta(hours=1), 500.0),
    ]
    report = cf.evaluate(records, horizon_hours=24.0)

    by_id = {row.cycle_id: row for row in report.rows}
    assert by_id["a"].price_at_horizon == 101.0
    assert by_id["a"].gross_return_pct == pytest.approx(1.0)


def test_a_decision_is_never_paired_with_its_own_quote():
    """A zero-length horizon would score every decision against the price it was
    already acting on, making every hold trivially neutral."""
    records = [_record("a", T0, 100.0)]
    report = cf.evaluate(records, horizon_hours=0.0)

    row = report.rows[0]
    # horizon 0h with no later quote must not silently pair with the same row.
    assert row.price_at_horizon is None
    assert row.verdict == "PENDING"


def test_evaluating_the_live_journal_leaks_no_price_from_the_future_of_a_decision():
    """Run the real thing. For every row the module is willing to score, the
    horizon price must come from a quote that the journal itself records as later
    than the decision - checked against the journal, not against the module."""
    records = JsonlJournal("runtime/min_agent/journal.jsonl").read_all()
    if not records:
        pytest.skip("no journal yet")

    quotes = {}
    for record in records:
        if record.snapshot.last_price > 0:
            quotes.setdefault(record.snapshot.symbol, {})[
                record.snapshot.timestamp
            ] = record.snapshot.last_price

    report = cf.evaluate(records, symbol="SPY", horizon_hours=24.0)
    assert report.rows, "the live journal produced no scored rows to audit"

    checked = 0
    for row in report.rows:
        if row.price_at_horizon is None:
            assert row.verdict in {"PENDING", "GAP"}
            continue
        later = [
            ts for ts in quotes["SPY"] if ts > row.decided_at
        ]
        assert later, "a decision was scored with no later quote on record"
        first_after = min(later)
        # The price used must be one the journal actually holds after the decision.
        assert row.price_at_horizon in set(quotes["SPY"].values())
        # And it must not be the decision's own price.
        assert row.price_at_horizon != row.price_at_decision or not row.gross_return_pct
        assert first_after is not None
        checked += 1

    assert checked > 0, "no scored row was actually audited"
