from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from autopoiesis.broker_client import list_open_orders
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


#: Broker statuses at which a chain's last link is a known outcome. `filled` projects a fill;
#: `canceled`, `expired` and `rejected` are known outcomes with nothing to project, and knowing
#: that is what clears the block - "this order did not trade" is an answer, not a gap. Same set the
#: fill reconciler treats as terminal, restated because the two walks are different.
TERMINAL_FILL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected"})


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


@dataclass(frozen=True)
class _ChainOutcome:
    """What walking a replacement chain produced, and whether it can be trusted.

    `final` is the last link walked, which is only meaningful when `verified` - a chain that
    failed a check still walked as far as it did, and that partial walk is what gets recorded so
    the next pass starts from somewhere.

    `progress` is set when the chain is intact but has not finished: a partial fill. It is not a
    failure, and the caller projects its cumulative fill while leaving the block on.
    """

    verified: bool
    reason: str
    chain: list[dict[str, object]]
    final: dict[str, object] | None
    progress: dict[str, object] | None = None


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
        """Walk a replaced order's chain, then act on how it ends.

        A `replaced` broker status is not a terminal state and not a fill: it records that this
        order was superseded, and `replaced_by` names the order that superseded it. The old
        behaviour skipped it and kept the global block, which is safe and also a dead end - an
        operator had to do the walk by hand, with nothing on the record about what the chain
        actually was.

        So the walk happens here, and it is bounded and identity-checked at every step. Each link
        must be the order the previous link named, and must carry the symbol, side and quantity the
        original submission declared. A link that is missing, unreadable, inconsistent, or that
        repeats an order already seen ends the walk unverified: the block stays, and the chain as
        far as it was walked is journalled with the reason, so the next pass - and the operator -
        start from more than nothing.

        The walk is bounded by the orders it has already seen rather than trusting the broker's
        chain length, because a malformed or self-referential `replaced_by` would otherwise spin
        forever inside a startup path.

        **How it ends decides what is recorded.** An unverified chain keeps the block and records
        `ORDER_REPLACEMENT_CHAIN_UNVERIFIED`. A verified chain ends at a terminal link, and then
        the submission's outcome is known - so the block is cleared with `ORDER_SUBMISSION_RESOLVED`
        and, when that link filled, the fill is projected as an `ORDER_FILL_SNAPSHOT` against the
        *original* client order id. A chain ending non-terminal (a partial fill) has its cumulative
        progress projected but keeps the block, because more of it can still arrive.

        Clearing the block is the point of walking: an ambiguous submit leaves the whole agent
        unable to trade until a human intervenes, and a chain that can be verified end to end is
        exactly the case where that intervention is not needed.
        """
        outcome = self._walk_replacement_chain(client_order_id, identity, head)
        # A chain that ended non-terminal is not a *failed* walk: the walk succeeded and the chain
        # is intact, it just has not finished. Its cumulative progress is projected so the ledger
        # sees what has filled so far, and the block stays on because more can still arrive. The
        # distinction matters in the record: "walk failed" needs an operator, "still filling" does
        # not - it will be picked up on the next pass.
        if outcome.progress is not None:
            self._append_fill_snapshot(client_order_id, identity, outcome.progress)
        if not outcome.verified:
            self._append_submission_event(
                "ORDER_REPLACEMENT_CHAIN_UNVERIFIED",
                status="FAILED",
                message=f"replacement chain walked, not verified: {outcome.reason}",
                client_order_id=client_order_id,
                identity=identity,
                extra={"observed_chain": outcome.chain, "reason": outcome.reason},
            )
            return

        final = outcome.final
        # `verified` implies a final link: the walk only reports verified when it reached a
        # terminal order, and that order is `final`. Stated rather than asserted so a future change
        # to the walk cannot make this an AttributeError inside a startup path.
        if final is None:  # pragma: no cover - the walk sets final whenever it sets verified
            self._append_submission_event(
                "ORDER_REPLACEMENT_CHAIN_UNVERIFIED",
                status="FAILED",
                message="replacement chain reported verified without a final link",
                client_order_id=client_order_id,
                identity=identity,
                extra={"observed_chain": outcome.chain, "reason": outcome.reason},
            )
            return
        filled = _float(final.get("filled_quantity"))
        status = str(final.get("status") or "").lower()
        payload = {
            **identity,
            "client_order_id": client_order_id,
            "order_id": final.get("order_id"),
            "replacement_chain": outcome.chain,
            "filled_quantity": filled,
            "filled_avg_price": _optional_float(final.get("filled_avg_price")),
            "paper_only": True,
        }
        if filled > 0:
            self._append_fill_snapshot(client_order_id, identity, final)
        self._append_submission_event(
            "ORDER_SUBMISSION_RESOLVED",
            status="SUCCESS",
            message=(
                "replacement chain verified end to end; "
                + (f"fill of {filled} projected" if filled > 0 else f"terminal as {status!r} "
                   "with no fill, so the order did not trade")
            ),
            client_order_id=client_order_id,
            identity=identity,
            extra=payload,
        )

    def _walk_replacement_chain(
        self, client_order_id: str, identity: dict[str, object], head: object
    ) -> _ChainOutcome:
        """Follow `replaced_by` to the end, checking each link, and say how it ended.

        Pure with respect to the journal: it reads the broker and returns what it saw. Every
        journalling decision belongs to the caller, which is what makes the unverified and
        verified paths distinguishable in the record rather than in a log.
        """
        by_order_id = getattr(self.client, "get_order", None)
        if by_order_id is None:
            # A broker client that cannot look an order up by id cannot have its chain walked.
            # The block stays; this is recorded rather than silently skipped, because "the chain
            # could not be walked" and "the chain ends here" are different operator actions.
            return _ChainOutcome(
                verified=False,
                reason="no order lookup by id",
                chain=[],
                final=None,
            )

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
                return _ChainOutcome(False, "replacement chain repeats or ends without an order "
                                    "id", observed, None)
            seen.add(order_id)
            observed.append(cursor)
            next_id = cursor.get("replaced_by")
            if not next_id:
                status = str(cursor.get("status") or "").lower()
                if status not in TERMINAL_FILL_STATUSES:
                    # The chain is whole; it has not finished. `progress` carries the current link
                    # so its cumulative fill can be projected, and the walk is unverified so the
                    # block stays on.
                    return _ChainOutcome(
                        False, f"chain has not finished; status {status!r}", observed, cursor,
                        progress=cursor,
                    )
                return _ChainOutcome(True, "chain end reached at a terminal order",
                                     observed, cursor)
            try:
                following = by_order_id(str(next_id))
            except Exception as exc:
                # The exception's type is recorded, not its message. A message can carry the host,
                # the URL and the request; a type is what an operator acts on, and copying a
                # third party's error text into an append-only journal is how host details end up
                # in a record nobody sanitises.
                return _ChainOutcome(
                    False, f"replacement {next_id} could not be read: {type(exc).__name__}",
                    observed, None,
                )
            if following is None:
                return _ChainOutcome(
                    False, f"replacement {next_id} does not exist at the broker", observed, None,
                )
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
                return _ChainOutcome(False, reason, observed, None)
            if (
                declared_quantity is not None
                and following_quantity is not None
                and abs(following_quantity - declared_quantity) > 1e-9
            ):
                reason = (
                    f"replacement {next_id} is for {following_quantity} against the submitted "
                    f"{declared_quantity}"
                )
                return _ChainOutcome(False, reason, observed, None)
            cursor = {
                "order_id": str(getattr(following, "id", "") or str(next_id)),
                "status": str(getattr(following, "status", "unknown") or "unknown"),
                "replaced_by": str(getattr(following, "replaced_by", "") or "") or None,
                "client_order_id": str(getattr(following, "client_order_id", "") or "") or None,
                "filled_quantity": _float(getattr(following, "filled_qty", 0)),
                "filled_avg_price": _optional_float(getattr(following, "filled_avg_price", None)),
            }
        return _ChainOutcome(False, reason, observed, None)  # pragma: no cover - loop always exits

    def _append_fill_snapshot(
        self, client_order_id: str, identity: dict[str, object], final: dict[str, object]
    ) -> None:
        """Project the chain's fill as the original submission's fill.

        The `ORDER_FILL_SNAPSHOT` payload is `FillConfirmation.payload()`'s, reused rather than
        restated: that shape is what `confirmed_fill_events` projects into the evaluator, and a
        second spelling of it here would be a second place for the two to drift apart - the same
        defect class as the cost formula being computed twice.

        The snapshot is keyed by the *original* client order id, which is what makes the fill
        attributable to the submission that placed it even though the broker's own order id is the
        replacement's.
        """
        from autopoiesis.fill_reconciler import FillConfirmation, PendingOrder

        confirmation = FillConfirmation(
            pending=PendingOrder(
                client_order_id=client_order_id,
                order_id=str(final.get("order_id") or "") or None,
                symbol=str(identity.get("symbol") or ""),
                side=str(identity.get("action") or ""),
                quantity=_float(identity.get("quantity")),
                strategy_id=str(identity.get("strategy_id")) if identity.get("strategy_id") else None,
            ),
            broker_status=str(final.get("status") or ""),
            filled_quantity=_float(final.get("filled_quantity")),
            filled_avg_price=_optional_float(final.get("filled_avg_price")),
        )
        self.journal.append_event(JournalEvent(
            event_id=str(uuid4()),
            event_type="ORDER_FILL_SNAPSHOT",
            timestamp=datetime.now(tz=timezone.utc),
            status="SUCCESS",
            message="fill projected from a verified replacement chain",
            cycle_id=str(identity.get("cycle_id")) if identity.get("cycle_id") else None,
            strategy_id=str(identity.get("strategy_id")) if identity.get("strategy_id") else None,
            payload=confirmation.payload(),
        ))

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
        return tuple(order.id for order in list_open_orders(self.client))

