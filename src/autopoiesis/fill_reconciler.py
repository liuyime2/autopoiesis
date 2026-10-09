from __future__ import annotations

from dataclasses import dataclass

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import JournalEventType

FILL_EVENT: JournalEventType = "ORDER_FILL_CONFIRMED"

# Broker order statuses that mean the order will never fill again.
TERMINAL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected", "replaced", "pending_cancel"})


@dataclass(frozen=True)
class PendingOrder:
    client_order_id: str
    order_id: str | None
    symbol: str
    side: str
    quantity: float
    strategy_id: str | None


@dataclass(frozen=True)
class FillConfirmation:
    pending: PendingOrder
    broker_status: str
    filled_quantity: float
    filled_avg_price: float | None

    @property
    def is_fill(self) -> bool:
        return self.filled_quantity > 0

    @property
    def is_terminal(self) -> bool:
        return self.broker_status.lower() in TERMINAL_STATUSES

    def payload(self) -> dict[str, object]:
        return {
            "client_order_id": self.pending.client_order_id,
            "order_id": self.pending.order_id,
            "symbol": self.pending.symbol,
            "side": self.pending.side,
            "strategy_id": self.pending.strategy_id,
            "requested_quantity": self.pending.quantity,
            "filled_quantity": self.filled_quantity,
            "filled_avg_price": self.filled_avg_price,
            "broker_status": self.broker_status,
            "source": "alpaca",
            "paper_only": True,
        }


class FillReconciler:
    """Closes the loop from a filled order back to the strategy that placed it.

    `executor.execute` reads filled_qty from the *submit* response, which is 0
    for a market order, and nothing ever re-polls. So orders filled but the
    journal recorded filled_quantity 0.0 and fill_quantity_ratio 0.0, and the
    evaluator reported pnl_evidence
    "missing_fill_price_and_broker_activity" for a strategy that had in fact
    traded. Without this the agent has no per-strategy PnL to learn from.

    Broker fills are the only source of truth here; nothing is inferred.
    """

    def __init__(self, *, client, journal: JsonlJournal):
        self.client = client
        self.journal = journal

    def pending(self, *, lookback: int = 500) -> list[PendingOrder]:
        """Submitted orders whose fill state the journal has not yet recorded."""
        already = {
            str(event.payload.get("client_order_id"))
            for event in self.journal.read_events(FILL_EVENT)
            if event.payload.get("client_order_id")
        }
        found: dict[str, PendingOrder] = {}
        for record in self.journal.last_n(lookback):
            execution = record.execution
            if execution.status != "SUBMITTED":
                continue
            coid = execution.client_order_id
            if not coid or coid in already or coid in found:
                continue
            if execution.filled_quantity and execution.filled_quantity > 0:
                continue
            found[coid] = PendingOrder(
                client_order_id=coid,
                order_id=execution.order_id,
                symbol=record.decision.symbol,
                side=record.decision.action,
                quantity=float(record.decision.quantity),
                strategy_id=record.strategy_id or record.decision.strategy_id,
            )
        return list(found.values())

    def resolve(self, *, lookback: int = 500) -> list[FillConfirmation]:
        confirmations = []
        for order in self.pending(lookback=lookback):
            confirmation = self._resolve_one(order)
            if confirmation is not None:
                confirmations.append(confirmation)
        return confirmations

    def _resolve_one(self, pending: PendingOrder) -> FillConfirmation | None:
        try:
            order = self.client.get_order_by_client_order_id(pending.client_order_id)
        except Exception:
            return None
        if order is None:
            return None
        filled = _float(getattr(order, "filled_qty", 0))
        status = str(getattr(order, "status", "") or "unknown")
        if filled <= 0 and status.lower() not in TERMINAL_STATUSES:
            return None
        return FillConfirmation(
            pending=pending,
            broker_status=status,
            filled_quantity=filled,
            filled_avg_price=_optional_float(getattr(order, "filled_avg_price", None)),
        )


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
