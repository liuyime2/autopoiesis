from __future__ import annotations

from datetime import datetime, timezone

from min_agent.models import AccountSnapshot, DataSnapshot, OpenOrderSnapshot, PositionSnapshot


class AlpacaDataGateway:
    def __init__(self, *, client):
        self.client = client

    def snapshot(self, symbol: str) -> DataSnapshot:
        symbol = symbol.upper()
        clock = self.client.get_clock()
        account = self.client.get_account()
        last_price = self._latest_price(symbol)
        equity = float(account.equity)
        portfolio_value = float(account.portfolio_value)
        # Day-start equity: prefer last_equity from Alpaca; fall back to portfolio_value
        day_start_equity = self._safe_float(account, "last_equity")
        if day_start_equity is not None and day_start_equity > 0:
            daily_loss = max(0.0, day_start_equity - equity)
        else:
            daily_loss = max(0.0, equity - portfolio_value)
        timestamp = getattr(clock, "timestamp", None) or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        positions = self._fetch_positions()
        open_orders = self._fetch_open_orders()

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
            ),
            day_start_equity=day_start_equity,
            positions=positions,
            open_orders=open_orders,
        )

    def _latest_price(self, symbol: str) -> float:
        trade = self.client.get_latest_trade(symbol)
        price = getattr(trade, "price", None) or getattr(trade, "p", None)
        if price is None:
            raise RuntimeError(f"Alpaca returned no latest trade price for {symbol}")
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
            raw_positions = self.client.get_all_positions()
        except Exception:
            return ()
        results = []
        for pos in raw_positions:
            try:
                results.append(PositionSnapshot(
                    symbol=str(getattr(pos, "symbol", "")),
                    quantity=float(getattr(pos, "qty", 0)),
                    market_value=float(getattr(pos, "market_value", 0)),
                ))
            except Exception:
                continue
        return tuple(results)

    def _fetch_open_orders(self) -> tuple[OpenOrderSnapshot, ...]:
        try:
            raw_orders = self.client.get_orders(status="open")
        except Exception:
            return ()
        results = []
        for order in raw_orders:
            try:
                results.append(OpenOrderSnapshot(
                    symbol=str(getattr(order, "symbol", "")),
                    side=str(getattr(order, "side", "")),
                    quantity=float(getattr(order, "qty", 0)),
                    status=str(getattr(order, "status", "unknown")),
                ))
            except Exception:
                continue
        return tuple(results)
