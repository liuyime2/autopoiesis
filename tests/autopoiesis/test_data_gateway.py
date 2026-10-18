"""Regression tests for the broker data gateway.

The gateway previously called ``client.get_all_positions()`` and
``client.get_orders()``, neither of which exists on
``alpaca-trade-api 3.2.0``. Both raised ``AttributeError`` and were swallowed by
``except Exception: return ()``, so every snapshot carried an empty position book
and an empty open-order list for 851 consecutive cycles. The SELL guard, the
conflicting-order guard and the aggregate exposure cap were all inert as a
result, and nothing in the suite noticed.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from autopoiesis.data_gateway import AlpacaDataGateway, BrokerDataUnavailable


class FakeClock:
    def __init__(self, is_open=True):
        self.is_open = is_open
        self.timestamp = datetime.now(tz=timezone.utc)


class FakeAccount:
    def __init__(self, **overrides):
        self.equity = 100_000.0
        self.cash = 50_000.0
        self.buying_power = 100_000.0
        self.portfolio_value = 100_000.0
        self.last_equity = 99_500.0
        for key, value in overrides.items():
            setattr(self, key, value)


class FakeTrade:
    def __init__(self, price=500.0):
        self.price = price


class FakePosition:
    def __init__(self, symbol="SPY", qty=10, market_value=5000.0):
        self.symbol = symbol
        self.qty = qty
        self.market_value = market_value


class FakeOrder:
    def __init__(self, symbol="SPY", side="buy", qty=1, status="new"):
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.status = status
        self.id = "synthetic-order"


class FakeClient:
    def __init__(
        self,
        *,
        positions=(),
        orders=(),
        account=None,
        is_open=True,
        price=500.0,
        fail=None,
        bid=499.99,
        ask=500.01,
        quote_time=None,
    ):
        self._positions = list(positions)
        self._orders = list(orders)
        self._account = account or FakeAccount()
        self._is_open = is_open
        self._price = price
        self._fail = fail or set()
        self._bid = bid
        self._ask = ask
        self._quote_time = quote_time

    def _guard(self, name):
        if name in self._fail:
            raise ConnectionError(f"simulated {name} outage")

    def get_clock(self):
        self._guard("get_clock")
        return FakeClock(self._is_open)

    def get_account(self):
        self._guard("get_account")
        return self._account

    def get_latest_trade(self, symbol):
        self._guard("get_latest_trade")
        return FakeTrade(self._price)

    def get_latest_quote(self, symbol):
        self._guard("get_latest_quote")
        return SimpleNamespace(bid_price=self._bid, ask_price=self._ask, timestamp=self._quote_time)

    def get_asset(self, symbol):
        self._guard("get_asset")
        return SimpleNamespace(**{"class": "us_equity"})

    def list_positions(self):
        self._guard("list_positions")
        return self._positions

    def list_orders(self, status=None, limit=None):
        self._guard("list_orders")
        return self._orders[:limit or 50]


def test_gateway_uses_method_names_that_exist_on_the_real_alpaca_client():
    """The exact regression: wrong client method names, silently swallowed."""
    tradeapi = pytest.importorskip("alpaca_trade_api")

    for method in (
        "list_positions",
        "list_orders",
        "get_clock",
        "get_account",
        "get_latest_trade",
        "get_latest_quote",
        "get_asset",
    ):
        assert hasattr(tradeapi.REST, method), f"REST.{method} is required by AlpacaDataGateway"

    for removed in ("get_all_positions", "get_orders"):
        assert not hasattr(tradeapi.REST, removed), (
            f"REST.{removed} is unexpectedly present; if the SDK is upgraded past 3.2.0 "
            "re-check that the gateway still fails loudly rather than degrading to an empty result"
        )


def test_snapshot_carries_real_positions():
    client = FakeClient(positions=[FakePosition(qty=7, market_value=3500.0)])

    snapshot = AlpacaDataGateway(client=client).snapshot("SPY")

    assert [pos.symbol for pos in snapshot.positions] == ["SPY"]
    assert snapshot.positions[0].quantity == 7
    assert snapshot.positions[0].market_value == 3500.0


def test_snapshot_normalises_lowercase_broker_order_side():
    """Alpaca returns side as 'buy'; OpenOrderSnapshot is Literal['BUY','SELL'].

    Without normalisation every open order failed validation and was dropped,
    leaving the conflicting-order guard permanently inert.
    """
    client = FakeClient(orders=[FakeOrder(side="buy"), FakeOrder(side="sell")])

    snapshot = AlpacaDataGateway(client=client).snapshot("SPY")

    assert [order.side for order in snapshot.open_orders] == ["BUY", "SELL"]
    assert [order.status for order in snapshot.open_orders] == ["new", "new"]


def test_position_book_outage_raises_instead_of_degrading_to_empty():
    client = FakeClient(fail={"list_positions"})

    with pytest.raises(BrokerDataUnavailable, match="positions"):
        AlpacaDataGateway(client=client).snapshot("SPY")


def test_open_orders_outage_raises_instead_of_degrading_to_empty():
    client = FakeClient(fail={"list_orders"})

    with pytest.raises(BrokerDataUnavailable, match="open orders"):
        AlpacaDataGateway(client=client).snapshot("SPY")


def test_wrong_method_name_on_client_is_surfaced_not_hidden():
    """A client exposing only the pre-3.2.0 names must not look like a flat account."""

    class LegacyClient:
        def get_clock(self):
            return FakeClock()

        def get_account(self):
            return FakeAccount()

        def get_asset(self, symbol):
            return SimpleNamespace(**{"class": "us_equity"})

        def get_latest_trade(self, symbol):
            return FakeTrade()

        def get_all_positions(self):
            return [FakePosition()]

        def get_orders(self, status=None):
            return []

    with pytest.raises(BrokerDataUnavailable, match="positions"):
        AlpacaDataGateway(client=LegacyClient()).snapshot("SPY")


def test_unpriceable_position_raises_rather_than_vanishing_from_risk_checks():
    client = FakeClient(positions=[FakePosition()])
    client._positions[0].qty = "not-a-number"

    with pytest.raises(BrokerDataUnavailable, match="qty"):
        AlpacaDataGateway(client=client).snapshot("SPY")


def test_missing_position_quantity_raises():
    client = FakeClient(positions=[FakePosition()])
    client._positions[0].qty = None

    with pytest.raises(BrokerDataUnavailable, match="missing 'qty'"):
        AlpacaDataGateway(client=client).snapshot("SPY")


def test_daily_loss_is_measured_from_broker_day_start_equity():
    client = FakeClient(account=FakeAccount(last_equity=99_500.0, equity=99_000.0))

    snapshot = AlpacaDataGateway(client=client).snapshot("SPY")

    assert snapshot.account.daily_loss == 500.0
    assert snapshot.account.day_start_equity_known is True


def test_missing_day_start_equity_is_flagged_rather_than_assumed_zero():
    """The old fallback computed equity - portfolio_value, which is always 0.0,
    silently disabling the daily-loss kill switch."""
    client = FakeClient(account=FakeAccount(last_equity=None))

    snapshot = AlpacaDataGateway(client=client).snapshot("SPY")

    assert snapshot.account.day_start_equity_known is False
    assert snapshot.day_start_equity is None


def test_snapshot_source_is_the_broker():
    snapshot = AlpacaDataGateway(client=FakeClient()).snapshot("SPY")

    assert snapshot.source == "alpaca"


@pytest.mark.parametrize("count", [51, 499])
def test_synthetic_snapshot_reads_orders_beyond_sdk_default(count):
    client = FakeClient(orders=[FakeOrder() for _ in range(count)])
    assert len(AlpacaDataGateway(client=client).snapshot("SPY").open_orders) == count


def test_synthetic_snapshot_refuses_a_full_order_page():
    client = FakeClient(orders=[FakeOrder() for _ in range(500)])
    with pytest.raises(BrokerDataUnavailable, match="open orders"):
        AlpacaDataGateway(client=client).snapshot("SPY")


@pytest.mark.parametrize("asset_class", [None, "", "unknown", "us_option", "crypto"])
def test_synthetic_unsupported_asset_is_refused_before_stock_quote(asset_class):
    client = FakeClient()
    client.get_asset = lambda symbol: SimpleNamespace(**{"class": asset_class})
    quoted = []
    client.get_latest_trade = lambda symbol: quoted.append(symbol) or FakeTrade()
    with pytest.raises(BrokerDataUnavailable, match="asset"):
        AlpacaDataGateway(client=client).snapshot("SPY")
    assert quoted == []


def test_synthetic_missing_asset_metadata_is_refused_before_stock_quote():
    client = FakeClient(fail={"get_asset"})
    quoted = []
    client.get_latest_trade = lambda symbol: quoted.append(symbol) or FakeTrade()
    with pytest.raises(BrokerDataUnavailable, match="asset"):
        AlpacaDataGateway(client=client).snapshot("SPY")
    assert quoted == []


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
@pytest.mark.parametrize("field", ["cash", "buying_power", "equity", "portfolio_value", "last_equity",
                                  "position_qty", "position_market_value", "order_qty", "price"])
def test_synthetic_gateway_refuses_nonfinite_broker_risk_numbers(field, value):
    client = FakeClient(positions=[FakePosition()])
    if field.startswith("position_"):
        setattr(client._positions[0], field.removeprefix("position_"), value)
    elif field == "order_qty":
        client._orders = [FakeOrder(qty=value)]
    elif field == "price":
        client._price = value
    else:
        setattr(client._account, field, value)
    with pytest.raises(BrokerDataUnavailable):
        AlpacaDataGateway(client=client).snapshot("SPY")


def test_synthetic_equity_snapshot_keeps_unsupported_holdings_in_whole_account_exposure():
    positions = [FakePosition(symbol="BTCUSD", market_value=1000),
                 FakePosition(symbol="OPTION", market_value=2000)]
    positions[0].asset_class = "crypto"
    positions[1].asset_class = "us_option"
    snapshot = AlpacaDataGateway(client=FakeClient(positions=positions)).snapshot("SPY")
    assert {position.symbol for position in snapshot.positions} == {"BTCUSD", "OPTION"}
    assert sum(abs(position.market_value) for position in snapshot.positions) == 3000


def test_the_quote_time_is_the_quotes_and_the_price_time_is_the_trades():
    """Three facts, three fields. They used to be two fields and one fact.

    `quote_time` held the *trade*'s timestamp, so the staleness gate refused a symbol whose print
    lagged its quote while its bid and ask were current - 82 refusals across 2,495 journalled cycles,
    47 of them inside one hour on 2026-10-15 with the age climbing to 4,834s for SPY. This pins the
    split at the gateway, which is where the two feeds meet and where they were merged.
    """
    quoted_at = datetime(2026, 10, 15, 14, 30, tzinfo=timezone.utc)
    snapshot = AlpacaDataGateway(
        client=FakeClient(bid=499.99, ask=500.01, quote_time=quoted_at)
    ).snapshot("SPY")

    assert snapshot.quote_time == quoted_at
    assert snapshot.bid_price == 499.99
    assert snapshot.ask_price == 500.01
    # The trade this fake returns carries no timestamp at all, which is the other half of the point:
    # an absent price time is "unknowable", not "now", and must not be read as fresh.
    assert snapshot.price_time is None


def test_a_quote_outage_falls_back_to_the_trade_time_rather_than_disabling_the_check():
    """A check that disappears when a dependency fails is weaker than the one it replaces.

    The gateway writes `quote_time = quoted_at or price_time`. Without that fallback, a broker that
    stopped answering the quote endpoint would leave the field empty, and an empty field is
    "nothing to check" in the Guardian - so the gate would silently stop gating exactly when the
    data got worse. This pins the composition so the fallback cannot be dropped as redundant.
    """
    trade = SimpleNamespace(price=500.0, t=datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc))
    client = FakeClient(fail={"get_latest_quote"})
    client.get_latest_trade = lambda symbol: trade

    snapshot = AlpacaDataGateway(client=client).snapshot("SPY")

    assert snapshot.quote_time == trade.t
    assert snapshot.price_time == trade.t
    assert snapshot.bid_price is None and snapshot.ask_price is None
