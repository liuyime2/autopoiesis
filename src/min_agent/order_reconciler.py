from __future__ import annotations

from dataclasses import dataclass, field

from min_agent.journal import JsonlJournal


@dataclass(frozen=True)
class ReconciliationReport:
    matched: bool
    submitted_order_ids: tuple[str, ...]
    broker_open_order_ids: tuple[str, ...]
    missing_from_broker: tuple[str, ...] = field(default_factory=tuple)
    extra_broker_orders: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""


class OrderReconciler:
    def __init__(self, *, client, journal: JsonlJournal):
        self.client = client
        self.journal = journal

    def reconcile(self, *, lookback: int = 200) -> ReconciliationReport:
        submitted = []
        for record in self.journal.last_n(lookback):
            order_id = record.execution.order_id
            if record.execution.status == "SUBMITTED" and order_id:
                submitted.append(order_id)

        try:
            broker_orders = self._open_order_ids()
        except Exception as exc:
            return ReconciliationReport(
                matched=False,
                submitted_order_ids=tuple(submitted),
                broker_open_order_ids=(),
                message=f"broker open order fetch failed: {exc}",
            )

        submitted_set = set(submitted)
        broker_set = set(broker_orders)
        missing = ()
        extra = tuple(sorted(broker_set - submitted_set))
        matched = not extra
        message = "matched" if matched else "broker has open orders not found in journal"
        return ReconciliationReport(
            matched=matched,
            submitted_order_ids=tuple(submitted),
            broker_open_order_ids=tuple(broker_orders),
            missing_from_broker=missing,
            extra_broker_orders=extra,
            message=message,
        )

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
