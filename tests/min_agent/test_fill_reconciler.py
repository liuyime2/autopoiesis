"""Regression tests for fill feedback.

The defect this file exists to prevent:

    executor.execute read filled_qty from the *submit* response, which is 0 for a
    market order, and nothing ever re-polled. So orders that filled were
    recorded as filled_quantity 0.0 and fill_quantity_ratio 0.0, and the
    evaluator reported pnl_evidence
    "missing_fill_price_and_broker_activity" for a strategy that had in fact
    traded. The recorded reflection.json shows exactly that:
    fixed-size-buy-001, submitted_orders 5, filled_quantity 0.0, ratio 0.0.

Without this feedback there is no per-strategy PnL to learn from, so the agent
cannot evolve no matter how the reward function is shaped.
"""

from datetime import datetime, timezone

import pytest

from min_agent.evaluator import PNL_EVIDENCE_MISSING, DeterministicEvaluator
from min_agent.fill_reconciler import FILL_EVENT, FillReconciler
from min_agent.journal import JsonlJournal
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    TradeDecision,
)

NOW = datetime.now(tz=timezone.utc)


class BrokerOrder:
    def __init__(self, filled_qty=0.0, status="new", filled_avg_price=None):
        self.filled_qty = filled_qty
        self.status = status
        self.filled_avg_price = filled_avg_price


class FakeClient:
    def __init__(self, orders=None, raise_for=()):
        self.orders = orders or {}
        self.raise_for = set(raise_for)
        self.queried: list[str] = []

    def get_order_by_client_order_id(self, coid):
        self.queried.append(coid)
        if coid in self.raise_for:
            raise ConnectionError("ConnectTimeout to paper-api.alpaca.markets")
        return self.orders.get(coid)


def _submitted_cycle(coid, strategy_id="buyer", quantity=2, symbol="SPY", action="BUY"):
    return CycleRecord(
        cycle_id=f"c-{coid}",
        snapshot=DataSnapshot(
            symbol=symbol,
            timestamp=NOW,
            market_open=True,
            last_price=500.0,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
            ),
        ),
        decision=TradeDecision(
            symbol=symbol, action=action, quantity=quantity, confidence=0.8, rationale="t", strategy_id=strategy_id
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status="SUBMITTED",
            order_id=f"o-{coid}",
            client_order_id=coid,
            filled_quantity=0.0,
            message="new",
        ),
        strategy_id=strategy_id,
    )


@pytest.fixture
def journal(tmp_path):
    return JsonlJournal(tmp_path / "journal.jsonl")


def test_a_filled_order_is_found_and_confirmed(journal):
    journal.append(_submitted_cycle("coid-1"))
    client = FakeClient({"coid-1": BrokerOrder(filled_qty=2.0, status="filled", filled_avg_price=501.25)})

    [confirmation] = FillReconciler(client=client, journal=journal).resolve()

    assert confirmation.is_fill is True
    assert confirmation.filled_quantity == 2.0
    assert confirmation.filled_avg_price == 501.25
    assert confirmation.pending.strategy_id == "buyer"
    assert confirmation.payload()["source"] == "alpaca"


def test_an_unfilled_open_order_is_not_confirmed(journal):
    journal.append(_submitted_cycle("coid-1"))
    client = FakeClient({"coid-1": BrokerOrder(filled_qty=0.0, status="new")})

    assert FillReconciler(client=client, journal=journal).resolve() == []


def test_a_cancelled_order_is_confirmed_with_zero_fill(journal):
    """Terminal-with-no-fill still has to leave the pending set, or the agent
    re-polls a dead order forever."""
    journal.append(_submitted_cycle("coid-1"))
    client = FakeClient({"coid-1": BrokerOrder(filled_qty=0.0, status="canceled")})

    [confirmation] = FillReconciler(client=client, journal=journal).resolve()

    assert confirmation.is_fill is False
    assert confirmation.is_terminal is True


def test_a_broker_outage_does_not_fabricate_a_fill(journal):
    journal.append(_submitted_cycle("coid-1"))
    client = FakeClient(raise_for={"coid-1"})

    assert FillReconciler(client=client, journal=journal).resolve() == []


