"""Fail-closed projection of broker submission attempts from the durable journal."""
from __future__ import annotations

from dataclasses import dataclass, field

from autopoiesis.journal import JsonlJournal

SUBMISSION_EVENTS = frozenset(
    {
        "ORDER_SUBMISSION_INTENT",
        "ORDER_SUBMISSION_ACKNOWLEDGED",
        "ORDER_SUBMISSION_UNKNOWN",
        "ORDER_SUBMISSION_RESOLVED",
        "ORDER_SUBMISSION_MANUALLY_RESOLVED",
    }
)


class SubmissionJournalError(RuntimeError):
    """The journal cannot safely prove the state of broker submissions."""


@dataclass(frozen=True)
class SubmissionJournalState:
    unresolved: dict[str, dict[str, object]] = field(default_factory=dict)
    acknowledged: dict[str, dict[str, object]] = field(default_factory=dict)
    known_order_ids: frozenset[str] = frozenset()
    attempt_client_order_ids: frozenset[str] = frozenset()


def project_submission_state(journal: JsonlJournal) -> SubmissionJournalState:
    """Project intent, acknowledgement, ambiguity, and legacy errors by client order ID.

    Full reads are intentional: a recent-window query cannot prove that an old unresolved
    intent has disappeared. Corrupt rows or an untrusted archive baseline stop the projection.
    """
    archive = journal.archive_manifest_status()
    if not archive["valid"] or not archive["trusted"]:
        raise SubmissionJournalError(f"journal archive baseline is not trusted: {archive['reason']}")

    records = journal.read_all()
    if journal.last_dropped_lines:
        raise SubmissionJournalError("journal cycle history is corrupt; refusing submission-state read")
    events = journal.read_events()
    if journal.last_dropped_lines:
        raise SubmissionJournalError("journal event history is corrupt; refusing submission-state read")

    states: dict[str, str] = {}
    details: dict[str, dict[str, object]] = {}
    acknowledged: dict[str, dict[str, object]] = {}
    known_order_ids: set[str] = set()
    attempts: set[str] = set()

    for event in events:
        if event.event_type not in SUBMISSION_EVENTS:
            continue
        client_order_id = event.payload.get("client_order_id")
        if not isinstance(client_order_id, str) or not client_order_id:
            raise SubmissionJournalError(
                f"submission event {event.event_id} has no client_order_id"
            )
        attempts.add(client_order_id)
        details.setdefault(client_order_id, {}).update(event.payload)
        details[client_order_id]["cycle_id"] = event.cycle_id
        details[client_order_id]["strategy_id"] = event.strategy_id
        states[client_order_id] = event.event_type

        order_id = event.payload.get("order_id")
        if event.event_type in {"ORDER_SUBMISSION_ACKNOWLEDGED", "ORDER_SUBMISSION_RESOLVED"}:
            if isinstance(order_id, str) and order_id:
                known_order_ids.add(order_id)
            if event.event_type == "ORDER_SUBMISSION_ACKNOWLEDGED":
                acknowledged[client_order_id] = dict(details[client_order_id])

    for record in records:
        execution = record.execution
        client_order_id = execution.client_order_id
        if not client_order_id:
            continue
        if execution.status == "SUBMITTED":
            attempts.add(client_order_id)
            if execution.order_id:
                known_order_ids.add(execution.order_id)
        elif execution.status == "ERROR" and record.decision.action != "HOLD":
            # Legacy executor errors followed the submit call and cannot prove that Alpaca
            # rejected the order. Treat them as potentially accepted unless a later durable
            # event for the same ID resolves them.
            attempts.add(client_order_id)
            if client_order_id not in states:
                states[client_order_id] = "LEGACY_ERROR"
                details[client_order_id] = {
                    "client_order_id": client_order_id,
                    "legacy_cycle_id": record.cycle_id,
                    "symbol": record.decision.symbol,
                    "action": record.decision.action,
                    "quantity": record.decision.quantity,
                    "strategy_id": record.strategy_id or record.decision.strategy_id,
                }

    unresolved = {
        client_order_id: details[client_order_id]
        for client_order_id, status in states.items()
        if status in {
            "ORDER_SUBMISSION_INTENT",
            "ORDER_SUBMISSION_UNKNOWN",
            "ORDER_SUBMISSION_MANUALLY_RESOLVED",
            "LEGACY_ERROR",
        }
    }
    return SubmissionJournalState(
        unresolved=unresolved,
        acknowledged=acknowledged,
        known_order_ids=frozenset(known_order_ids),
        attempt_client_order_ids=frozenset(attempts),
    )
