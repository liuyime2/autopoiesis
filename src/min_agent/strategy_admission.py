from __future__ import annotations

from dataclasses import dataclass

from min_agent.guardian import Guardian
from min_agent.models import StrategySpec
from min_agent.strategy_engine import StrategyLibrary


@dataclass(frozen=True)
class StrategyAdmissionResult:
    accepted: bool
    reason: str
    strategy_id: str | None = None


class StrategyAdmission:
    def __init__(self, *, guardian: Guardian, strategy_library: StrategyLibrary):
        self.guardian = guardian
        self.strategy_library = strategy_library

    def admit(self, strategy: StrategySpec) -> StrategyAdmissionResult:
        if self.strategy_library.exists(strategy.strategy_id):
            return StrategyAdmissionResult(
                accepted=False,
                reason="strategy_id already exists; duplicate admission rejected",
                strategy_id=strategy.strategy_id,
            )

        progression_reason = self._progression_rejection_reason(strategy)
        if progression_reason is not None:
            return StrategyAdmissionResult(
                accepted=False,
                reason=progression_reason,
                strategy_id=strategy.strategy_id,
            )

        review = self.guardian.review_strategy(strategy)
        if not review.approved:
            return StrategyAdmissionResult(
                accepted=False,
                reason=review.reason,
                strategy_id=strategy.strategy_id,
            )

        lifecycle = "BASELINE" if strategy.kind == "HOLD_BASELINE" else "PROBATION"
        self.strategy_library.save(strategy.model_copy(update={"lifecycle": lifecycle}))
        return StrategyAdmissionResult(
            accepted=True,
            reason="approved",
            strategy_id=strategy.strategy_id,
        )

    def _has_kind(self, kind: str) -> bool:
        return any(strategy.kind == kind for strategy in self.strategy_library.list())

    def _progression_rejection_reason(self, strategy: StrategySpec) -> str | None:
        strategies = self.strategy_library.list()
        if not strategies:
            if strategy.kind != "HOLD_BASELINE":
                return "curriculum progression requires HOLD_BASELINE before trading skills"
            return None

        has_baseline = any(item.kind == "HOLD_BASELINE" for item in strategies)
        has_fixed_size = any(item.kind == "FIXED_SIZE" for item in strategies)
        has_trading_skill = any(item.kind != "HOLD_BASELINE" for item in strategies)
        if not has_baseline and strategy.kind != "HOLD_BASELINE":
            return "curriculum progression requires HOLD_BASELINE before trading skills"
        if not has_trading_skill:
            if strategy.kind == "HOLD_BASELINE":
                return None
            if strategy.kind != "FIXED_SIZE":
                return "first trading skill must be a tiny FIXED_SIZE strategy"
            if strategy.max_position_value > self.guardian.max_position_value:
                return "strategy max_position_value exceeds Guardian hard limit"
            if not self._is_small_fixed_size(strategy):
                return "first FIXED_SIZE skill must use tiny exposure with quantity <= 1"
            return None
        if strategy.kind == "TREND_FOLLOW" and not has_fixed_size:
            return "TREND_FOLLOW requires an admitted FIXED_SIZE skill first"
        if strategy.kind == "TREND_FOLLOW" and self._duplicates_trend_follow(strategy, strategies):
            return "duplicate TREND_FOLLOW parameters rejected; propose distinct exploration parameters"
        return None

    def _is_small_fixed_size(self, strategy: StrategySpec) -> bool:
        action = str(strategy.parameters["action"]).upper()
        quantity = int(strategy.parameters["quantity"])
        return (
            strategy.kind == "FIXED_SIZE"
            and action == "BUY"
            and quantity == 1
            and strategy.max_position_value <= self.guardian.max_position_value
        )

    def _duplicates_trend_follow(self, strategy: StrategySpec, strategies: list[StrategySpec]) -> bool:
        for existing in strategies:
            if existing.kind != "TREND_FOLLOW":
                continue
            if existing.symbols != strategy.symbols:
                continue
            if (
                existing.parameters["reference_price"] == strategy.parameters["reference_price"]
                and existing.parameters["threshold_pct"] == strategy.parameters["threshold_pct"]
                and existing.parameters["quantity"] == strategy.parameters["quantity"]
            ):
                return True
        return False