def test_an_already_confirmed_fill_is_not_polled_twice(journal):
    journal.append(_submitted_cycle("coid-1"))
    journal.append_event(
        JournalEvent(
            event_id="e1",
            event_type=FILL_EVENT,
            timestamp=NOW,
            status="SUCCESS",
            message="filled",
            payload={"client_order_id": "coid-1", "filled_quantity": 2.0},
        )
    )
    client = FakeClient({"coid-1": BrokerOrder(filled_qty=2.0, status="filled")})

    assert FillReconciler(client=client, journal=journal).resolve() == []
    assert client.queried == []


def test_non_submitted_cycles_are_never_polled(journal):
    journal.append(_submitted_cycle("coid-1").model_copy(
        update={"execution": ExecutionResult(status="REJECTED", order_id=None, client_order_id="coid-x",
                                             filled_quantity=0, message="market is closed")}
    ))
    client = FakeClient()

    assert FillReconciler(client=client, journal=journal).resolve() == []
    assert client.queried == []


def test_a_fill_already_recorded_on_the_cycle_is_not_pending(journal):
    filled = _submitted_cycle("coid-1").model_copy(
        update={
            "execution": ExecutionResult(
                status="SUBMITTED", order_id="o-1", client_order_id="coid-1", filled_quantity=2.0, message="filled"
            )
        }
    )
    journal.append(filled)
    client = FakeClient()

    assert FillReconciler(client=client, journal=journal).resolve() == []


# --- the fill must actually reach the metrics ---------------------------------


def test_confirmed_fill_makes_fill_ratio_real(journal):
    journal.append(_submitted_cycle("coid-1", quantity=2))
    client = FakeClient({"coid-1": BrokerOrder(filled_qty=2.0, status="filled", filled_avg_price=500.0)})
    reconciler = FillReconciler(client=client, journal=journal)

    records = journal.read_all()
    before = DeterministicEvaluator().evaluate(records)
    assert before.strategy_metrics["buyer"].filled_quantity == 0.0
    assert before.strategy_metrics["buyer"].fill_quantity_ratio == 0.0

    fills = {c.pending.client_order_id: c.filled_quantity for c in reconciler.resolve()}
    after = DeterministicEvaluator().evaluate(records, fills=fills)

    assert after.strategy_metrics["buyer"].filled_quantity == 2.0
    assert after.strategy_metrics["buyer"].fill_quantity_ratio == 1.0
    assert after.filled_quantity == 2.0


def test_without_broker_evidence_pnl_is_still_reported_missing(journal):
    """Fills alone are not PnL. Closed-lot evidence is still required, and the
    evaluator must keep saying so rather than implying a verified result."""
    journal.append(_submitted_cycle("coid-1"))

    report = DeterministicEvaluator().evaluate(journal.read_all(), fills={"coid-1": 2.0})

    assert report.pnl_evidence == PNL_EVIDENCE_MISSING
    assert report.strategy_metrics["buyer"].pnl_evidence == PNL_EVIDENCE_MISSING


def test_a_fill_for_an_unknown_client_order_id_is_ignored(journal):
    journal.append(_submitted_cycle("coid-1"))

    report = DeterministicEvaluator().evaluate(journal.read_all(), fills={"coid-other": 99.0})

    assert report.strategy_metrics["buyer"].filled_quantity == 0.0


def test_journal_fill_never_lowers_an_already_recorded_fill(journal):
    journal.append(_submitted_cycle("coid-1").model_copy(
        update={
            "execution": ExecutionResult(
                status="SUBMITTED", order_id="o-1", client_order_id="coid-1", filled_quantity=5.0, message="filled"
            )
        }
    ))

    report = DeterministicEvaluator().evaluate(journal.read_all(), fills={"coid-1": 2.0})

    assert report.strategy_metrics["buyer"].filled_quantity == 5.0


# --- client surface ----------------------------------------------------------


def test_client_method_exists_on_the_real_alpaca_client():
    tradeapi = pytest.importorskip("alpaca_trade_api")
    assert hasattr(tradeapi.REST, "get_order_by_client_order_id")
