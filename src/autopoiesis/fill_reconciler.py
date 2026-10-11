from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import JournalEvent, JournalEventType
from autopoiesis.submission_state import project_submission_state

FILL_EVENT: JournalEventType = "ORDER_FILL_CONFIRMED"
FILL_SNAPSHOT_EVENT: JournalEventType = "ORDER_FILL_SNAPSHOT"

# Broker order statuses that mean the order will never fill again.
TERMINAL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected"})


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
            "cumulative_filled_quantity": self.filled_quantity,
            "cumulative_filled_avg_price": self.filled_avg_price,
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
        """Acknowledged orders whose latest cumulative fill state may have changed."""
        del lookback  # Truncating the order history could strand an older partial fill.
        state = project_submission_state(self.journal)
        events = self.journal.read_events()
        if self.journal.last_dropped_lines:
            raise RuntimeError("journal event history is corrupt; refusing fill reconciliation")
        legacy_terminal = {
            str(event.payload.get("client_order_id"))
            for event in events
            if event.event_type == FILL_EVENT
            if event.payload.get("client_order_id")
        }
        latest_snapshots = latest_fill_snapshots(events)
        latest_status = {
            coid: str(event.payload.get("broker_status", "")).lower()
            for coid, event in latest_snapshots.items()
        }
        found: dict[str, PendingOrder] = {}
        for record in self.journal.read_all():
            if self.journal.last_dropped_lines:
                raise RuntimeError("journal cycle history is corrupt; refusing fill reconciliation")
            execution = record.execution
            if execution.status != "SUBMITTED":
                continue
            coid = execution.client_order_id
            if not coid or coid in legacy_terminal or coid in found:
                continue
            if execution.filled_quantity > 0 and coid not in latest_snapshots:
                continue
            if latest_status.get(coid) in TERMINAL_STATUSES:
                continue
            found[coid] = PendingOrder(
                client_order_id=coid,
                order_id=execution.order_id,
                symbol=record.decision.symbol,
                side=record.decision.action,
                quantity=float(record.decision.quantity),
                strategy_id=record.strategy_id or record.decision.strategy_id,
            )
        for client_order_id, payload in state.acknowledged.items():
            if client_order_id in found or client_order_id in legacy_terminal:
                continue
            if latest_status.get(client_order_id) in TERMINAL_STATUSES:
                continue
            found[client_order_id] = _pending_from_payload(client_order_id, payload)
        for event in events:
            if event.event_type != "ORDER_SUBMISSION_RESOLVED":
                continue
            candidate_client_order_id = event.payload.get("client_order_id")
            if not isinstance(candidate_client_order_id, str) or not candidate_client_order_id:
                continue
            if candidate_client_order_id in found or candidate_client_order_id in legacy_terminal:
                continue
            if latest_status.get(candidate_client_order_id) in TERMINAL_STATUSES:
                continue
            found[candidate_client_order_id] = _pending_from_payload(
                candidate_client_order_id, event.payload
            )
        return list(found.values())

    def resolve(self, *, lookback: int = 500) -> list[FillConfirmation]:
        confirmations = []
        prior = latest_fill_snapshots(self.journal.read_events())
        for order in self.pending(lookback=lookback):
            confirmation = self._resolve_one(order)
            if confirmation is not None and not _same_snapshot(
                prior.get(order.client_order_id), confirmation
            ):
                confirmations.append(confirmation)
        return confirmations

    def _resolve_one(self, pending: PendingOrder) -> FillConfirmation | None:
        try:
            order = self.client.get_order_by_client_order_id(pending.client_order_id)
        except Exception:
            return None
        if order is None:
            return None
        # The order must be the one that was asked for. The lookup is by `client_order_id`, and a
        # broker that answers it with a *different* order - a loose match, a stale index, a
        # different account's fill, a replacement that replaced the one we tracked - would have
        # that order's quantity and price confirmed against this pending order, and the journal
        # would record a fill that never happened to this order. Nothing downstream can tell: the
        # confirmation is well-formed, the order id is the pending one, and only the numbers are
        # somebody else's.
        #
        # Three identities are compared, because the broker knows more than one: the client order id
        # it answered with, and the broker's own order id when we recorded one. Both must match or
        # the confirmation is refused.
        #
        # An *absent* field is not a contradiction. A broker that does not report `client_order_id`
        # at all must not break reconciliation - the query was made with the pending id, so a silent
        # answer is a missing field rather than a different one. Refusing on absence would turn one
        # wrong fill into zero fills, which is a worse failure than the one being fixed here; that
        # is why the id comparison is conditional on the broker having said something, and the
        # order-id one on this deployment having recorded one.
        returned_client_order_id = str(getattr(order, "client_order_id", "") or "")
        returned_order_id = str(getattr(order, "id", "") or "")
        if returned_client_order_id and returned_client_order_id != pending.client_order_id:
            return None
        if pending.order_id and returned_order_id and returned_order_id != pending.order_id:
            return None
        broker_status = str(getattr(order, "status", "") or "unknown")
        if broker_status.lower() == "replaced":
            self.journal.append_event(JournalEvent(
                event_id=str(uuid4()),
                event_type="ORDER_SUBMISSION_UNKNOWN",
                timestamp=datetime.now(tz=timezone.utc),
                status="FAILED",
                message="broker order is replaced; replacement chain requires operator investigation",
                payload={
                    "client_order_id": pending.client_order_id,
                    "order_id": returned_order_id or pending.order_id,
                    "symbol": pending.symbol,
                    "action": pending.side,
                    "quantity": pending.quantity,
                    "strategy_id": pending.strategy_id,
                    "broker_status": broker_status,
                    "replaced_by": str(getattr(order, "replaced_by", "") or "") or None,
                    "paper_only": True,
                },
            ))
            return None
        filled = _float(getattr(order, "filled_qty", 0))
        status = broker_status
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


