from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from min_agent.models import BrokerEvidenceBatch, BrokerFillActivity, BrokerOrderSnapshot, PortfolioHistoryPoint


class BrokerEvidenceProvider:
    def __init__(self, *, client):
        self.client = client
        self.window_fallback: str | None = None
        """Set when a requested window could not be honoured, so the batch can
        say so instead of advertising a window it did not fetch."""

    def ingest(self, *, window_start: datetime, window_end: datetime) -> BrokerEvidenceBatch:
        missing_reasons: list[str] = []
        orders = self._safe_fetch("orders", missing_reasons, lambda: self._fetch_orders(window_start, window_end))
        activities = self._safe_fetch(
            "activities", missing_reasons, lambda: self._fetch_activities(window_start, window_end)
        )
        portfolio_history = self._safe_fetch(
            "portfolio_history", missing_reasons, lambda: self._fetch_portfolio_history(window_start, window_end)
        )
        failed = len(missing_reasons)
        status = "SUCCESS"
        if failed == 3:
            status = "FAILED"
        elif failed:
            status = "PARTIAL"
        return BrokerEvidenceBatch(
            generated_at=datetime.now(tz=timezone.utc),
            window_start=window_start,
            window_end=window_end,
            orders=tuple(orders),
            activities=tuple(activities),
            portfolio_history=tuple(portfolio_history),
            status=status,
            missing_reasons=tuple(missing_reasons),
        )

    def _safe_fetch(self, name: str, missing_reasons: list[str], fetch):
        try:
            return fetch()
        except Exception as exc:  # pragma: no cover - external SDK errors vary
            missing_reasons.append(f"{name} fetch failed: {exc}")
            return []

    def _fetch_orders(self, window_start: datetime, window_end: datetime) -> list[BrokerOrderSnapshot]:
        after = _api_time(window_start)
        until = _api_time(window_end)
        if hasattr(self.client, "list_orders"):
            raw_orders = self.client.list_orders(status="all", after=after, until=until)
        elif hasattr(self.client, "get_orders"):
            raw_orders = self.client.get_orders(status="all", after=after, until=until)
        else:
            raise AttributeError("client has no list_orders/get_orders")
        return [order for order in (self._normalize_order(item) for item in raw_orders or []) if order is not None]

    def _fetch_activities(self, window_start: datetime, window_end: datetime) -> list[BrokerFillActivity]:
        if not hasattr(self.client, "get_activities"):
            raise AttributeError("client has no get_activities")
        after = _api_time(window_start)
        until = _api_time(window_end)
        try:
            raw_activities = self.client.get_activities(activity_types="FILL", after=after, until=until)
        except TypeError:
            try:
                raw_activities = self.client.get_activities(activity_type="FILL", after=after, until=until)
            except TypeError:
                raw_activities = self.client.get_activities(after=after, until=until)
        activities = []
        for item in raw_activities or []:
            activity_type = str(_get(item, "activity_type", _get(item, "type", "FILL"))).upper()
            if activity_type and activity_type != "FILL":
                continue
            normalized = self._normalize_activity(item)
            if normalized is not None:
                activities.append(normalized)
        return activities

    def _fetch_portfolio_history(self, window_start: datetime, window_end: datetime) -> list[PortfolioHistoryPoint]:
        """Fetch portfolio history for exactly the requested window.

        `get_portfolio_history(date_start, date_end, period, timeframe)` takes
        *strings* named `date_start`/`date_end`. This used to call
        `get_portfolio_history(start=..., end=...)` with datetimes, which raises
        TypeError, and then silently fell back to a no-argument call whose
        default is `period="1M"`. So a 24-hour request was answered with a
        30-day window while the batch still advertised the 24-hour window, and
        every downstream account_return_pct came from the wrong period.

        A window we cannot honour is reported, not substituted.
        """
        if not hasattr(self.client, "get_portfolio_history"):
            raise AttributeError("client has no get_portfolio_history")
        window_days = max(1, (window_end - window_start).days)
        # Alpaca rejects intraday timeframes for windows over 30 days:
        # "invalid timeframe provided: 15Min. Valid timeframe for days > 30 is 1D".
        timeframe = "15Min" if window_days <= 30 else "1D"
        try:
            history = self.client.get_portfolio_history(
                date_start=window_start.strftime("%Y-%m-%d"),
                date_end=window_end.strftime("%Y-%m-%d"),
                timeframe=timeframe,
            )
        except TypeError:
            # A client whose signature genuinely differs. Retry unparameterised
            # but say so, rather than pretending the window was honoured.
            history = self.client.get_portfolio_history()
            self.window_fallback = f"unparameterised (requested {window_days}d @ {timeframe})"
        else:
            self.window_fallback = None
        return self._normalize_portfolio_history(history)

    def _normalize_order(self, order) -> BrokerOrderSnapshot | None:
        order_id = _text(_get(order, "id", _get(order, "order_id", None)))
        symbol = _text(_get(order, "symbol", None))
        side = _text(_get(order, "side", None)).upper()
        quantity = _float(_get(order, "qty", _get(order, "quantity", None)))
        status = _text(_get(order, "status", "unknown"))
        if not order_id or not symbol or side not in {"BUY", "SELL"} or quantity is None or quantity <= 0:
            return None
        return BrokerOrderSnapshot(
            order_id=order_id,
            client_order_id=_text(_get(order, "client_order_id", None)) or None,
            symbol=symbol,
            side=side,
            quantity=quantity,
            filled_quantity=_float(_get(order, "filled_qty", _get(order, "filled_quantity", 0))) or 0,
            filled_avg_price=_float(_get(order, "filled_avg_price", None)),
            status=status or "unknown",
            submitted_at=_datetime(_get(order, "submitted_at", None)),
            filled_at=_datetime(_get(order, "filled_at", None)),
        )

    def _normalize_activity(self, activity) -> BrokerFillActivity | None:
        activity_id = _text(_get(activity, "id", _get(activity, "activity_id", None)))
        symbol = _text(_get(activity, "symbol", None))
        side = _text(_get(activity, "side", None)).upper()
        quantity = _float(_get(activity, "qty", _get(activity, "quantity", None)))
        price = _float(_get(activity, "price", None))
        transaction_time = _datetime(_get(activity, "transaction_time", _get(activity, "date", None)))
        if not activity_id or not symbol or side not in {"BUY", "SELL"} or not quantity or not price or transaction_time is None:
            return None
        return BrokerFillActivity(
            activity_id=activity_id,
            order_id=_text(_get(activity, "order_id", None)) or None,
            client_order_id=_text(_get(activity, "client_order_id", None)) or None,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            gross_amount=_float(_get(activity, "net_amount", _get(activity, "gross_amount", None))),
            fees=_float(_get(activity, "fee", _get(activity, "fees", 0))) or 0,
            transaction_time=transaction_time,
        )

    def _normalize_portfolio_history(self, history) -> list[PortfolioHistoryPoint]:
        timestamps = list(_values(history, "timestamp"))
        equities = list(_values(history, "equity"))
        profit_loss = list(_values(history, "profit_loss"))
        profit_loss_pct = list(_values(history, "profit_loss_pct"))
        points = []
        for index, raw_timestamp in enumerate(timestamps):
            timestamp = _datetime(raw_timestamp)
            equity = _float(equities[index] if index < len(equities) else None)
            if timestamp is None or equity is None:
                continue
            points.append(
                PortfolioHistoryPoint(
                    timestamp=timestamp,
                    equity=equity,
                    profit_loss=_float(profit_loss[index] if index < len(profit_loss) else None),
                    profit_loss_pct=_float(profit_loss_pct[index] if index < len(profit_loss_pct) else None),
                )
            )
        return points


def _get(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _values(obj, name: str) -> Iterable:
    value = _get(obj, name, [])
    return value or []


def _text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _api_time(value: datetime) -> str:
    timestamp = value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    return timestamp.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
