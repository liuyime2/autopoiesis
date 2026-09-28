from __future__ import annotations

from collections.abc import Mapping
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
    def __init__(
        self,
        *,
        guardian: Guardian,
        strategy_library: StrategyLibrary,
        market_prices: Mapping[str, float] | None = None,
        max_reference_price_deviation: float = 0.5,
    ):
        self.guardian = guardian
        self.strategy_library = strategy_library
        self.market_prices = dict(market_prices or {})
        self.max_reference_price_deviation = max_reference_price_deviation

    def set_market_prices(self, prices: Mapping[str, float]) -> None:
        self.market_prices = dict(prices)

    def admit(self, strategy: StrategySpec) -> StrategyAdmissionResult:
        if self.strategy_library.exists(strategy.strategy_id):
            return StrategyAdmissionResult(
                accepted=False,
                reason="strategy_id already exists; duplicate admission rejected",
                strategy_id=strategy.strategy_id,
            )

        reference_reason = self._reference_price_rejection_reason(strategy)
        if reference_reason is not None:
            return StrategyAdmissionResult(
                accepted=False,
                reason=reference_reason,
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

    def _reference_price_rejection_reason(self, strategy: StrategySpec) -> str | None:
        """Reject a TREND_FOLLOW anchored to a price the market never had.

        The curriculum prompt used to contain a literal example
        `{"reference_price": 100.0}`. The model copied it, and nine strategies
        were admitted with reference_price in {100, 120, 150, 170, 180, 200}
        against a SPY that traded between 723.21 and 756.55 in the same journal.
        Price was therefore always far above the reference, so every one of them
        was permanently degenerate and could only ever emit the same action.

        The anchor must be a real market price. Without a price on hand the check
        is skipped rather than guessed - it is a quality gate, not a safety one.
        """
        if strategy.kind != "TREND_FOLLOW":
            return None
        reference = strategy.parameters.get("reference_price")
        if not isinstance(reference, (int, float)) or isinstance(reference, bool) or reference <= 0:
            return None
        for symbol in strategy.symbols:
            price = self.market_prices.get(symbol.upper())
            if price is None or price <= 0:
                continue
            deviation = abs(reference - price) / price
            if deviation > self.max_reference_price_deviation:
                return (
                    f"reference_price {reference} is {deviation:.0%} from the real "
                    f"{symbol} price {price}; anchor it to the market, not to an example"
                )
        return None

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
