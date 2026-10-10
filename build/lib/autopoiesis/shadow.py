"""Shadow trading: run the whole real path and submit nothing.

Phase 5 of the objective asks for shadow trading as a stage of its own, before
evidence-gated probation. It is the stage that answers one question the other
stages cannot: *would the full production decision path have produced a
profitable outcome if it had actually placed the order?*

It runs the real loop. Real market data, the real model, the real Guardian, the real
journal. Only the final call to the broker is replaced. That is deliberate and it is
the whole design: a shadow mode with its own simplified decision path would be
grading a different system from the one that will trade, and would pass while the
real path failed.

The safety property that matters most is negative. A shadow order is **not** a fill,
and the system must never be able to believe otherwise. If shadow results leaked into
the PnL ledger as executed trades, the account would show profits from money that was
never risked - the most dangerous possible bug in this system, because it would make
an unvalidated path look like the best one on record. Three things prevent it:

1. Shadow results carry ``status="SHADOWED"``, a distinct value that is not in the
   set the evaluator treats as executed. A shadow order cannot masquerade as a fill
   even if a caller passes it through the normal path.
2. ``order_id`` is always ``None``, so no reconciliation pass can match it to a
   broker order. A real fill always has one.
3. Every shadow order is journalled as its own event type, so the record of what
   *would* have happened is as durable as the record of what did.

`AUTOPOIESIS_SHADOW=1` turns the sink on. It is a config flag rather than a fourth
`mode` because `mode` already carries a hard safety meaning - anything but `paper`
raises at config load - and overloading it with "paper but pretend" would blur
exactly the line that must stay sharp.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from autopoiesis.models import (
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    JournalEventType,
    TradeDecision,
)

#: Emitted for every order that would have been submitted. Durable evidence that the
#: path ran, which is the entire point of the stage.
SHADOW_EVENT: JournalEventType = "SHADOW_ORDER_INTENT"


class ShadowExecutor:
    """A drop-in for `AlpacaPaperExecutor` that reaches no broker.

    Implements the same `execute` signature the loop depends on, so enabling shadow
    is a constructor swap in one place and the decision path above it is untouched.
    """

    def __init__(self, *, journal=None, clock=None):
        self.journal = journal
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.intents: list[ExecutionResult] = []
        #: Journal writes that failed. The except below must stay - a journal fault
        #: must never escalate into a real order - but swallowing it silently is how
        #: the first version of this file lost every shadow intent: the event type
        #: was not in JournalEventType, ValidationError was raised, and the broad
        #: except ate it. The stage then looked wired and journalled nothing.
        self.journal_failures: list[str] = []

    def execute(
        self,
        decision: TradeDecision,
        guardian_result: GuardianResult,
        *,
        cycle_id: str | None = None,
    ) -> ExecutionResult:
        client_order_id = (
            self.client_order_id(decision, cycle_id=cycle_id)
            if decision.action != "HOLD" else None
        )

        # The Guardian is consulted exactly as the real executor consults it, and its
        # rejection is honoured exactly. A shadow stage that approved orders the
        # Guardian would have blocked would validate a path the live system would
        # never take, and would report phantom edge on trades that could not happen.
        if not guardian_result.approved:
            return ExecutionResult(
                status="REJECTED",
                order_id=None,
                client_order_id=client_order_id,
                filled_quantity=0,
                message=f"shadow: blocked by guardian, {guardian_result.reason}",
            )

        if decision.action == "HOLD":
            return ExecutionResult(
                status="SKIPPED", order_id=None, client_order_id=None,
                filled_quantity=0, message="shadow: hold",
            )

        # No price is recorded. The shadow executor has no quote - the loop owns the
        # snapshot - and inventing one here would mean the shadow order was priced
        # by something other than the real path. `filled_avg_price` stays None so
        # nothing can mistake this for an executed fill.
        result = ExecutionResult(
            status="SHADOWED",
            order_id=None,
            client_order_id=client_order_id,
            filled_quantity=0,
            submitted_at=self.clock(),
            message=(
                f"shadow: would submit {decision.action} {decision.quantity} "
                f"{decision.symbol}"
            ),
        )
        self.intents.append(result)
        self._journal(decision, guardian_result, result, cycle_id)
        return result

    def _journal(
        self,
        decision: TradeDecision,
        guardian_result: GuardianResult,
        result: ExecutionResult,
        cycle_id: str | None,
    ) -> None:
        if self.journal is None:
            return
        try:
            # `append_event` takes a JournalEvent, not keyword arguments. Passing
            # kwargs raised TypeError, which the except below swallowed, so every
            # shadow intent was silently dropped and the stage journalled nothing -
            # the third time in this work that a JournalEvent/dict confusion turned
            # a wired path into a dead one.
            self.journal.append_event(
                JournalEvent(
                    event_id=f"shadow-{uuid4().hex}",
                    event_type=SHADOW_EVENT,
                    timestamp=self.clock(),
                    status="SUCCESS",
                    message=result.message,
                    cycle_id=cycle_id,
                    strategy_id=decision.strategy_id,
                    payload={
                        "symbol": decision.symbol,
                        "action": decision.action,
                        "quantity": decision.quantity,
                        "confidence": decision.confidence,
                        "decision_source": decision.decision_source,
                        "rationale": decision.rationale,
                        "guardian_approved": guardian_result.approved,
                        "guardian_reason": guardian_result.reason,
                            "client_order_id": result.client_order_id,
                        "shadowed": True,
                    },
                )
            )
        except Exception as exc:
            # A journal failure must not be able to turn a shadow cycle into a real
            # one, and must not stop the loop. The worst case is an unrecorded
            # shadow intent, which is visible as a gap rather than as a false fill.
            # It is recorded here so the gap is countable instead of invisible.
            self.journal_failures.append(f"{type(exc).__name__}: {exc}")

    def client_order_id(self, decision: TradeDecision, *, cycle_id: str | None) -> str:
        """The same deterministic id the real executor would use.

        Generated here so a shadow order can be matched to a real one later, which is
        what makes "the shadow said BUY, the live run also said BUY" a checkable
        statement rather than an impression.
        """
        raw = f"{decision.symbol}|{decision.action}|{decision.quantity}|{cycle_id or ''}"
        return "shadow-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class ShadowShorts:
    """Real orders for everything except SHORT and COVER, which are shadowed.

    The rollout step for short selling (`AUTOPOIESIS_SHORTS=shadow`): shorts go through the
    same decision path and the same Guardian, and are journalled as intents, while the long
    book keeps trading on paper. Switching to `paper` removes this wrapper and nothing else.
    """

    def __init__(self, *, real, shadow: ShadowExecutor):
        self.real = real
        self.shadow = shadow

    def execute(
        self,
        decision: TradeDecision,
        guardian_result: GuardianResult,
        *,
        cycle_id: str | None = None,
    ) -> ExecutionResult:
        target = self.shadow if decision.action in {"SHORT", "COVER"} else self.real
        return target.execute(decision, guardian_result, cycle_id=cycle_id)

    def __getattr__(self, name):
        # Anything else the loop asks of an executor (client_order_id, ...) is the real one's.
        return getattr(self.real, name)
