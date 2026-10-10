from __future__ import annotations

import hashlib

from autopoiesis.coerce import datetime_utc
from autopoiesis.config import is_paper_endpoint
from autopoiesis.models import BROKER_SIDE, ExecutionResult, GuardianResult, TradeDecision


class AlpacaPaperExecutor:
    def __init__(self, *, client, base_url: str):
        # The same host test config.is_paper_endpoint() applies, so the last gate before
        # `submit_order` cannot be more permissive than the one at start-up. It used to be a
        # second, independent substring check.
        if not is_paper_endpoint(base_url):
            raise ValueError(
                f"AlpacaPaperExecutor requires Alpaca's paper host; got {base_url!r}"
            )
        self.client = client
        self.base_url = base_url

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
            return ExecutionResult(
                status="ERROR",
                order_id=None,
                client_order_id=client_order_id,
                filled_quantity=0,
                message=str(exc),
            )

        submitted_at = datetime_utc(getattr(order, "submitted_at", None))
        filled_at = datetime_utc(getattr(order, "filled_at", None))
        broker_status = str(getattr(order, "status", "submitted")) or "submitted"

        return ExecutionResult(
            status="SUBMITTED",
            order_id=str(getattr(order, "id", "")) or None,
            client_order_id=str(getattr(order, "client_order_id", "")) or client_order_id,
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


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


