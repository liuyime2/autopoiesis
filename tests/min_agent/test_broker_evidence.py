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

    assert calls[0]["timeframe"] == "1D"
    assert calls[0]["date_start"] == "2026-05-31"


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
