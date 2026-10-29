"""Collect information about a symbol from the broker and compute risk markers.

This is the code half of the "new information sources" item in the 2026-10-20 review.
It is read-only: it places no order, changes no position, and writes nothing to the
journal. Its output is evidence for research trials and context for the decision
engine, and it is stated as such in both places.

Why a separate module rather than more methods on `DataGateway`. The gateway is the
broker-facing reader: it fetches bars, profiles, and headlines, and it caches per
symbol for the life of one cycle. The collector is the *interpreter*: it takes those
raw reads and turns them into risk markers with stated derivations, and it is the
thing research trials import when they want to know what was known about a symbol at
a given time. Keeping the two apart means a new information source (earnings
calendar, insider trades, options flow) is added here without touching the broker
reader, and the gateway never learns about research.

Design rules, inherited from the project's standing constraints:
  - real data only. An empty result is a result, not a fabrication.
  - never raises. A failed read is recorded on `last_error` and the marker is None.
  - deterministic. The same broker state produces the same snapshot.
  - no side effects. No orders, no journal writes, no state changes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from autopoiesis.models import EarningsEvent, InformationSnapshot, NewsItem

#: How far ahead the earnings calendar is fetched. One quarter of trading days
#: covers the next earnings season for most US equities without pulling a year of
#: announcements the collector cannot use.
EARNINGS_WINDOW_DAYS = 90

#: Headlines newer than this count toward the 24-hour news marker.
NEWS_RECENCY_HOURS = 24

#: An earnings report within this many calendar days counts as "imminent" and sets
#: the risk marker. Five days covers the window in which a position is exposed to
#: an earnings gap without time to adjust.
EARNINGS_IMMINENT_DAYS = 5


class InformationCollector:
    """Read-only collector of broker information about a symbol.

    Construct with a data gateway (which already knows how to talk to the broker)
    or with a client directly. The gateway is preferred: it caches per symbol and
    records why a read failed.
    """

    def __init__(self, *, gateway=None, client=None):
        if gateway is None and client is None:
            raise ValueError("InformationCollector needs either a gateway or a client")
        self.gateway = gateway
        self.client = client
        self.last_error: str | None = None

    def earnings_calendar(self, symbol: str, *, days_ahead: int = EARNINGS_WINDOW_DAYS) -> tuple[EarningsEvent, ...]:
        """Upcoming earnings announcements for ``symbol`` within ``days_ahead`` days.

        Reads from the broker's corporate-actions feed. Returns an empty tuple when
        the broker has no announcements in the window or when the read fails; the
        reason is on ``last_error``. An empty result is not an error for instruments
        that do not report earnings.
        """
        symbol = symbol.upper()
        self.last_error = None
        client = self._client()
        if client is None:
            self.last_error = "no broker client available"
            return ()
        try:
            raw = client.data_get(
                "/v1/corporate-actions/announcements",
                {"symbols": symbol, "types": "earnings", "limit": 50},
                api_version="v1",
            )
        except Exception as exc:  # pragma: no cover - network failure path
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ()
        items = (raw or {}).get("corporate_actions") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            # Some brokers return the list under a different key; try the common ones.
            for key in ("announcements", "earnings", "data"):
                candidate = (raw or {}).get(key) if isinstance(raw, dict) else None
                if isinstance(candidate, list):
                    items = candidate
                    break
        if not isinstance(items, list):
            self.last_error = "broker returned no announcement list"
            return ()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days_ahead)
        out: list[EarningsEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("corporate_action_type") or item.get("type") or "").lower() not in {
                "", "earnings", "earnings_report",
            }:
                continue
            report_date = _parse_date(item.get("ex_date") or item.get("report_date") or item.get("date"))
            if report_date is None:
                continue
            if report_date < now.date() or report_date > cutoff.date():
                continue
            out.append(
                EarningsEvent(
                    symbol=symbol,
                    report_date=report_date,
                    fiscal_period=str(item.get("fiscal_period") or item.get("period") or ""),
                    fiscal_year=_as_int(item.get("fiscal_year") or item.get("year")),
                    eps_estimate=_as_float(item.get("eps_estimate") or item.get("estimate_eps")),
                    eps_actual=_as_float(item.get("eps_actual") or item.get("actual_eps")),
                    revenue_estimate=_as_float(item.get("revenue_estimate")),
                    revenue_actual=_as_float(item.get("revenue_actual")),
                    time_of_day=str(item.get("time_of_day") or item.get("timing") or "").lower(),
                )
            )
        out.sort(key=lambda e: e.report_date or date.max)
        return tuple(out)

    def news_risk(self, symbol: str, *, limit: int = 10) -> tuple[NewsItem, ...]:
        """Recent headlines for ``symbol``, newest first. Read-only.

        Delegates to the gateway when one is available (so caching and error reasons are
        shared); otherwise calls the broker directly. Returns an empty tuple on failure.
        """
        symbol = symbol.upper()
        if self.gateway is not None and hasattr(self.gateway, "recent_news"):
            try:
                return self.gateway.recent_news(symbol, limit=limit)
            except Exception as exc:  # pragma: no cover - gateway failure path
                self.last_error = f"{type(exc).__name__}: {exc}"
                return ()
        client = self._client()
        if client is None:
            self.last_error = "no broker client available"
            return ()
        try:
            raw = client.data_get("/news", {"symbols": symbol, "limit": int(limit)}, api_version="v1beta1")
        except Exception as exc:  # pragma: no cover - network failure path
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ()
        items_raw = (raw or {}).get("news") if isinstance(raw, dict) else None
        if not isinstance(items_raw, list):
            self.last_error = "broker returned no news list"
            return ()
        out: list[NewsItem] = []
        for item in items_raw[:limit]:
            if not isinstance(item, dict):
                continue
            headline = str(item.get("headline") or "").strip()
            if not headline:
                continue
            symbols = item.get("symbols") or [symbol]
            created = None
            if item.get("created_at"):
                try:
                    created = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
                except ValueError:
                    created = None
            out.append(
                NewsItem(
                    symbol=str(symbols[0]).upper(),
                    headline=headline[:200],
                    source=str(item.get("source") or item.get("author") or "alpaca_news")[:60],
                    created_at=created,
                    summary=str(item.get("summary") or "").strip()[:200],
                )
            )
        return tuple(out)

    def collect(self, symbol: str, *, now: datetime | None = None) -> InformationSnapshot:
        """Collect everything known about ``symbol`` at ``now`` and compute risk markers.

        Read-only. Never raises. A missing value is reported as None, never as zero.
        """
        symbol = symbol.upper()
        self.last_error = None
        when = now or datetime.now(timezone.utc)
        earnings = self.earnings_calendar(symbol)
        news = self.news_risk(symbol)
        days_to_earnings: int | None = None
        earnings_imminent = False
        if earnings:
            first = earnings[0].report_date
            if first is not None:
                days_to_earnings = (first - when.date()).days
                earnings_imminent = 0 <= days_to_earnings <= EARNINGS_IMMINENT_DAYS
        news_count_24h = sum(
            1 for item in news
            if item.created_at is not None
            and (when - item.created_at).total_seconds() <= NEWS_RECENCY_HOURS * 3600
        )
        return InformationSnapshot(
            symbol=symbol,
            collected_at=when,
            earnings=earnings,
            news=news,
            days_to_earnings=days_to_earnings,
            news_count_24h=news_count_24h,
            earnings_imminent=earnings_imminent,
            error=self.last_error,
        )

    def _client(self):
        if self.client is not None:
            return self.client
        if self.gateway is not None and hasattr(self.gateway, "client"):
            return self.gateway.client
        return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None
