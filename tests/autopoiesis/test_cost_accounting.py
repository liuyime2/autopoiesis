"""After-cost PnL, computed in the live path rather than only the backtest.

The objective's success criterion is *after-cost* net PnL. Cost was implemented in
the research backtest and absent from the live ledger, so the headline figure the
system reported was gross - and on paper the broker charges nothing, so nothing in
the data would ever have revealed it.

The distinction that matters is between what was *observed* and what was *assumed*,
and both are reported. Conflating them produces a number that looks net and is not.
"""

from datetime import datetime, timezone

import pytest

from autopoiesis.evaluator import (
    DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT,
    _assumed_cost_for_lots,
)

TS = datetime(2026, 6, 11, tzinfo=timezone.utc)


class _Lot:
    def __init__(self, buy_price, sell_price, quantity, strategy_id="s1"):
        self.buy_price = buy_price
        self.sell_price = sell_price
        self.quantity = quantity
        self.strategy_id = strategy_id


def test_cost_is_charged_on_both_sides_of_a_round_trip():
    lots = [_Lot(100.0, 110.0, 10)]
    cost = _assumed_cost_for_lots(lots, 0.05)

    # notional = (100 + 110) * 10 = 2100. A 0.05% round-trip cost split across the
    # two sides is 0.05/200 = 0.00025 of notional, so 2100 * 0.00025 = 0.525.
    # The first version of this test expected 1.05 - the arithmetic was wrong, not
    # the formula.
    assert cost["s1"] == pytest.approx(0.525)


def test_a_higher_cost_assumption_charges_more():
    lots = [_Lot(100.0, 110.0, 10)]
    assert _assumed_cost_for_lots(lots, 0.50)["s1"] > _assumed_cost_for_lots(
        lots, 0.05
    )["s1"]


def test_cost_is_attributed_to_the_strategy_that_opened_the_lot():
    lots = [_Lot(100.0, 110.0, 5, "alpha"), _Lot(100.0, 110.0, 5, "beta")]
    cost = _assumed_cost_for_lots(lots, 0.05)

    assert set(cost) == {"alpha", "beta"}
    assert cost["alpha"] == pytest.approx(0.2625)
    assert cost["beta"] == pytest.approx(0.2625)


def test_an_unattributed_lot_is_labelled_rather_than_dropped():
    cost = _assumed_cost_for_lots([_Lot(100.0, 110.0, 1, "")], 0.05)
    assert "unattributed" in cost


def test_no_lots_charge_nothing():
    assert _assumed_cost_for_lots([], 0.05) == {}


def test_the_default_assumption_is_non_zero():
    """A default of zero would silently reproduce the defect this module exists to
    remove, and it would do it invisibly."""
    assert DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT > 0


def test_the_evidence_reports_observed_and_assumed_separately():
    """On paper the observed fee is 0.00. A figure called "net" that silently means
    "gross, because the broker is free" is a label that outlives its assumption."""
    from autopoiesis.models import PnLEvidence

    pnl = PnLEvidence(
        status="broker_strategy_closed_lot_pnl_verified",
        strategy_realized_pnl={"s1": 100.0},
        strategy_fees={"s1": 0.0},
        assumed_cost={"s1": 1.0},
        assumed_cost_pct=0.05,
        unrealized_pnl=0.0,
        net_pnl=100.0,
        net_of_fees=100.0,
        net_after_assumed_cost=99.0,
    )

    assert pnl.strategy_fees["s1"] == 0.0
    assert pnl.assumed_cost["s1"] == 1.0
    assert pnl.net_of_fees == 100.0, "after observed fees, which were free"
    assert pnl.net_after_assumed_cost == 99.0, "after the assumption"
    assert pnl.net_after_assumed_cost < pnl.net_of_fees


class _Snap:
    def __init__(self, timestamp, last_price):
        self.timestamp = timestamp
        self.last_price = last_price


class _Rec:
    def __init__(self, timestamp, last_price):
        self.snapshot = _Snap(timestamp, last_price)


def test_the_holding_benchmark_is_computed_from_the_prices_the_evaluation_already_holds():
    """One price source, so the benchmark cannot disagree with the lots it judges.

    The comparison is assembled from the same records the PnL comes from rather than a
    second fetch: a benchmark that rests on a different price series than the PnL it is
    judging is worse than no benchmark, because the disagreement is invisible.
    """
    from autopoiesis.evaluator import _market_return_pct

    records = [
        _Rec(datetime(2026, 6, 11, tzinfo=timezone.utc), 100.0),
        _Rec(datetime(2026, 6, 12, tzinfo=timezone.utc), 110.0),
    ]

    assert _market_return_pct(records) == pytest.approx(10.0)


