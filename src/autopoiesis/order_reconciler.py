from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import JournalEvent
from autopoiesis.submission_state import SubmissionJournalError, project_submission_state


@dataclass(frozen=True)
class ReconciliationReport:
    matched: bool
    submitted_order_ids: tuple[str, ...]
    broker_open_order_ids: tuple[str, ...]
    missing_from_broker: tuple[str, ...] = field(default_factory=tuple)
    extra_broker_orders: tuple[str, ...] = field(default_factory=tuple)
    unresolved_client_order_ids: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""


class OrderReconciler:
    def __init__(self, *, client, journal: JsonlJournal):
        self.client = client
        self.journal = journal

    def reconcile(self, *, lookback: int = 200) -> ReconciliationReport:
        del lookback  # Full-history projection is required to find old unresolved intents.
        try:
            state = project_submission_state(self.journal)
        except SubmissionJournalError as exc:
            return ReconciliationReport(
                matched=False,
                submitted_order_ids=(),
                broker_open_order_ids=(),
                message=f"journal safety projection failed: {exc}",
            )

        self.resolve_unresolved_submissions()
        try:
            state = project_submission_state(self.journal)
        except SubmissionJournalError as exc:
            return ReconciliationReport(
                matched=False,
                submitted_order_ids=(),
                broker_open_order_ids=(),
                message=f"journal safety projection failed after lookup: {exc}",
            )
        submitted = tuple(sorted(state.known_order_ids))
        unresolved = tuple(sorted(state.unresolved))

        try:
            broker_orders = self._open_order_ids()
        except Exception as exc:
            return ReconciliationReport(
                matched=False,
                submitted_order_ids=submitted,
                broker_open_order_ids=(),
                unresolved_client_order_ids=unresolved,
                message=f"broker open order fetch failed: {exc}",
            )

        submitted_set = set(submitted)
        broker_set = set(broker_orders)
        missing = ()
        extra = tuple(sorted(broker_set - submitted_set))
        matched = not extra and not unresolved
        if unresolved:
            message = "unresolved broker submission(s): " + ", ".join(unresolved)
        else:
            message = "matched" if matched else "broker has open orders not found in journal"
        return ReconciliationReport(
            matched=matched,
            submitted_order_ids=submitted,
            broker_open_order_ids=tuple(broker_orders),
            missing_from_broker=missing,
            extra_broker_orders=extra,
            unresolved_client_order_ids=unresolved,
            message=message,
        )

    def resolve_unresolved_submissions(self) -> tuple[str, ...]:
        """Retry exact client-ID lookups; return IDs that remain ambiguous."""
        state = project_submission_state(self.journal)
        self._resolve_unknowns(state.unresolved)
        return tuple(sorted(project_submission_state(self.journal).unresolved))

    def _resolve_unknowns(self, unresolved: dict[str, dict[str, object]]) -> None:
        lookup = getattr(self.client, "get_order_by_client_order_id", None)
        if lookup is None:
            return
        for client_order_id, identity in unresolved.items():
            try:
                order = lookup(client_order_id)
            except Exception:
                continue
            if order is None:
                continue
            returned_client_order_id = str(getattr(order, "client_order_id", "") or "")
            order_id = str(getattr(order, "id", "") or "")
            if returned_client_order_id != client_order_id or not order_id:
                continue
            broker_status = str(getattr(order, "status", "unknown") or "unknown").lower()
            # A replaced order needs a linked replacement lookup and fill projection.
            # Until that chain is represented safely, preserve the global block.
            if broker_status == "replaced":
                continue
            payload = {
                **identity,
                "client_order_id": client_order_id,
                "order_id": order_id,
                "broker_status": broker_status,
                "filled_quantity": _float(getattr(order, "filled_qty", 0)),
                "filled_avg_price": _optional_float(getattr(order, "filled_avg_price", None)),
                "source": "alpaca",
                "paper_only": True,
            }
            self.journal.append_event(JournalEvent(
                event_id=str(uuid4()),
                event_type="ORDER_SUBMISSION_RESOLVED",
                timestamp=datetime.now(tz=timezone.utc),
                status="SUCCESS",
                message="exact broker order recovered by client order ID",
                cycle_id=str(identity.get("cycle_id")) if identity.get("cycle_id") else None,
                strategy_id=str(identity.get("strategy_id")) if identity.get("strategy_id") else None,
                payload=payload,
            ))

    def _open_order_ids(self) -> tuple[str, ...]:
        if hasattr(self.client, "list_orders"):
            orders = self.client.list_orders(status="open")
        else:
            orders = self.client.get_orders(status="open")
        ids = []
        for order in orders:
            order_id = str(getattr(order, "id", ""))
            if order_id:
                ids.append(order_id)
        return tuple(ids)


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
