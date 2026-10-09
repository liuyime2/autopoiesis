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


class FakeClient:
    def __init__(self, *, positions=(), orders=(), account=None, is_open=True, price=500.0, fail=None):
        self._positions = list(positions)
        self._orders = list(orders)
        self._account = account or FakeAccount()
        self._is_open = is_open
        self._price = price
        self._fail = fail or set()

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

    def list_positions(self):
        self._guard("list_positions")
        return self._positions

    def list_orders(self, status=None):
        self._guard("list_orders")
        return self._orders


def test_gateway_uses_method_names_that_exist_on_the_real_alpaca_client():
    """The exact regression: wrong client method names, silently swallowed."""
    tradeapi = pytest.importorskip("alpaca_trade_api")

    for method in ("list_positions", "list_orders", "get_clock", "get_account", "get_latest_trade"):
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
