from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from autopoiesis.fill_reconciler import FILL_EVENT
from autopoiesis.models import CycleRecord, JournalEvent, TradeDecision


class TradingLoop:
    # An order submitted this recently, with no fill confirmation, is treated as still in
    # flight. Long enough to cover a lost broker response and a restart; short enough that a
    # genuinely stuck order is reconciled rather than silently blocking trading.
    IN_FLIGHT_WINDOW_MINUTES = 30

    def __init__(
        self,
        *,
        data_gateway,
        decision_engine,
        guardian,
        executor,
        journal,
        mode: str,
        trade_counter=None,
        now: Callable[[], datetime] | None = None,
        manage_account: bool = False,
    ):
        self.data_gateway = data_gateway
        self.decision_engine = decision_engine
        self.guardian = guardian
        self.executor = executor
        #: Failures reading the agent's own holdings. A SELL is refused while this
        #: is non-empty, so a broken journal cannot silently widen what may be sold.
        self._agent_holding_errors: list[str] = []
        self.journal = journal
        self.mode = mode
        self.trade_counter = trade_counter
        #: What time the Guardian should judge snapshot freshness against.
        #:
        #: `None` keeps live behaviour exactly: the Guardian falls back to
        #: `datetime.now`. It exists because the staleness check compares the snapshot's
        #: timestamp against the current time, so a replay over real bars from last week
        #: was refused on all 847 cycles as "data snapshot is stale" - correctly, since
        #: those bars really are stale. Pointing this at the replayed session's own clock
        #: runs the *same* check against the correct time base rather than weakening it,
        #: which is the difference between testing the real risk path and testing a
        #: system that nobody ships.
        self.now = now
        #: With the account holder's consent (`MIN_AGENT_MANAGE_ACCOUNT=true`) a SELL is
        #: bounded by the account's position rather than by the agent's own fills.
        self.manage_account = manage_account

    def _identical_order_already_in_flight(self, decision: TradeDecision) -> str | None:
        """A client_order_id of the client_order_id of a submission we never saw fill.

        `AlpacaPaperExecutor.client_order_id` hashes the cycle id, and every cycle gets a
        fresh uuid4 - so a retry produces a different id and the broker cannot deduplicate it.
        The only backstop was Guardian's check against *resting broker* orders, which catches
        an order the broker still holds open and misses the common case: the order filled
        immediately and the response was lost.

        So this reads the journal for a submission with the same symbol, action, quantity and
        strategy that no ORDER_FILL_CONFIRMED event has since matched. That is exactly the
        state where re-submitting doubles the position. Returning the reason lets the caller
        record a skipped cycle rather than silently doing less than it appears to.

        Scoped to a window: an order submitted hours ago with no fill confirmation is a
        different problem - reconciliation, not duplication - and must not block trading
        forever.
        """
        if self.journal is None or decision.action == "HOLD":
            return None
        window = timedelta(minutes=self.IN_FLIGHT_WINDOW_MINUTES)
        now = datetime.now(timezone.utc)
        confirmed: set[str] = set()
        submitted: list[tuple[datetime, str, str]] = []
        try:
            for event in self.journal.read_events():
                if event.event_type == FILL_EVENT:
                    coid = event.payload.get("client_order_id")
                    if isinstance(coid, str):
                        confirmed.add(coid)
                continue
            for record in self.journal.last_n(200):
                execution = record.execution
                if execution.status != "SUBMITTED":
                    continue
                other = record.decision
                if (
                    other.action == decision.action
                    and other.symbol == decision.symbol
                    and float(other.quantity) == float(decision.quantity)
                    and (other.strategy_id or "") == (decision.strategy_id or "")
                ):
                    submitted.append((
                        record.snapshot.timestamp,
                        execution.client_order_id or "",
                        record.cycle_id,
                    ))
        except Exception as exc:
            # Unavailable journal means the guard cannot vouch either way. Skipping is the
            # safe direction: a missed order is recoverable, a doubled position is not.
            return f"journal unavailable, refusing to re-submit without an in-flight check: {exc}"

        for when, coid, cycle in submitted:
            if coid and coid in confirmed:
                continue
            if now - when > window:
                continue
            return (
                f"an identical order was submitted in cycle {cycle} at {when.isoformat()} "
                f"({coid or 'no client_order_id'}) and no fill confirmation has been "
                f"recorded since; re-submitting would double the position"
            )
        return None

    def _sellable_holding(self, symbol: str, snapshot) -> float | None:
        """What a SELL of `symbol` may reach: the account's position when the agent manages
        the whole account, otherwise only what the agent itself bought."""
        if self.manage_account:
            return float(sum(p.quantity for p in snapshot.positions if p.symbol == symbol and p.quantity > 0))
        return self._agent_holding(symbol)

    def _agent_holding(self, symbol: str) -> float | None:
        """Shares of `symbol` the agent itself bought and has not sold.

        Computed from ORDER_FILL_CONFIRMED events, so it reflects only what this
        agent did. `None` when the journal is unavailable, which makes the Guardian
        refuse a SELL rather than fall back to the account's figure - the safe
        direction, since a missed exit is recoverable and selling the owner's shares
        is not.

        The events are replayed in timestamp order and clamped at zero on the way.
        Summing the SELLs as one total was wrong in a way that only surfaced once the
        agent actually held shares: this journal contains a SELL of 52 SPY preceded by
        23 agent BUY fills - 29 of those shares belonging to the account owner - and
        followed by 10 further agent BUYs. A commutative sum gives 33 - 52 = -19 and
        reports the agent as holding nothing, so the Guardian refused every exit and
        the agent could never close a position it demonstrably owned, against a
        broker holding of 10. Replaying in order gives 23 - 52 + 10 = 10, floored to
        0, which is the real figure and matches the broker exactly.

        Flooring is what makes the oversell visible instead of netted away. The 29
        owner shares were liquidated by an order this agent submitted, but no
        arithmetic should hand the agent a credit for them; the clamp keeps the
        holding at what the agent can legitimately sell and leaves the historical
        29-share discrepancy documented rather than absorbed into a balance.
        """
        if self.journal is None:
            return None
        try:
            events = [
                event
                for event in self.journal.read_events("ORDER_FILL_CONFIRMED")
                if (event.payload or {}).get("symbol") == symbol
                and (event.payload or {}).get("filled_quantity") is not None
            ]
            # read_events does not promise chronological order, and the replay above
            # depends on it: an out-of-order SELL would deduct against shares that
            # had not been bought yet.
            events.sort(key=lambda event: event.timestamp)
            quantity = 0.0
            for event in events:
                payload = event.payload or {}
                filled = float(payload["filled_quantity"])
                side = str(payload.get("side", "")).upper()
                if side == "BUY":
                    quantity += filled
                elif side == "SELL":
                    quantity -= filled
                quantity = max(0.0, quantity)
        except Exception as exc:
            # An unreadable journal means the holding is genuinely unknown, and
            # unknown is exactly the case the Guardian refuses. Recorded rather than
            # swallowed, because a silent `None` here looks identical to a
            # legitimate "the agent holds nothing".
            self._agent_holding_errors.append(f"{type(exc).__name__}: {exc}")
            return None
        return quantity if quantity > 1e-9 else 0.0

    def _agent_short(self, symbol: str) -> float | None:
        """Shares of `symbol` the agent itself has shorted and not covered.

        Replayed from ORDER_FILL_CONFIRMED events whose side is the decision's own SHORT or
        COVER - kept apart from the long book, so a cover can never be read as a buy of the
        owner's position or a sell as a short. Clamped at zero in order, like the long side.
        None when the journal cannot be read: the Guardian then refuses a COVER.
        """
        read = getattr(self.journal, "read_events", None)
        if read is None:
            return None
        try:
            events = sorted(
                (
                    e for e in read("ORDER_FILL_CONFIRMED")
                    if (e.payload or {}).get("symbol") == symbol
                    and (e.payload or {}).get("filled_quantity") is not None
                ),
                key=lambda e: e.timestamp,
            )
            short = 0.0
            for event in events:
                payload = event.payload or {}
                side = str(payload.get("side", "")).upper()
                filled = float(payload["filled_quantity"])
                if side == "SHORT":
                    short += filled
                elif side == "COVER":
                    short = max(0.0, short - filled)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._agent_holding_errors.append(f"{type(exc).__name__}: {exc}")
            return None
        return short if short > 1e-9 else 0.0

    def run_once(self, symbol: str) -> CycleRecord | None:
        cycle_id = str(uuid4())
        try:
            snapshot = self.data_gateway.snapshot(symbol)
        except Exception as exc:
            # A cycle with no broker snapshot cannot produce a CycleRecord, and
            # inventing one would be fabricated data (AGENTS.md 2). Journal the
            # failure as an event instead so the evaluator, reflector and
            # curriculum can see that it happened at all.
            self._journal_cycle_failure(cycle_id, symbol, exc)
            return None
        errors: list[str] = []
        try:
            if hasattr(self.decision_engine, "decide_snapshot"):
                decision = self.decision_engine.decide_snapshot(snapshot)
            else:
                decision = self.decision_engine.decide(self._decision_context(snapshot))
        except Exception as exc:
            errors.append(f"decision_error: {exc}")
            decision = TradeDecision(
                symbol=snapshot.symbol,
                action="HOLD",
                quantity=0,
                confidence=0.0,
                rationale="fail-closed HOLD after invalid decision engine output",
            )
        trades_today = 0
        if self.trade_counter is not None:
            try:
                trades_today = self.trade_counter.trades_today()
            except Exception as exc:
                errors.append(f"trade_counter_error: {exc}")
                decision = TradeDecision(
                    symbol=snapshot.symbol,
                    action="HOLD",
                    quantity=0,
                    confidence=0.0,
                    rationale="fail-closed HOLD after trade counter error",
                    strategy_id=decision.strategy_id,
                )
        guardian_result = self.guardian.review(
            decision,
            snapshot,
            mode=self.mode,
            now=self.now() if self.now else None,
            trades_today=trades_today,
            # What the agent itself holds, so a SELL cannot reach the account
            # owner's pre-existing shares. Derived from the journal's own confirmed
            # fills rather than from the account snapshot, because the account
            # snapshot is exactly the number that let 29 shares of someone else's
            # position be sold.
            agent_position_quantity=self._sellable_holding(decision.symbol, snapshot),
            # And what it is short, from the same fills, for the same reason on the other side.
            agent_short_quantity=self._agent_short(decision.symbol),
        )
        # Refuse to re-submit an order this agent already submitted and has not seen fill.
        # A lost broker response looks exactly like a decision that was never acted on, and
        # the two produce opposite results: one wasted cycle, or one doubled position.
        in_flight = self._identical_order_already_in_flight(decision)
        if in_flight is not None:
            decision = decision.model_copy(update={
                "action": "HOLD",
                "quantity": 0,
                "rationale": f"skip: {in_flight}",
            })
            guardian_result = self.guardian.review(
                decision, snapshot, mode=self.mode,
                now=self.now() if self.now else None,
                trades_today=trades_today,
                agent_position_quantity=self._sellable_holding(decision.symbol, snapshot),
                agent_short_quantity=self._agent_short(decision.symbol),
            )
            # Deliberately NOT appended to `errors`.
            #
            # `record.error` feeds `StrategyResult.errors`, which is the numerator of the
            # lifecycle's failure rate: >= 0.75 retires a strategy as a "severe operational
            # failure" and > 0.25 pauses it for "error rate above lifecycle threshold".
            # Charging the guard for firing therefore retires the strategies that trade
            # most, because a strategy that wants to trade while its order is pending is
            # precisely a strategy that trades. Measured on this journal, every FIXED_SIZE
            # strategy carrying errors had `in_flight_order` as the only error text -
            # fixed-size-sell-005 (3), fixed-size-probe-0001 (3), fixed-size-sell-006 (4) -
            # and all three are PAUSED or RETIRED, while fixed-size-buy-001 (21 informative
            # decisions) and tiny-fixed-size-001, the two with no errors at all, are the only
            # ones that survived. The cycle this guard produces submits nothing, takes no
            # risk and ends in a fail-safe HOLD: it is the guard working, not a failure.
            #
            # Nothing is lost by dropping it. The skip stays on the cycle twice over: the
            # decision's own rationale carries the specific reason, and the executor
            # returns SKIPPED, which the evaluator counts as a skipped order.
            #
            # This is the same shape as `SYSTEM_REJECTION_REASONS` on the Guardian's
            # rejection channel, where a refusal the system issued correctly stopped
            # counting against the strategy. The error channel never got that split.

        execution = self.executor.execute(decision, guardian_result, cycle_id=cycle_id)
        record = CycleRecord(
            cycle_id=cycle_id,
            snapshot=snapshot,
            decision=decision,
            guardian=guardian_result,
            execution=execution,
            strategy_id=decision.strategy_id,
            error="; ".join(errors) or None,
        )
        self.journal.append(record)
        return record

    def _journal_cycle_failure(self, cycle_id: str, symbol: str, exc: Exception) -> None:
        event = JournalEvent(
            event_id=str(uuid4()),
            event_type="CYCLE_FAILED",
            timestamp=datetime.now(tz=timezone.utc),
            status="FAILED",
            message=f"{type(exc).__name__}: {exc}",
            cycle_id=cycle_id,
            payload={
                "symbol": symbol.upper(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "paper_only": True,
            },
        )
        try:
            self.journal.append_event(event)
        except Exception:
            pass

    def _decision_context(self, snapshot) -> dict:
        return {
            "symbol": snapshot.symbol,
            "timestamp": snapshot.timestamp.isoformat(),
            "market_open": snapshot.market_open,
            "last_price": snapshot.last_price,
            "equity": snapshot.account.equity,
            "cash": snapshot.account.cash,
            "buying_power": snapshot.account.buying_power,
            "daily_loss": snapshot.account.daily_loss,
            "positions": [
                {"symbol": pos.symbol, "quantity": pos.quantity, "market_value": pos.market_value}
                for pos in snapshot.positions
            ],
            "open_orders": [
                {
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "status": order.status,
                }
                for order in snapshot.open_orders
            ],
        }
