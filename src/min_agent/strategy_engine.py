from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
from min_agent.models import DataSnapshot, StrategyResult, StrategySpec, TradeDecision


class StrategyLibrary:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def save(self, strategy: StrategySpec) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(strategy.strategy_id)
        path.write_text(strategy.model_dump_json(indent=2), encoding="utf-8")

    def exists(self, strategy_id: str) -> bool:
        return self._path(strategy_id).exists()

    def try_load(self, strategy_id: str) -> StrategySpec | None:
        path = self._path(strategy_id)
        if not path.exists():
            return None
        return StrategySpec.model_validate_json(path.read_text(encoding="utf-8"))

    def load(self, strategy_id: str) -> StrategySpec:
        return StrategySpec.model_validate_json(self._path(strategy_id).read_text(encoding="utf-8"))

    def list(self) -> list[StrategySpec]:
        if not self.directory.exists():
            return []
        strategies = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                strategies.append(StrategySpec.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return strategies

    def _path(self, strategy_id: str) -> Path:
        return self.directory / f"{strategy_id}.json"


@dataclass(frozen=True)
class StrategyLifecycleDecision:
    strategy: StrategySpec
    new_lifecycle: str
    reason: str


class StrategyLifecycleManager:
    def __init__(
        self,
        *,
        min_active_cycles: int = 5,
        min_active_submitted_orders: int = 1,
        max_error_rate: float = 0.25,
        max_rejection_rate: float = 0.5,
        severe_failure_rate: float = 0.75,
    ):
        self.min_active_cycles = min_active_cycles
        self.min_active_submitted_orders = min_active_submitted_orders
        self.max_error_rate = max_error_rate
        self.max_rejection_rate = max_rejection_rate
        self.severe_failure_rate = severe_failure_rate

    def review(self, strategies: list[StrategySpec], results: list[StrategyResult]) -> list[StrategyLifecycleDecision]:
        result_by_id = {result.strategy_id: result for result in results}
        decisions = []
        for strategy in strategies:
            result = result_by_id.get(strategy.strategy_id)
            decision = self._review_one(strategy, result)
            if decision is not None:
                decisions.append(decision)
        return decisions

    def _review_one(self, strategy: StrategySpec, result: StrategyResult | None) -> StrategyLifecycleDecision | None:
        if strategy.lifecycle in {"BASELINE", "PAUSED", "RETIRED"}:
            return None
        if result is None or result.cycles <= 0:
            return None

        failure_rate = (result.errors + result.rejected_orders) / result.cycles
        rejection_rate = result.rejected_orders / result.cycles
        if failure_rate >= self.severe_failure_rate:
            return StrategyLifecycleDecision(strategy, "RETIRED", f"severe operational failure rate {failure_rate:.2f}")
        if result.pnl_evidence == PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED and result.realized_pnl is not None and result.realized_pnl < 0:
            return StrategyLifecycleDecision(strategy, "RETIRED", "broker-verified negative realized PnL")
        if result.errors / result.cycles > self.max_error_rate:
            return StrategyLifecycleDecision(strategy, "PAUSED", "error rate above lifecycle threshold")
        if rejection_rate > self.max_rejection_rate:
            return StrategyLifecycleDecision(strategy, "PAUSED", "Guardian rejection rate above lifecycle threshold")
        if strategy.lifecycle == "PROBATION" and result.cycles >= self.min_active_cycles:
            if self._is_degenerate_no_exploration(strategy, result):
                return StrategyLifecycleDecision(strategy, "PAUSED", "probation produced no exploration evidence")
            return StrategyLifecycleDecision(strategy, "ACTIVE", "probation completed with acceptable operational metrics")
        return None

    def _is_degenerate_no_exploration(self, strategy: StrategySpec, result: StrategyResult) -> bool:
        if strategy.kind != "FIXED_SIZE":
            return False
        action = str(strategy.parameters["action"]).upper()
        quantity = int(strategy.parameters["quantity"])
        return (
            strategy.lifecycle == "PROBATION"
            and (action == "HOLD" or quantity == 0)
            and result.action_counts.get("BUY", 0) == 0
            and result.action_counts.get("SELL", 0) == 0
            and result.submitted_orders == 0
            and result.rejected_orders == 0
            and result.intended_notional == 0
        )


class StrategySelector:
    def __init__(self, *, min_probation_cycles: int = 3, min_probation_submitted_orders: int = 1):
        self.min_probation_cycles = min_probation_cycles
        self.min_probation_submitted_orders = min_probation_submitted_orders

    def select(self, strategies: list[StrategySpec], results: list[StrategyResult] | None = None) -> StrategySpec | None:
        eligible = [
            strategy
            for strategy in strategies
            if strategy.enabled and strategy.lifecycle not in {"PAUSED", "RETIRED"}
        ]
        if not eligible:
            return None

        result_by_id = {result.strategy_id: result for result in results or []}
        tradable = [strategy for strategy in eligible if strategy.kind != "HOLD_BASELINE" and strategy.lifecycle != "BASELINE"]
        probation = [strategy for strategy in tradable if self._needs_probation(strategy, result_by_id.get(strategy.strategy_id))]
        if probation:
            return sorted(probation, key=lambda strategy: (strategy.created_at, strategy.strategy_id))[0]

        candidates = tradable or eligible
        scores = {result.strategy_id: result.score for result in results or []}
        return max(candidates, key=lambda strategy: scores.get(strategy.strategy_id, 0.0))

    def _needs_probation(self, strategy: StrategySpec, result: StrategyResult | None) -> bool:
        if strategy.lifecycle != "PROBATION":
            return False
        if result is None:
            return True
        return result.cycles < self.min_probation_cycles


class StrategyExecutor:
    def decide(self, strategy: StrategySpec, snapshot: DataSnapshot) -> TradeDecision:
        if snapshot.symbol not in strategy.symbols:
            return self._hold(snapshot, strategy, "snapshot symbol is outside strategy universe")

        if strategy.kind == "HOLD_BASELINE":
            return self._hold(snapshot, strategy, "hold baseline")
        if strategy.kind == "FIXED_SIZE":
            return self._fixed_size(strategy, snapshot)
        if strategy.kind == "TREND_FOLLOW":
            return self._trend_follow(strategy, snapshot)
        return self._hold(snapshot, strategy, "unsupported strategy kind")

    def _fixed_size(self, strategy: StrategySpec, snapshot: DataSnapshot) -> TradeDecision:
        action = str(strategy.parameters["action"]).upper()
        quantity = int(strategy.parameters["quantity"]) if action != "HOLD" else 0
        max_quantity = int(strategy.max_position_value // snapshot.last_price)
        quantity = max(0, min(quantity, max_quantity))
        if action != "HOLD" and quantity <= 0:
            return self._hold(snapshot, strategy, "position size is zero after risk cap")
        return TradeDecision(
            symbol=snapshot.symbol,
            action=action,
            quantity=quantity,
            confidence=float(strategy.parameters["confidence"]),
            rationale=f"strategy:{strategy.strategy_id} fixed size",
            strategy_id=strategy.strategy_id,
        )

    def _trend_follow(self, strategy: StrategySpec, snapshot: DataSnapshot) -> TradeDecision:
        reference_price = float(strategy.parameters["reference_price"])
        threshold_pct = float(strategy.parameters["threshold_pct"])
        quantity = int(strategy.parameters["quantity"])
        max_quantity = int(strategy.max_position_value // snapshot.last_price)
        quantity = max(0, min(quantity, max_quantity))

        if quantity <= 0:
            return self._hold(snapshot, strategy, "position size is zero after risk cap")
        if snapshot.last_price >= reference_price * (1 + threshold_pct):
            action = "BUY"
        elif snapshot.last_price <= reference_price * (1 - threshold_pct):
            action = "SELL"
        else:
            action = "HOLD"
            quantity = 0

        return TradeDecision(
            symbol=snapshot.symbol,
            action=action,
            quantity=quantity,
            confidence=float(strategy.parameters["confidence"]),
            rationale=f"strategy:{strategy.strategy_id} trend follow vs {reference_price}",
            strategy_id=strategy.strategy_id,
        )

    def _hold(self, snapshot: DataSnapshot, strategy: StrategySpec, reason: str) -> TradeDecision:
        return TradeDecision(
            symbol=snapshot.symbol,
            action="HOLD",
            quantity=0,
            confidence=0.0,
            rationale=f"strategy:{strategy.strategy_id} {reason}",
            strategy_id=strategy.strategy_id,
        )
