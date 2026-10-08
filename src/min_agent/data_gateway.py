from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from min_agent.models import (
    AccountSnapshot,
    DataSnapshot,
    OpenOrderSnapshot,
    PositionSnapshot,
    Side,
)


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
        #: Why the last `daily_closes` call returned nothing (None while it has not failed).
        self.last_daily_error: str | None = None
        self.last_universe_error: str | None = None

    def snapshot(self, symbol: str) -> DataSnapshot:
        symbol = symbol.upper()
        clock = self.client.get_clock()
        account = self.client.get_account()
        last_price = self._latest_price(symbol)

        equity = float(account.equity)
        portfolio_value = float(account.portfolio_value)
        day_start_equity = self._safe_float(account, "last_equity")
        day_start_equity_known = day_start_equity is not None and day_start_equity > 0
        # The `..._known` flag cannot narrow `day_start_equity` for the arithmetic, so
        # the check is repeated here rather than inferred. One source of truth for the
        # flag, one visible guard for the subtraction.
        daily_loss = (
            max(0.0, day_start_equity - equity) if day_start_equity is not None and day_start_equity > 0 else 0.0
        )

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

    #: Exchanges whose listings count as a normal US stock. OTC is a different market.
    MAJOR_EXCHANGES = frozenset({"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"})

    def tradable_symbols(self) -> frozenset[str] | None:
        """Active, tradable US equities on a major exchange, cached for the day. `None` on failure
        (the Guardian then refuses rather than guess). Read-only."""
        from datetime import date

        today = date.today()
        cached = getattr(self, "_tradable_cache", None)
        if cached and cached[0] == today:
            return cached[1]
        try:
            assets = self.client.list_assets(status="active", asset_class="us_equity")
            symbols = frozenset(
                str(a.symbol).upper() for a in assets
                if getattr(a, "tradable", False) and str(getattr(a, "exchange", "")).upper() in self.MAJOR_EXCHANGES
            )
        except Exception as exc:
            self.last_universe_error = f"{type(exc).__name__}: {exc}"
            return cached[1] if cached else None  # yesterday's list beats no list
        self._tradable_cache = (today, symbols)
        return symbols

    def held_symbols(self) -> list[str]:
        """Every symbol the account holds a non-zero position in, sorted. Read-only."""
        return sorted({p.symbol for p in self._fetch_positions() if p.quantity != 0})

    def attention_symbols(self, k: int, *, min_price: float = 5.0, exclude: frozenset[str] = frozenset()) -> list[str]:
        """Up to `k` symbols worth a look this round: the broker's most active by dollar volume,
        after quality filters (a major exchange, priced at least `min_price`, not a warrant, unit
        or right). The raw list is mostly penny stocks and SPAC paper. Read-only; `[]` on failure."""
        if k <= 0:
            return []
        try:
            raw = self.client.data_get("/screener/stocks/most-actives", {"by": "volume", "top": 60}, api_version="v1beta1")
            names = [str(x["symbol"]).upper() for x in raw.get("most_actives", [])]
            allowed = self.tradable_symbols()
            movers = self.client.data_get("/screener/stocks/movers", {"top": 40}, api_version="v1beta1")
            names += [str(x["symbol"]).upper() for x in movers.get("gainers", []) + movers.get("losers", [])
                      if float(x.get("price", 0) or 0) >= min_price]
        except Exception as exc:
            self.last_universe_error = f"{type(exc).__name__}: {exc}"
            return []
        candidates: list[str] = []
        for s in names:
            if s in exclude or s in candidates or not (1 <= len(s) <= 5) or not s.isalpha():
                continue
            if len(s) == 5 and s[-1] in "WUR":  # warrants, units, rights
                continue
            if allowed is not None and s not in allowed:
                continue
            candidates.append(s)
        if not candidates:
            return []
        try:  # the most-active list carries no price; one batched call removes the penny stocks
            trades = self.client.get_latest_trades(candidates[:80])
            price = {s: float(getattr(trades.get(s), "price", 0) or getattr(trades.get(s), "p", 0) or 0) for s in candidates[:80]}
        except Exception as exc:
            self.last_universe_error = f"{type(exc).__name__}: {exc}"
            return []
        return [s for s in candidates if price.get(s, 0.0) >= min_price][:k]

    def daily_closes(self, symbol: str, days: int = 2800) -> list[float] | None:
        """Split- and dividend-adjusted daily closes, oldest first, ending yesterday. Read-only.

        Adjusted so a split is not a crash: a volatility forecast fed a raw 2:1 split would see a
        50% one-day move. `None` when the broker returns nothing or the call fails.
        """
        from datetime import date, timedelta

        try:
            end = (date.today() - timedelta(days=1)).isoformat()
            start = (date.today() - timedelta(days=int(days * 1.5))).isoformat()
            frame = self.client.get_bars(symbol.upper(), "1Day", start, end, adjustment="all").df
            closes = [float(v) for v in frame["close"].tolist()]
            return closes[-days:] if closes else None
        except Exception as exc:
            self.last_daily_error = f"{type(exc).__name__}: {exc}"
            return None

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
                side=cast("Side", str(_required(getattr(order, "side", None), "open order", "side")).upper()),
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
