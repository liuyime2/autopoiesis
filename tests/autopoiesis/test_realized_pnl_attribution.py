"""Realized PnL belongs to whoever sold, not to whoever bought.

Measured 2026-10-13 on the live journal, after the first open session on source
`9fb9066d820f27c9`. The attribution report credited +760.03 of account-level realized PnL to

    tiny-fixed-size-001        +378.18   bought 17, sold 0
    trend-follow-buy-001       +147.51   bought  9, sold 0
    fixed-size-buy-001         +146.94   bought 16, sold 0
    fixed-size-probe-0001       +87.40   bought 33, sold 0

Every one of those four had **zero sells**. No agent strategy in the entire journal had ever
both bought and sold, so every strategy carrying a `closed_quantity` was one that had never
sold. The account figure of +760.03 was never wrong - in a shared account, selling shares the
account bought lower really does realize a gain - but attributing it to the opener handed the
whole of it to four never-selling strategies and reported them as the library's best
performers, while `trend-follow-sell-002` and `trend-follow-20261011-001`, which actually
executed every one of those sells, earned nothing.

`closed_quantity` was the worse half. It read as "this strategy closed N shares" when it
meant "N of this strategy's shares were closed".

The lot keeps both identities: `strategy_id` is the opener, because that is whose cost basis
was consumed and whose open lots are still valued, and `closing_strategy_id` is the seller.
`by_strategy` now credits the seller and `by_opening_strategy` carries the entry attribution
as a diagnostic, because the two answer different questions and reporting only one could be
read as either claim.

Usage: pytest tests/autopoiesis/test_realized_pnl_attribution.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis.attribution import attribute
from autopoiesis.evaluator import closing_strategy_of
from autopoiesis.models import ClosedLotAttribution

BASE = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)

_LOT = {
    "symbol": "SPY", "buy_fill_id": "b", "sell_fill_id": "s", "buy_order_id": "b",
    "sell_order_id": "s", "buy_client_order_id": None, "sell_client_order_id": None,
    "quantity": 10, "buy_price": 90.0, "sell_price": 110.0, "buy_fees": 0.0, "sell_fees": 0.0,
    "realized_pnl": 200.0, "opened_at": BASE, "closed_at": BASE + timedelta(hours=5),
    "source": "llm",
}


def _payload(lots):
    # `attribute()` is handed the evaluator's JSON dump, not its models: it reads every
    # field with `.get`, so a list of models is not a supported input.
    return {
        "closed_lots": [lot.model_dump(mode="json") for lot in lots],
        "open_lots": [], "status": "verified",
    }


def _cross_strategy_lot():
    """The shape that was on the live record: A bought, a different B sold."""
    return ClosedLotAttribution(strategy_id="the-buyer", closing_strategy_id="the-seller", **_LOT)


def _solo_lot():
    return ClosedLotAttribution(strategy_id="solo", closing_strategy_id=None, **_LOT)


# --- the defect --------------------------------------------------------------


def test_realized_pnl_is_credited_to_the_strategy_that_sold():
    report = attribute([], _payload([_cross_strategy_lot()]))

    assert report.by_strategy == {"the-seller": 200.0}
    assert "the-buyer" not in report.by_strategy, (
        "a strategy that bought and held has realized nothing; crediting it is how a "
        "never-selling strategy gets reported as the library's best performer"
    )


def test_the_entry_attribution_is_kept_as_a_diagnostic():
    """Both answers are useful; publishing only one lets either be read as the claim."""
    report = attribute([], _payload([_cross_strategy_lot()]))

    assert report.by_opening_strategy == {"the-buyer": 200.0}


def test_the_account_total_is_unaffected_by_who_is_credited():
    """The account really did realise 200. Only the split between strategies is a choice."""
    report = attribute([], _payload([_cross_strategy_lot()]))

    assert report.total_realized_pnl == 200.0
    assert report.closed_lots == 1
    assert sum(report.by_strategy.values()) == report.total_realized_pnl
    assert sum(report.by_opening_strategy.values()) == report.total_realized_pnl


def test_a_lot_its_own_strategy_closed_is_credited_to_that_strategy():
    """The common case must not change: no cross-strategy close, no second identity."""
    report = attribute([], _payload([_solo_lot()]))

    assert report.by_strategy == {"solo": 200.0}
    assert report.by_opening_strategy == {"solo": 200.0}


def test_two_sellers_of_one_buyers_lot_each_keep_their_own_share():
    """A split close must not pool into one strategy or lose the opener entirely."""
    half = dict(_LOT, quantity=5, realized_pnl=100.0)
    lots = [
        ClosedLotAttribution(strategy_id="the-buyer", closing_strategy_id="seller-a", **half),
        ClosedLotAttribution(strategy_id="the-buyer", closing_strategy_id="seller-b", **half),
    ]

    report = attribute([], _payload(lots))

    assert report.by_strategy == {"seller-a": 100.0, "seller-b": 100.0}
    assert report.by_opening_strategy == {"the-buyer": 200.0}
    assert report.total_realized_pnl == 200.0


# --- the helper --------------------------------------------------------------


def test_closing_strategy_of_reads_the_closer_and_falls_back_to_the_opener():
    assert closing_strategy_of(_cross_strategy_lot()) == "the-seller"
    assert closing_strategy_of(_solo_lot()) == "solo"


@pytest.mark.parametrize("count", [1, 5])
def test_no_strategy_without_a_close_appears_in_the_closing_attribution(count):
    """The property that failed on the live record, stated so it cannot come back.

    Reproduced with fixtures rather than from the live journal, because the live journal is
    the account's and must not become one.
    """
    lots = [
        ClosedLotAttribution(strategy_id=f"never-sold-{i}", closing_strategy_id="seller", **_LOT)
        for i in range(count)
    ]

    report = attribute([], _payload(lots))

    assert report.by_strategy == {"seller": 200.0 * count}
    assert not any(k.startswith("never-sold") for k in report.by_strategy)
    assert set(report.by_opening_strategy) == {f"never-sold-{i}" for i in range(count)}
