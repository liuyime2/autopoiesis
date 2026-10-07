from datetime import datetime, timezone

import pytest

from min_agent.broker_evidence import BrokerEvidenceProvider
from min_agent.models import BrokerFillActivity


class Client:
    def list_orders(self, **kwargs):
        return [
            type(
                "Order",
                (),
                {
                    "id": "order-1",
                    "client_order_id": "client-1",
                    "symbol": "spy",
                    "side": "buy",
                    "qty": "2",
                    "filled_qty": "2",
                    "filled_avg_price": "100.5",
                    "status": "filled",
                    "submitted_at": "2026-06-10T13:30:00Z",
                    "filled_at": "2026-06-10T13:31:00Z",
                },
            )()
        ]

    def get_activities(self, **kwargs):
        return [
            {
                "id": "activity-1",
                "activity_type": "FILL",
                "order_id": "order-1",
                "client_order_id": "client-1",
                "symbol": "spy",
                "side": "buy",
                "qty": "2",
                "price": "100.5",
                "fee": "0.01",
                "transaction_time": "2026-06-10T13:31:00Z",
            }
        ]

    def get_portfolio_history(self, **kwargs):
        return type(
            "History",
            (),
            {
                "timestamp": [1_781_096_400],
                "equity": [100_500],
                "profit_loss": [500],
                "profit_loss_pct": [0.005],
            },
        )()


def test_broker_evidence_provider_normalizes_orders_activities_and_history():
    batch = BrokerEvidenceProvider(client=Client()).ingest(
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )

    assert batch.status == "SUCCESS"
    assert batch.orders[0].symbol == "SPY"
    assert batch.orders[0].filled_avg_price == 100.5
    assert batch.activities[0].fees == 0.01
    assert batch.portfolio_history[0].profit_loss == 500


def test_broker_evidence_provider_reports_partial_failures():
    class PartialClient(Client):
        def get_activities(self, **kwargs):
            raise RuntimeError("activity outage")

    batch = BrokerEvidenceProvider(client=PartialClient()).ingest(
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )

    assert batch.status == "PARTIAL"
    assert "activities fetch failed" in batch.missing_reasons[0]


def test_broker_fill_activity_rejects_fabricated_source():
    with pytest.raises(ValueError):
        BrokerFillActivity(
            activity_id="a1",
            symbol="SPY",
            side="BUY",
            quantity=1,
            price=100,
            transaction_time=datetime.now(tz=timezone.utc),
            source="fake",
        )


def test_portfolio_history_uses_the_real_signature_and_honours_the_window():
    """Regression: `get_portfolio_history(start=, end=)` does not exist on
    alpaca-trade-api 3.2.0. The signature is
    (date_start: str, date_end: str, period, timeframe), and a TypeError was
    swallowed into a no-argument call whose default is period="1M" - so a
    24-hour request was answered with 30 days while the batch still advertised
    24 hours.
    """
    import inspect
    from datetime import datetime, timedelta, timezone

    import alpaca_trade_api as tradeapi

    signature = inspect.signature(tradeapi.REST.get_portfolio_history)
    assert "date_start" in signature.parameters
    assert "date_end" in signature.parameters
    assert "timeframe" in signature.parameters

    calls = []

    class Client:
        def get_portfolio_history(self, **kwargs):
            calls.append(kwargs)
            return []

    provider = BrokerEvidenceProvider(client=Client())
    end = datetime(2026, 9, 28, tzinfo=timezone.utc)
    provider._fetch_portfolio_history(end - timedelta(hours=24), end)

    assert calls == [{"date_start": "2026-09-27", "date_end": "2026-09-28", "timeframe": "15Min"}]
    assert provider.window_fallback is None


def test_portfolio_history_drops_to_daily_resolution_beyond_thirty_days():
    """Alpaca rejects intraday timeframes for windows over 30 days:
    'invalid timeframe provided: 15Min. Valid timeframe for days > 30 is 1D'."""
    from datetime import datetime, timedelta, timezone

    calls = []

    class Client:
        def get_portfolio_history(self, **kwargs):
            calls.append(kwargs)
            return []

    provider = BrokerEvidenceProvider(client=Client())
    end = datetime(2026, 9, 28, tzinfo=timezone.utc)
    provider._fetch_portfolio_history(end - timedelta(days=120), end)

    # A 120-day window is already known to exceed the intraday limit, so daily is
    # requested first rather than paying a call the broker will reject.
    assert calls[0]["timeframe"] == "1D"
    assert calls[0]["date_start"] == "2026-05-31"


