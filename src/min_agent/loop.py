from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from min_agent.models import CycleRecord, JournalEvent, TradeDecision


class TradingLoop:
    def __init__(self, *, data_gateway, decision_engine, guardian, executor, journal, mode: str, trade_counter=None):
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

    def _agent_holding(self, symbol: str) -> float | None:
        """Shares of `symbol` the agent itself bought and has not sold.

        Computed from ORDER_FILL_CONFIRMED events, so it reflects only what this
        agent did. `None` when the journal is unavailable, which makes the Guardian
        refuse a SELL rather than fall back to the account's figure - the safe
        direction, since a missed exit is recoverable and selling the owner's shares
        is not.
        """
        if self.journal is None:
            return None
        try:
            quantity = 0.0
            for event in self.journal.read_events("ORDER_FILL_CONFIRMED"):
                payload = event.payload or {}
                if payload.get("symbol") != symbol:
                    continue
                filled = payload.get("filled_quantity")
                if filled is None:
                    continue
                side = str(payload.get("side", "")).upper()
                if side == "BUY":
                    quantity += float(filled)
                elif side == "SELL":
                    quantity -= float(filled)
        except Exception as exc:
            # An unreadable journal means the holding is genuinely unknown, and
            # unknown is exactly the case the Guardian refuses. Recorded rather than
            # swallowed, because a silent `None` here looks identical to a
            # legitimate "the agent holds nothing".
            self._agent_holding_errors.append(f"{type(exc).__name__}: {exc}")
            return None
        return quantity if quantity > 1e-9 else 0.0

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
            trades_today=trades_today,
            # What the agent itself holds, so a SELL cannot reach the account
            # owner's pre-existing shares. Derived from the journal's own confirmed
            # fills rather than from the account snapshot, because the account
            # snapshot is exactly the number that let 29 shares of someone else's
            # position be sold.
            agent_position_quantity=self._agent_holding(decision.symbol),
        )
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
