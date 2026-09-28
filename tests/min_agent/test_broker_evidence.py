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