def latest_fill_snapshots(events: list[JournalEvent]) -> dict[str, JournalEvent]:
    latest: dict[str, JournalEvent] = {}
    for event in events:
        if event.event_type != FILL_SNAPSHOT_EVENT:
            continue
        client_order_id = event.payload.get("client_order_id")
        if isinstance(client_order_id, str) and client_order_id:
            latest[client_order_id] = event
    return latest


def confirmed_fill_events(events: list[JournalEvent]) -> list[JournalEvent]:
    """Project one latest cumulative fill row per order plus legacy confirmed fills."""
    snapshots = latest_fill_snapshots(events)
    covered = set(snapshots)
    projected = [
        event for event in events
        if event.event_type == FILL_EVENT
        and (
            not isinstance(event.payload.get("client_order_id"), str)
            or event.payload.get("client_order_id") not in covered
        )
    ]
    projected.extend(snapshots.values())
    return sorted(projected, key=lambda event: event.timestamp)


def _pending_from_payload(client_order_id: str, payload: dict[str, object]) -> PendingOrder:
    symbol = payload.get("symbol")
    side = payload.get("action", payload.get("side"))
    quantity = payload.get("quantity", payload.get("requested_quantity", 0))
    strategy_id = payload.get("strategy_id")
    order_id = payload.get("order_id")
    return PendingOrder(
        client_order_id=client_order_id,
        order_id=order_id if isinstance(order_id, str) and order_id else None,
        symbol=symbol if isinstance(symbol, str) else "",
        side=side if isinstance(side, str) else "",
        quantity=_float(quantity),
        strategy_id=strategy_id if isinstance(strategy_id, str) else None,
    )


def _same_snapshot(event: JournalEvent | None, confirmation: FillConfirmation) -> bool:
    if event is None:
        return False
    previous = event.payload
    return (
        _float(previous.get("cumulative_filled_quantity", previous.get("filled_quantity")))
        == confirmation.filled_quantity
        and _optional_float(previous.get("cumulative_filled_avg_price", previous.get("filled_avg_price")))
        == confirmation.filled_avg_price
        and str(previous.get("broker_status", "")).lower() == confirmation.broker_status.lower()
    )
