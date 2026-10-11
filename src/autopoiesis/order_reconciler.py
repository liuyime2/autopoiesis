from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import JournalEvent, JournalEventStatus, JournalEventType
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


#: Broker statuses at which a chain's last link is a fill that can be projected. Same set the fill
#: reconciler treats as terminal, restated here because the two walks are different: that one
#: resolves a directly acknowledged order, this one walks to the end of a replacement chain.
TERMINAL_FILL_STATUSES = frozenset({"filled"})


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
            # A replaced order is the head of a chain, not a dead end. Follow `replaced_by` to
            # the order that superseded it and keep following to the end; the last link is the
            # one that can hold the fill. Until the whole chain is represented the block stays,
            # so a partial walk is still progress and still safe.
            if broker_status == "replaced":
                self._record_replacement_chain(client_order_id, identity, order)
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

    def _record_replacement_chain(
        self, client_order_id: str, identity: dict[str, object], head: object
    ) -> None:
        """Walk a replaced order's chain and journalled what it found.

        A `replaced` order is not a terminal state and not a fill: it records that this order was
        superseded, and `replaced_by` names the order that superseded it. The old behaviour skipped
        it and kept the global block, which is safe and also a dead end - an operator had to do the
        walk by hand, with nothing on the record about what the chain actually was.

        So the walk happens here, and it is bounded and identity-checked at every step. Each link
        must be the order the previous link named, and must carry the symbol, side and quantity the
        original submission declared. A link that is missing, unreadable or inconsistent ends the
        walk as unverified: the block stays, and the chain as far as it was walked is journalled so
        the next pass - and the operator - starts from more than nothing.

        The walk is bounded rather than trusting the broker's chain length, because a malformed or
        self-referential `replaced_by` would otherwise loop forever inside a startup path.

        Nothing here resolves an order by itself. It records the chain; the chain is journalled, and
        a fill is only projected for a link that is both reachable and filled, by the resolve path
        that already exists for a directly acknowledged order.
        """
        by_order_id = getattr(self.client, "get_order", None)
        if by_order_id is None:
            # A broker client that cannot look an order up by id cannot have its chain walked.
            # The block stays; this is recorded rather than silently skipped, because "the chain
            # could not be walked" and "the chain ends here" are different operator actions.
            self._append_submission_event(
                "ORDER_REPLACEMENT_CHAIN_UNVERIFIED",
                status="FAILED",
                message="broker client cannot look up an order by id; replacement chain not walked",
                client_order_id=client_order_id,
                identity=identity,
                extra={"observed_chain": [], "reason": "no order lookup by id"},
            )
            return

        declared_symbol = str(identity.get("symbol") or "").upper()
        declared_side = str(identity.get("action") or "").upper()
        declared_quantity = _optional_float(identity.get("quantity"))

        observed: list[dict[str, object]] = []
        seen: set[str] = set()
        cursor: dict[str, object] | None = {
            "order_id": str(getattr(head, "id", "") or ""),
            "status": str(getattr(head, "status", "unknown") or "unknown"),
            "replaced_by": str(getattr(head, "replaced_by", "") or "") or None,
            "client_order_id": str(getattr(head, "client_order_id", "") or "") or client_order_id,
            "filled_quantity": _float(getattr(head, "filled_qty", 0)),
            "filled_avg_price": _optional_float(getattr(head, "filled_avg_price", None)),
        }
        reason = "chain end not recognised as filled"
        while cursor is not None:
            order_id = str(cursor.get("order_id") or "")
            if not order_id or order_id in seen:
                reason = "replacement chain repeats or ends without an order id"
                break
            seen.add(order_id)
            observed.append(cursor)
            next_id = cursor.get("replaced_by")
            if not next_id:
                status = str(cursor.get("status") or "").lower()
                if status == "replaced":
                    reason = "chain ends at a replaced order that names no replacement"
                elif status in TERMINAL_FILL_STATUSES:
                    reason = "chain end reached at a filled order"
                else:
                    reason = f"chain ends at a non-terminal status {status!r}"
                break
            try:
                following = by_order_id(str(next_id))
            except Exception as exc:
                reason = f"replacement {next_id} could not be read: {type(exc).__name__}"
                break
            if following is None:
                reason = f"replacement {next_id} does not exist at the broker"
                break
            following_symbol = str(getattr(following, "symbol", "") or "").upper()
            following_side = str(getattr(following, "side", "") or "").upper()
            following_quantity = _optional_float(getattr(following, "qty", None))
            if (
                declared_symbol and following_symbol and following_symbol != declared_symbol
            ) or (declared_side and following_side and following_side != declared_side):
                reason = (
                    f"replacement {next_id} does not match the submitted order "
                    f"({following_symbol} {following_side} against {declared_symbol} "
                    f"{declared_side})"
                )
                break
            if (
                declared_quantity is not None
                and following_quantity is not None
                and abs(following_quantity - declared_quantity) > 1e-9
            ):
                reason = (
                    f"replacement {next_id} is for {following_quantity} against the submitted "
                    f"{declared_quantity}"
                )
                break
            cursor = {
                "order_id": str(getattr(following, "id", "") or str(next_id)),
                "status": str(getattr(following, "status", "unknown") or "unknown"),
                "replaced_by": str(getattr(following, "replaced_by", "") or "") or None,
                "client_order_id": str(getattr(following, "client_order_id", "") or "") or None,
                "filled_quantity": _float(getattr(following, "filled_qty", 0)),
                "filled_avg_price": _optional_float(getattr(following, "filled_avg_price", None)),
            }

        self._append_submission_event(
            "ORDER_REPLACEMENT_CHAIN_UNVERIFIED",
            status="FAILED",
            message=f"replacement chain walked, not verified: {reason}",
            client_order_id=client_order_id,
            identity=identity,
            extra={"observed_chain": observed, "reason": reason},
        )

    def _append_submission_event(
        self,
        event_type: JournalEventType,
        *,
        status: JournalEventStatus,
        message: str,
        client_order_id: str,
        identity: dict[str, object],
        extra: dict[str, object] | None = None,
    ) -> None:
        """One journalled record about a submission's state, with the identity attached."""
        payload = {
            **identity,
            "client_order_id": client_order_id,
            "paper_only": True,
            **(extra or {}),
        }
        self.journal.append_event(JournalEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=datetime.now(tz=timezone.utc),
            status=status,
            message=message,
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


