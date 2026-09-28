from __future__ import annotations

from uuid import uuid4

from min_agent.models import CycleRecord, TradeDecision


class TradingLoop:
    def __init__(self, *, data_gateway, decision_engine, guardian, executor, journal, mode: str, trade_counter=None):
        self.data_gateway = data_gateway
        self.decision_engine = decision_engine
        self.guardian = guardian
        self.executor = executor
        self.journal = journal
        self.mode = mode
        self.trade_counter = trade_counter

    def run_once(self, symbol: str) -> CycleRecord:
        cycle_id = str(uuid4())
        snapshot = self.data_gateway.snapshot(symbol)
        error = None
        try:
            if hasattr(self.decision_engine, "decide_snapshot"):
                decision = self.decision_engine.decide_snapshot(snapshot)
            else:
                decision = self.decision_engine.decide(self._decision_context(snapshot))
        except Exception as exc:
            error = f"decision_error: {exc}"
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
                error = f"trade_counter_error: {exc}"
                decision = TradeDecision(
                    symbol=snapshot.symbol,
                    action="HOLD",
                    quantity=0,
                    confidence=0.0,
                    rationale="fail-closed HOLD after trade counter error",
                    strategy_id=decision.strategy_id,
                )
        guardian_result = self.guardian.review(decision, snapshot, mode=self.mode, trades_today=trades_today)
        execution = self.executor.execute(decision, guardian_result, cycle_id=cycle_id)
        record = CycleRecord(
            cycle_id=cycle_id,
            snapshot=snapshot,
            decision=decision,
            guardian=guardian_result,
            execution=execution,
            strategy_id=decision.strategy_id,
            error=error,
        )
        self.journal.append(record)
        return record

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
