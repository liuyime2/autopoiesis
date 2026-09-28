from __future__ import annotations

from datetime import datetime, timezone

from min_agent.models import DataSnapshot, GuardianResult, StrategySpec, TradeDecision


class Guardian:
    def __init__(
        self,
        *,
        allowlist: set[str] | frozenset[str],
        max_position_value: float,
        max_daily_loss: float,
        max_trades_per_day: int = 10,
        max_total_exposure: float | None = None,
        min_confidence: float = 0.5,
        max_snapshot_age_seconds: int = 900,
    ):
        self.allowlist = frozenset(symbol.upper() for symbol in allowlist)
        self.max_position_value = max_position_value
        self.max_daily_loss = max_daily_loss
        self.max_trades_per_day = max_trades_per_day
        self.max_total_exposure = max_total_exposure
        self.min_confidence = min_confidence
        self.max_snapshot_age_seconds = max_snapshot_age_seconds

    def review(
        self,
        decision: TradeDecision,
        snapshot: DataSnapshot,
        mode: str = "paper",
        trades_today: int = 0,
        now: datetime | None = None,
    ) -> GuardianResult:
        if mode.strip().lower() != "paper":
            return GuardianResult(approved=False, reason="only paper mode is allowed")

        if decision.symbol != snapshot.symbol:
            return GuardianResult(approved=False, reason="decision symbol does not match data snapshot")

        if decision.action != "SELL" and decision.symbol not in self.allowlist:
            return GuardianResult(approved=False, reason="symbol is outside the allowlist")

        if decision.action == "SELL" and decision.symbol not in self.allowlist:
            if not any(pos.symbol == decision.symbol for pos in snapshot.positions):
                return GuardianResult(approved=False, reason="symbol is outside the allowlist")

        if self._is_stale(snapshot, now=now):
            return GuardianResult(approved=False, reason="data snapshot is stale")

        if decision.action == "HOLD":
            return GuardianResult(approved=True, reason="approved")

        # Every remaining check gates an actual order. The daily-loss limit used
        # to sit above the HOLD branch, so a deliberate no-op was journalled as
        # a risk REJECTION rather than a hold - misleading in the audit trail,
        # even though the reason is in SYSTEM_REJECTION_REASONS and so was never
        # charged against the strategy.
        if snapshot.account.daily_loss >= self.max_daily_loss:
            return GuardianResult(approved=False, reason="daily loss limit reached")

        if not snapshot.account.day_start_equity_known:
            return GuardianResult(approved=False, reason="account day-start equity unavailable")

        if not snapshot.market_open:
            return GuardianResult(approved=False, reason="market is closed")

        if trades_today >= self.max_trades_per_day:
            return GuardianResult(approved=False, reason="max trades per day reached")

        if decision.confidence < self.min_confidence:
            return GuardianResult(approved=False, reason="decision confidence below minimum")

        position_value = decision.quantity * snapshot.last_price
        if decision.action == "BUY" and position_value > self.max_position_value:
            return GuardianResult(approved=False, reason="position value exceeds hard limit")

        if decision.action == "BUY" and snapshot.account.buying_power < position_value:
            return GuardianResult(approved=False, reason="insufficient buying power")

        if decision.action == "SELL" and not self._has_position(snapshot, decision.symbol, decision.quantity):
            return GuardianResult(approved=False, reason="cannot sell more than known holdings")

        if self._has_conflicting_open_order(snapshot, decision):
            return GuardianResult(approved=False, reason="conflicting open order exists")

        total_exposure = sum(pos.market_value for pos in snapshot.positions)
        if self.max_total_exposure is not None and decision.action == "BUY":
            if total_exposure + position_value > self.max_total_exposure:
                return GuardianResult(approved=False, reason="total exposure exceeds hard limit")

        return GuardianResult(approved=True, reason="approved")

    def review_strategy(self, strategy: StrategySpec) -> GuardianResult:
        if not set(strategy.symbols).issubset(self.allowlist):
            return GuardianResult(approved=False, reason="strategy contains symbols outside the allowlist")
        if strategy.max_position_value > self.max_position_value:
            return GuardianResult(approved=False, reason="strategy position value exceeds hard limit")
        return GuardianResult(approved=True, reason="approved")

    def _is_stale(self, snapshot: DataSnapshot, *, now: datetime | None) -> bool:
        current = now or datetime.now(timezone.utc)
        timestamp = snapshot.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return (current - timestamp).total_seconds() > self.max_snapshot_age_seconds

    def _has_position(self, snapshot: DataSnapshot, symbol: str, quantity: int) -> bool:
        positions = [pos for pos in snapshot.positions if pos.symbol == symbol]
        if not positions:
            return False
        return sum(pos.quantity for pos in positions) >= quantity

    def _has_conflicting_open_order(self, snapshot: DataSnapshot, decision: TradeDecision) -> bool:
        return any(order.symbol == decision.symbol and order.side == decision.action for order in snapshot.open_orders)
