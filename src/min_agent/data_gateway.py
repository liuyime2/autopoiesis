from __future__ import annotations

from datetime import datetime, timezone

from min_agent.models import AccountSnapshot, DataSnapshot, OpenOrderSnapshot, PositionSnapshot


class BrokerDataUnavailable(RuntimeError):
    """Broker state could not be read.

    Raised instead of degrading to an empty result. A risk check that reads
    "no positions" while the API is down is unsound: it silently disables the
    SELL guard, the conflicting-open-order guard, and the aggregate exposure
    cap. Degrading is how this gateway hid a wrong client method name for
    thirteen days and 851 cycles.
    """


class AlpacaDataGateway:
    """Reads real broker state.

    Every value here comes from the broker or the call fails loudly. There are
    no synthetic or placeholder values on this path (AGENTS.md 2).
    """

    def __init__(self, *, client):
        self.client = client

    def snapshot(self, symbol: str) -> DataSnapshot:
        symbol = symbol.upper()
        clock = self.client.get_clock()
        account = self.client.get_account()
        last_price = self._latest_price(symbol)

        equity = float(account.equity)
        portfolio_value = float(account.portfolio_value)
        day_start_equity = self._safe_float(account, "last_equity")
        day_start_equity_known = day_start_equity is not None and day_start_equity > 0
        daily_loss = max(0.0, day_start_equity - equity) if day_start_equity_known else 0.0

        timestamp = getattr(clock, "timestamp", None) or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        return DataSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            market_open=bool(clock.is_open),
            last_price=last_price,
            source="alpaca",
            account=AccountSnapshot(
                equity=equity,
                cash=float(account.cash),
                buying_power=float(account.buying_power),
                portfolio_value=portfolio_value,
                daily_loss=daily_loss,
                day_start_equity_known=day_start_equity_known,
            ),
            day_start_equity=day_start_equity,
            positions=self._fetch_positions(),
            open_orders=self._fetch_open_orders(),
        )

    def _latest_price(self, symbol: str) -> float:
        trade = self.client.get_latest_trade(symbol)
        price = getattr(trade, "price", None) or getattr(trade, "p", None)
        if price is None:
            raise BrokerDataUnavailable(f"Alpaca returned no latest trade price for {symbol}")
        return float(price)

    def _safe_float(self, obj, attr: str) -> float | None:
        value = getattr(obj, attr, None)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _fetch_positions(self) -> tuple[PositionSnapshot, ...]:
        try:
            raw_positions = self.client.list_positions()
        except Exception as exc:
            raise BrokerDataUnavailable(f"could not read broker positions: {exc}") from exc
        return tuple(
            PositionSnapshot(
                symbol=str(_required(getattr(pos, "symbol", None), "position", "symbol")),
                quantity=_required_float(getattr(pos, "qty", None), "position", "qty"),
                market_value=_required_float(getattr(pos, "market_value", None), "position", "market_value"),
            )
            for pos in raw_positions
        )

    def _fetch_open_orders(self) -> tuple[OpenOrderSnapshot, ...]:
        try:
            raw_orders = self.client.list_orders(status="open")
        except Exception as exc:
            raise BrokerDataUnavailable(f"could not read broker open orders: {exc}") from exc
        return tuple(
            OpenOrderSnapshot(
                symbol=str(_required(getattr(order, "symbol", None), "open order", "symbol")),
                # Alpaca returns side lowercase; OpenOrderSnapshot is Literal["BUY","SELL"].
                # Without this normalisation every order is dropped as invalid.
                side=str(_required(getattr(order, "side", None), "open order", "side")).upper(),
                quantity=_required_float(getattr(order, "qty", None), "open order", "qty"),
                status=str(_required(getattr(order, "status", None), "open order", "status")),
            )
            for order in raw_orders
        )


def _required(value, context: str, attr: str) -> str:
    if value is None or value == "":
        raise BrokerDataUnavailable(f"broker {context} is missing {attr!r}")
    return value


def _required_float(value, context: str, attr: str) -> float:
    if value is None:
        raise BrokerDataUnavailable(f"broker {context} is missing {attr!r}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BrokerDataUnavailable(f"broker {context} has non-numeric {attr!r}: {value!r}") from exc