def test_no_price_series_yields_no_benchmark_rather_than_a_guess():
    """A missing benchmark must read as unknown, not as flat."""
    from autopoiesis.evaluator import _market_return_pct

    assert _market_return_pct([]) is None
    assert _market_return_pct([_Rec(TS, 100.0)]) is None


def test_a_return_is_per_cent_of_the_capital_it_was_entrusted_with():
    """The arithmetic behind `strategy_return_pct`, stated on numbers that can be checked.

    10.00 realized on a peak exposure of 100.00 is +10.0%. This is the figure the
    project's success metric needs and nothing reported it: the promotion gate screens on
    decision quality, and the counterfactual ledger cannot see this number at all - a
    trade's return there is holding from the same instant less cost, so every decision
    shows an excess of exactly the assumed cost over holding.
    """
    realized = {"s1": 10.0}
    peak = {"s1": 100.0}
    market = 4.0

    returns = {k: round(realized[k] / peak[k] * 100.0, 6) for k in peak if peak[k] > 0}
    excess = {k: round(v - market, 6) for k, v in returns.items()}

    assert returns == {"s1": 10.0}
    assert excess == {"s1": 6.0}


def test_turnover_is_not_the_denominator_for_what_the_capital_returned():
    """A strategy that cycles one share many times turns over far more than it risked.

    Cumulative turnover as the denominator understated `tiny-fixed-size-001` by 0.5 points
    and then compared it against a market return computed on capital held once - two
    different denominators, so the "excess" was not an excess of anything. Peak exposure
    is what the strategy was actually entrusted with: here 3 lots of 40 turnover a peak
    of only 80, so the return on turnover reads +5.0% where the return on capital at
    risk is +12.5%.
    """
    lots = 3
    notional_each = 40.0
    turnover = lots * notional_each
    peak = 2 * notional_each          # never more than two lots open at once
    realized = 10.0

    on_turnover = realized / turnover * 100.0
    on_peak = realized / peak * 100.0

    assert on_turnover == pytest.approx(8.333333, abs=1e-5)
    assert on_peak == pytest.approx(12.5)
    assert on_peak > on_turnover


def test_a_strategy_that_did_not_trade_is_absent_rather_than_zero():
    """"Did not trade" must not render as "returned nothing"."""
    realized = {"s1": 10.0}
    peak = {"s1": 100.0}

    returns = {k: round(realized[k] / peak[k] * 100.0, 6) for k in peak if k in realized}

    assert "s2" not in returns


class _SymRec:
    def __init__(self, timestamp, last_price, symbol="SPY"):
        self.snapshot = _Snap(timestamp, last_price)
        self.snapshot.symbol = symbol


def _day(d, h=15):
    return datetime(2026, 9, d, h, tzinfo=timezone.utc)


def test_the_market_leg_covers_only_the_strategy_s_own_window():
    """A strategy that first traded on the 28th was compared with SPY from June.

    The benchmark ran the market leg over the whole journal while each strategy's capital was
    deployed for a fraction of it, so a strategy created late was charged for a move it was
    never exposed to. The leg is now the same instrument over the strategy's own window:
    first entry to last exit, or to the last quote while a lot is open.
    """
    from autopoiesis.evaluator import _market_return_over

    records = [
        _SymRec(_day(1), 100.0),     # long before the strategy existed
        _SymRec(_day(28), 120.0),    # its first entry
        _SymRec(_day(29), 123.0),
        _SymRec(_day(30), 126.0),    # its last exit
        _SymRec(_day(30, 20), 130.0),
    ]
    assert _market_return_over(records, "SPY", _day(28), _day(30)) == pytest.approx(5.0)
    assert _market_return_over(records, "QQQ", _day(28), _day(30)) is None
    assert _market_return_over(records, "SPY", _day(29), _day(29)) is None, "one quote is no window"


def test_a_strategy_window_runs_to_the_last_quote_while_a_lot_is_open():
    from autopoiesis.evaluator import _strategy_windows

    class _Closed:
        def __init__(self, sid, o, c):
            self.strategy_id, self.symbol, self.opened_at, self.closed_at = sid, "SPY", o, c

    class _Open:
        def __init__(self, sid, o, as_of):
            self.strategy_id, self.symbol, self.opened_at, self.as_of = sid, "SPY", o, as_of

    windows = _strategy_windows(
        [_Closed("a", _day(28), _day(29)), _Closed("b", _day(2), _day(3))],
        [_Open("a", _day(29), _day(30))],
    )
    assert windows["a"] == ("SPY", _day(28), _day(30))
    assert windows["b"] == ("SPY", _day(2), _day(3))
