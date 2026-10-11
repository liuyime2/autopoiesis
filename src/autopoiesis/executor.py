from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from autopoiesis.coerce import datetime_utc
from autopoiesis.config import is_paper_endpoint
from autopoiesis.models import BROKER_SIDE, ExecutionResult, GuardianResult, JournalEvent, TradeDecision
from autopoiesis.submission_state import SubmissionJournalError, project_submission_state


class AlpacaPaperExecutor:
    def __init__(self, *, client, base_url: str, journal):
        # The same host test config.is_paper_endpoint() applies, so the last gate before
        # `submit_order` cannot be more permissive than the one at start-up. It used to be a
        # second, independent substring check.
        if not is_paper_endpoint(base_url):
            raise ValueError(
                f"AlpacaPaperExecutor requires Alpaca's paper host; got {base_url!r}"
            )
        self.client = client
        self.base_url = base_url
        self.journal = journal

    def execute(
        self,
        decision: TradeDecision,
        guardian_result: GuardianResult,
        *,
        cycle_id: str | None = None,
    ) -> ExecutionResult:
        client_order_id = self.client_order_id(decision, cycle_id=cycle_id) if decision.action != "HOLD" else None

        if not guardian_result.approved:
            return ExecutionResult(
                status="REJECTED",
                order_id=None,
                client_order_id=client_order_id,
                filled_quantity=0,
                message=guardian_result.reason,
            )

        if decision.action == "HOLD":
            return ExecutionResult(status="SKIPPED", order_id=None, client_order_id=None, filled_quantity=0, message="hold")

        try:
            state = project_submission_state(self.journal)
        except SubmissionJournalError as exc:
            return ExecutionResult(
                status="SKIPPED", order_id=None, client_order_id=client_order_id,
                filled_quantity=0, message=f"submission refused: {exc}",
            )
        if state.unresolved:
            unresolved_id = sorted(state.unresolved)[0]
            return ExecutionResult(
                status="SKIPPED", order_id=None, client_order_id=client_order_id,
                filled_quantity=0,
                message=f"submission refused: unresolved broker order {unresolved_id}",
            )

        identity = {
            "client_order_id": client_order_id,
            "cycle_id": cycle_id,
            "symbol": decision.symbol.upper(),
            "action": decision.action,
            "side": BROKER_SIDE[decision.action],
            "quantity": float(decision.quantity),
            "order_type": "market",
            "time_in_force": "day",
            "strategy_id": decision.strategy_id,
            "paper_only": True,
        }
        self.journal.append_event(_submission_event(
            "ORDER_SUBMISSION_INTENT", identity, status="ACCEPTED",
            message="durable write-ahead order intent",
        ))

        try:
            order = self.client.submit_order(
                symbol=decision.symbol,
                qty=decision.quantity,
                # SHORT is submitted as a sell and COVER as a buy; the decision keeps the
                # intent, which is what the journal and the ledger read.
                side=BROKER_SIDE[decision.action],
                type="market",
                time_in_force="day",
                client_order_id=client_order_id,
            )
        except Exception as exc:  # pragma: no cover - external SDK errors vary
            self.journal.append_event(_submission_event(
                "ORDER_SUBMISSION_UNKNOWN",
                {**identity, "exception_type": type(exc).__name__},
                status="FAILED",
                message="broker submit outcome is unknown; lookup by client order ID required",
            ))
            return ExecutionResult(
                status="SUBMISSION_UNKNOWN",
                order_id=None,
                client_order_id=client_order_id,
                filled_quantity=0,
                message="broker submit outcome is unknown; reconciliation required",
            )

        submitted_at = datetime_utc(getattr(order, "submitted_at", None))
        filled_at = datetime_utc(getattr(order, "filled_at", None))
        broker_status = str(getattr(order, "status", "submitted")) or "submitted"
        returned_client_order_id = str(getattr(order, "client_order_id", "") or "")
        order_id = str(getattr(order, "id", "") or "")
        if not order_id or returned_client_order_id != client_order_id:
            self.journal.append_event(_submission_event(
                "ORDER_SUBMISSION_UNKNOWN",
                {
                    **identity,
                    "observed_order_id": order_id or None,
                    "observed_client_order_id": returned_client_order_id or None,
                },
                status="FAILED",
                message="broker response did not confirm the expected order identity",
            ))
            return ExecutionResult(
                status="SUBMISSION_UNKNOWN", order_id=None, client_order_id=client_order_id,
                filled_quantity=0, message="broker response identity is ambiguous; reconciliation required",
            )

        self.journal.append_event(_submission_event(
            "ORDER_SUBMISSION_ACKNOWLEDGED",
            {
                **identity,
                "order_id": order_id,
                "broker_status": broker_status,
            },
            status="SUCCESS",
            message="broker acknowledged the client order ID",
        ))

        return ExecutionResult(
            status="SUBMITTED",
            order_id=order_id,
            client_order_id=client_order_id,
            filled_quantity=float(getattr(order, "filled_qty", 0) or 0),
            filled_avg_price=_float_or_none(getattr(order, "filled_avg_price", None)),
            filled_at=filled_at,
            submitted_at=submitted_at,
            broker_status=broker_status,
            message=broker_status,
        )

    def client_order_id(self, decision: TradeDecision, *, cycle_id: str | None = None) -> str:
        raw = "|".join(
            [
                cycle_id or "manual",
                decision.symbol,
                decision.action,
                str(decision.quantity),
                decision.strategy_id or "no-strategy",
            ]
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"autopoiesis-{digest}"


def _submission_event(event_type, payload, *, status, message) -> JournalEvent:
    return JournalEvent(
        event_id=str(uuid4()),
        event_type=event_type,
        timestamp=datetime.now(tz=timezone.utc),
        status=status,
        message=message,
        cycle_id=payload.get("cycle_id"),
        strategy_id=payload.get("strategy_id"),
        payload=payload,
    )


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

