"""The information collector is read-only: it places no order and changes no state.

It exists so the 2026-10-20 review's "new information sources" item has its code half
ready before the research trials that will use it. Tests here pin the contract: real
broker data or an honest empty result, never a fabrication; never raises; deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from autopoiesis.information_collector import InformationCollector
from autopoiesis.models import EarningsEvent, InformationSnapshot, NewsItem


def _client(earnings=None, news=None, fail_earnings=False, fail_news=False):
    class C:
        def data_get(self, path, params, api_version=None):
            if "corporate-actions" in path or "announcements" in path:
                if fail_earnings:
                    raise RuntimeError("broker down")
                return {"corporate_actions": earnings or []}
            if "/news" in path:
                if fail_news:
                    raise RuntimeError("broker down")
                return {"news": news or []}
            return {}
    return C()


def test_collect_returns_a_snapshot_with_earnings_and_news():
    earnings = [{"corporate_action_type": "earnings", "ex_date": "2026-11-05",
                 "fiscal_period": "Q3", "fiscal_year": 2026, "eps_estimate": 1.23,
                 "time_of_day": "post_market"}]
    news = [{"headline": "AAPL beats", "source": "reuters", "created_at": "2026-10-29T10:00:00Z",
             "symbols": ["AAPL"], "summary": "strong quarter"}]
    collector = InformationCollector(client=_client(earnings=earnings, news=news))
    # A fixed clock: the fixture's dates are calendar dates, and against the wall clock this test
    # passed on 2026-10-25 and failed from 2026-10-31 as "11 days away" became 5.
    snap = collector.collect("AAPL", now=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc))
    assert isinstance(snap, InformationSnapshot)
    assert snap.symbol == "AAPL"
    assert len(snap.earnings) == 1
    assert snap.earnings[0].report_date.isoformat() == "2026-11-05"
    assert snap.earnings[0].eps_estimate == 1.23
    assert snap.earnings_imminent is False  # 7 days away at the fixed clock
    assert snap.days_to_earnings is not None and snap.days_to_earnings > 5
    assert len(snap.news) == 1
    assert snap.news[0].headline == "AAPL beats"
    assert snap.news_count_24h == 1
    assert snap.error is None


def test_collect_is_honest_when_the_broker_has_nothing():
    collector = InformationCollector(client=_client(earnings=[], news=[]))
    snap = collector.collect("ZZZ")
    assert snap.earnings == ()
    assert snap.news == ()
    assert snap.days_to_earnings is None
    assert snap.earnings_imminent is False
    assert snap.news_count_24h == 0
    assert snap.error is None  # empty is a result, not an error


def test_collect_never_raises_on_broker_failure():
    collector = InformationCollector(client=_client(fail_earnings=True, fail_news=True))
    snap = collector.collect("SPY")
    assert isinstance(snap, InformationSnapshot)
    assert snap.symbol == "SPY"
    # Either earnings or news failed; the snapshot still exists and explains why.
    assert snap.error is not None


def test_earnings_imminent_when_within_five_days():
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=3)).isoformat()
    earnings = [{"corporate_action_type": "earnings", "ex_date": soon}]
    collector = InformationCollector(client=_client(earnings=earnings))
    snap = collector.collect("SPY")
    assert snap.earnings_imminent is True
    assert snap.days_to_earnings is not None and snap.days_to_earnings <= 5


def test_collect_is_deterministic():
    earnings = [{"corporate_action_type": "earnings", "ex_date": "2026-11-10"}]
    news = [{"headline": "h", "source": "s", "created_at": "2026-10-24T10:00:00Z", "symbols": ["X"]}]
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    a = InformationCollector(client=_client(earnings=earnings, news=news)).collect("X", now=now)
    b = InformationCollector(client=_client(earnings=earnings, news=news)).collect("X", now=now)
    assert a == b


def test_uses_gateway_recent_news_when_available():
    gw = SimpleNamespace(recent_news=lambda sym, limit=5: (NewsItem(symbol=sym, headline="gw headline", source="gw"),))
    collector = InformationCollector(gateway=gw, client=_client())
    snap = collector.collect("AAPL")
    assert len(snap.news) == 1
    assert snap.news[0].headline == "gw headline"


def test_news_count_24h_excludes_old_headlines():
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    news = [
        {"headline": "old", "source": "s", "created_at": old, "symbols": ["X"]},
        {"headline": "recent", "source": "s", "created_at": recent, "symbols": ["X"]},
    ]
    collector = InformationCollector(client=_client(news=news))
    snap = collector.collect("X")
    assert snap.news_count_24h == 1
