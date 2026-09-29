"""What the agent is holding, not only what it has closed.

Only realized PnL was attributed. A position the agent was holding contributed
nothing to its own record, so the account figure and the agent figure could never be
reconciled even in principle - one measured closed trades, the other measured
everything, and the gap could only be explained away.

The property that matters most is the refusal: an open lot whose price is unknown is
reported as unpriced, not valued at its entry cost. Marking a position at its own cost
reports it as worth exactly nothing, which is a fabricated number wearing the
appearance of a measurement.
"""

from datetime import datetime, timezone

import pytest

from min_agent.evaluator import _open_lots, _Lot, _LotLedger
from min_agent.models import OpenLotAttribution

TS = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _lot(quantity=5, price=100.0, strategy_id="s1"):
    return _Lot(
        quantity=quantity, price=price, fees=0.0, fill_id="f1",
        order_id="o1", client_order_id="c1", opened_at=TS, source="broker",
        strategy_id=strategy_id,
    )


def _ledger(lots):
    return _LotLedger(lots={"SPY": list(lots)})


def test_an_open_lot_is_marked_to_the_last_observed_price():
    result = _open_lots(_ledger([_lot()]), {"SPY": 110.0}, TS)

    assert len(result) == 1
    lot = result[0]
    assert lot.entry_price == 100.0
    assert lot.current_price == 110.0
    assert lot.unrealized_pnl == pytest.approx(50.0)
    assert lot.unrealized_pnl_pct == pytest.approx(0.10)
    assert lot.as_of == TS


def test_a_losing_open_lot_reports_a_negative_number():
    lot = _open_lots(_ledger([_lot()]), {"SPY": 90.0}, TS)[0]
    assert lot.unrealized_pnl == pytest.approx(-50.0)


def test_an_unpriced_lot_is_reported_as_unpriced_not_as_worthless():
    """The failure this rule exists to prevent. Valuing an open position at its entry
    price reports it as worth exactly nothing, which looks like a measurement and is
    an invention."""
    lot = _open_lots(_ledger([_lot()]), {}, TS)[0]

    assert lot.current_price is None
    assert lot.unrealized_pnl is None
    assert lot.unrealized_pnl_pct is None
    assert lot.entry_price == 100.0, "the cost is still known even when the price is not"


def test_a_zero_quantity_lot_is_not_reported_as_open():
    assert _open_lots(_ledger([_lot(quantity=0)]), {"SPY": 110.0}, TS) == ()


def test_open_lots_keep_the_strategy_that_opened_them():
    """The same attribution rule the realized side uses: the strategy that opened a
    lot is the one expected to realize its PnL, which may not be the strategy that
    eventually sells it."""
    result = _open_lots(
        _ledger([_lot(strategy_id="opener")]), {"SPY": 110.0}, TS
    )
    assert result[0].strategy_id == "opener"


def test_an_unattributed_lot_is_labelled_rather_than_dropped():
    result = _open_lots(_ledger([_lot(strategy_id="")]), {"SPY": 110.0}, TS)
    assert result[0].strategy_id == "unattributed"


def test_several_open_lots_are_all_reported():
    result = _open_lots(
        _ledger([_lot(quantity=2, price=100.0), _lot(quantity=3, price=120.0)]),
        {"SPY": 110.0}, TS,
    )
    assert len(result) == 2
    assert sum(l.unrealized_pnl for l in result) == pytest.approx(20.0 + (-30.0))


def test_a_lot_with_no_strategy_is_not_reported_as_the_empty_ledger():
    assert _open_lots(_LotLedger(), {"SPY": 110.0}, TS) == ()


def test_the_model_refuses_impossible_values():
    with pytest.raises(ValueError):
        OpenLotAttribution(
            strategy_id="s1", symbol="SPY", quantity=0, entry_price=100.0,
            opened_at=TS,
        )
    with pytest.raises(ValueError):
        OpenLotAttribution(
            strategy_id="s1", symbol="SPY", quantity=1, entry_price=0.0,
            opened_at=TS,
        )