def test_a_window_that_spans_thirty_one_calendar_days_is_not_intraday():
    """The boundary is inclusive of dates, not a 24h duration: a window exactly
    30*24h long spans 31 dates and Alpaca already rejects intraday for it. That is
    why `--ingest-evidence`, which requests exactly 30 days, failed with
    'invalid timeframe provided: 15Min. Valid timeframe for days > 30 is 1D'."""
    from datetime import datetime, timedelta, timezone

    seen = []

    class Client:
        def get_portfolio_history(self, **kwargs):
            seen.append(kwargs)
            if kwargs.get("timeframe") == "15Min":
                raise RuntimeError(
                    "invalid timeframe provided: 15Min. Valid timeframe for days > 30 is 1D"
                )
            return []

    provider = BrokerEvidenceProvider(client=Client())
    end = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    provider._fetch_portfolio_history(end - timedelta(days=30), end)

    # 30*24h spans 31 dates, so the provider asks for daily first.
    assert [call["timeframe"] for call in seen] == ["1D"]
    assert "1D" in (provider.window_fallback or ""), (
        "the resolution we had to accept must be disclosed, not silent"
    )


def test_an_unhonourable_window_is_disclosed_not_substituted():
    from datetime import datetime, timedelta, timezone

    class OddClient:
        def get_portfolio_history(self, *args, **kwargs):
            if kwargs:
                raise TypeError("unexpected keyword argument")
            return []

    provider = BrokerEvidenceProvider(client=OddClient())
    end = datetime(2026, 9, 28, tzinfo=timezone.utc)
    provider._fetch_portfolio_history(end - timedelta(hours=24), end)

    assert provider.window_fallback is not None
    assert "unparameterised" in provider.window_fallback


def _fill(index: int) -> dict:
    return {
        "id": f"activity-{index}",
        "activity_type": "FILL",
        "order_id": f"order-{index}",
        "client_order_id": f"client-{index}",
        "symbol": "SPY",
        "side": "buy",
        "qty": "1",
        "price": "100",
        "fee": "0",
        "transaction_time": "2026-06-10T13:31:00Z",
    }


#: A window wide enough that the broker's page limit could bind, so the tests below are
#: about the limit and not about the shape of the fixture.
WIDE = dict(
    window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
    window_end=datetime(2026, 10, 4, tzinfo=timezone.utc),
)


def test_a_full_page_of_activities_is_reported_as_the_fragment_it_is():
    """The broker caps a page at 100 and says nothing about the rest.

    Measured on this account by paging with `page_token` until the API stopped: a
    nine-month window holds 279 fills, and a single request returned 100 of them with
    `missing_reasons` empty. So any figure computed from a window that wide was a
    fragment reported as a total - which is the same failure the journal's own rotation
    had, and the same response: say so rather than fetch a wider buffer in anticipation.

    Paging is deliberately not done. Every window this repository asks for is well under
    the limit - the daemon's 24 hours returns 0-20, `minictrl evidence ingest`'s 30 days
    returns 43, four months returns 66 - so a second page would be built for a window
    nobody requests.
    """
    limit = BrokerEvidenceProvider.ACTIVITY_PAGE_LIMIT

    class PagedClient(Client):
        def get_activities(self, **kwargs):
            return [_fill(i) for i in range(limit)]

    batch = BrokerEvidenceProvider(client=PagedClient()).ingest(**WIDE)

    assert len(batch.activities) == limit
    assert batch.status == "PARTIAL"
    assert any("full page" in reason for reason in batch.missing_reasons), (
        f"a fragment must not be presented as the whole window: {batch.missing_reasons}"
    )
    assert any("fragment" in reason for reason in batch.missing_reasons), (
        f"the reason must say what it is, not merely that something is missing: "
        f"{batch.missing_reasons}"
    )


def test_a_window_under_the_page_limit_is_not_questioned():
    """The check must not fire on the windows the code actually requests.

    A warning that fires on every ordinary cycle is a warning nobody reads, and this one
    would print inside the PnL evidence of every single pass.
    """

    class SmallClient(Client):
        def get_activities(self, **kwargs):
            return [_fill(i) for i in range(BrokerEvidenceProvider.ACTIVITY_PAGE_LIMIT - 1)]

    batch = BrokerEvidenceProvider(client=SmallClient()).ingest(**WIDE)

    assert batch.status == "SUCCESS"
    assert batch.missing_reasons == ()


def test_a_truncation_on_one_ingest_does_not_follow_the_next():
    """The flag is per-ingest, not per-provider.

    `BrokerEvidenceProvider` is a long-lived object on the daemon, so a flag that
    latched would report a truncation for every window after the first wide one - which
    is the same class of bug as the unparameterised `get_portfolio_history` fallback this
    file already pins.
    """
    limit = BrokerEvidenceProvider.ACTIVITY_PAGE_LIMIT

    class SometimesPaged(Client):
        def __init__(self):
            self.wide = True

        def get_activities(self, **kwargs):
            return [_fill(i) for i in range(limit if self.wide else 1)]

    client = SometimesPaged()
    provider = BrokerEvidenceProvider(client=client)

    first = provider.ingest(**WIDE)
    client.wide = False
    second = provider.ingest(**WIDE)

    assert first.status == "PARTIAL"
    assert second.status == "SUCCESS"
    assert second.missing_reasons == ()
