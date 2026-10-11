from datetime import datetime, timezone

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    TradeDecision,
)
from autopoiesis.order_reconciler import OrderReconciler


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
            client_order_id=f"autopoiesis-{cycle_id}",
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
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="test-orders-1", reason="test baseline"
    )
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
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="test-orders-2", reason="test baseline"
    )
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


def test_reconciler_resolves_intent_by_exact_client_order_id_and_projects_known_id(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    client_order_id = "autopoiesis-ambiguous"
    journal.append_event(JournalEvent(
        event_id="intent-1",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        cycle_id="cycle-unknown",
        strategy_id="s1",
        payload={"client_order_id": client_order_id, "symbol": "SPY", "action": "BUY", "quantity": 1},
    ))

    class Broker(FakeAlpacaClient):
        def get_order_by_client_order_id(self, requested):
            assert requested == client_order_id
            return type("Order", (), {
                "id": "broker-accepted",
                "client_order_id": client_order_id,
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
            })()

    reconciler = OrderReconciler(
        client=Broker(open_order_ids=["broker-accepted"]), journal=journal
    )
    report = reconciler.reconcile()

    assert report.matched is True
    assert report.submitted_order_ids == ("broker-accepted",)
    assert report.unresolved_client_order_ids == ()
    resolved = journal.read_events("ORDER_SUBMISSION_RESOLVED")
    assert len(resolved) == 1
    assert resolved[0].payload["client_order_id"] == client_order_id


def test_reconciler_keeps_not_found_intent_unresolved_and_fails_closed(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    journal.append_event(JournalEvent(
        event_id="intent-not-found",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        payload={"client_order_id": "cid-missing", "symbol": "SPY", "action": "BUY", "quantity": 1},
    ))

    class Broker(FakeAlpacaClient):
        def get_order_by_client_order_id(self, _requested):
            return None

    report = OrderReconciler(client=Broker(), journal=journal).reconcile()

    assert report.matched is False
    assert report.unresolved_client_order_ids == ("cid-missing",)
    assert journal.read_events("ORDER_SUBMISSION_RESOLVED") == []


def test_acknowledged_order_without_cycle_is_startup_known(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    journal.append_event(JournalEvent(
        event_id="ack-without-cycle",
        event_type="ORDER_SUBMISSION_ACKNOWLEDGED",
        timestamp=datetime.now(tz=timezone.utc),
        status="SUCCESS",
        payload={"client_order_id": "cid-ack", "order_id": "oid-ack", "symbol": "SPY"},
    ))

    report = OrderReconciler(
        client=FakeAlpacaClient(open_order_ids=["oid-ack"]), journal=journal
    ).reconcile()

    assert report.matched is True
    assert report.submitted_order_ids == ("oid-ack",)


def test_replaced_ambiguous_order_remains_blocked_until_chain_is_supported(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-replaced"
    journal.append_event(JournalEvent(
        event_id="intent-replaced",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        payload={"client_order_id": coid, "symbol": "SPY", "action": "BUY", "quantity": 1},
    ))

    class Broker(FakeAlpacaClient):
        def get_order_by_client_order_id(self, _requested):
            return type("Order", (), {
                "id": "old-order", "client_order_id": coid, "status": "replaced",
                "filled_qty": "0", "replaced_by": "new-order",
            })()

    report = OrderReconciler(client=Broker(), journal=journal).reconcile()

    assert report.matched is False
    assert report.unresolved_client_order_ids == (coid,)
    assert journal.read_events("ORDER_SUBMISSION_RESOLVED") == []
