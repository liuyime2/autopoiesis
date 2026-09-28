from datetime import datetime, timezone

from min_agent.journal import JsonlJournal
from min_agent.models import AccountSnapshot, CycleRecord, DataSnapshot, ExecutionResult, GuardianResult, TradeDecision
from min_agent.order_reconciler import OrderReconciler


def make_record(*, cycle_id, order_id, status="SUBMITTED"):
    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 6, 9, 14, 30, tzinfo=timezone.utc),
        market_open=True,
        last_price=100,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
    )
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=snapshot,
        decision=TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status=status,
            order_id=order_id,
            client_order_id=f"min-agent-{cycle_id}",
            filled_quantity=0,
            submitted_at=datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc),
            message="submitted",
        ),
    )


class FakeAlpacaClient:
    def __init__(self, open_order_ids=None):
        self._open_orders = open_order_ids or []

    def list_orders(self, status: str = "open"):
        return [type("Order", (), {"id": oid})() for oid in self._open_orders]


class FailingAlpacaClient:
    def list_orders(self, status: str = "open"):
        raise RuntimeError("broker unavailable")


def test_reconciler_reports_matched_when_both_empty(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = FakeAlpacaClient(open_order_ids=[])
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is True
    assert report.submitted_order_ids == ()
    assert report.broker_open_order_ids == ()


def test_reconciler_reports_matched_when_all_orders_resolved(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(make_record(cycle_id="c1", order_id="oid-1"))
    journal.append(make_record(cycle_id="c2", order_id="oid-2"))
    client = FakeAlpacaClient(open_order_ids=[])
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is True
    assert len(report.submitted_order_ids) == 2
    assert report.missing_from_broker == ()
    assert report.extra_broker_orders == ()


def test_reconciler_reports_divergence_when_broker_has_open_order(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(make_record(cycle_id="c1", order_id="oid-1"))
    journal.append(make_record(cycle_id="c2", order_id="oid-2"))
    client = FakeAlpacaClient(open_order_ids=["oid-extra"])
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is False
    assert "broker" in report.message


def test_reconciler_reports_unmatched_when_broker_fetch_fails(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = FailingAlpacaClient()
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is False
    assert "fetch failed" in report.message