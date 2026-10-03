from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from min_agent.guardian import Guardian
from min_agent.models import StrategySpec
from min_agent.strategy_engine import StrategyLibrary, behavioural_signature


@dataclass(frozen=True)
class StrategyAdmissionResult:
    accepted: bool
    reason: str
    #: Always the id of the spec that was screened. It was ``str | None = None``
    #: because a rejection "has" no strategy, but `admit` takes a `StrategySpec`
    #: whose id is required, so every verdict names the candidate it judged - and
    #: callers that then need to look the candidate up (the offline screen) would
    #: otherwise have to guard against a state that cannot occur.
    strategy_id: str


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

    def _reference_price_rejection_reason(self, strategy: StrategySpec) -> str | None:
        """Reject a TREND_FOLLOW that cannot trade, in either direction.

        A TREND_FOLLOW emits BUY above `ref*(1+threshold)` and SELL below
        `ref*(1-threshold)`, and HOLD everywhere between. Two ways of anchoring
        therefore make it permanently degenerate, and both were admitted here:

        1. **Far from the market.** The curriculum prompt used to contain a literal
           example `{"reference_price": 100.0}`. The model copied it, and nine
           strategies were admitted with reference_price in {100, 120, 150, 170,
           180, 200} against a SPY that traded between 723.21 and 756.55 in the
           same journal. Price was always far above the reference, so every one of
           them could only ever emit the same action. Refused by the deviation
           check below.

        2. **At the market.** The same prompt told the model to set
           `reference_price` to the real last price, "copied exactly". That puts
           the trigger band around the present, so the strategy is permanently
           HOLD - it emits no action at all, which is the same degeneracy facing
           the other way. 26 strategies were admitted this way, anchored between
           762 and 770 and differing only in the second decimal place. Measured
           against the last five recorded trading days (SPY 759.37-772.39), 25 of
           the 38 TREND_FOLLOW on disk could not emit a single order in that
           window and none could fire in both directions.

        Case 2 is what the second check is for. It cannot be caught by the first:
        `threshold_pct` is capped at 0.20, so a reference that swallows the current
        price is never more than 20% away and never trips a 50% deviation.

        The anchor must be a real market price. Without a price on hand the checks
        are skipped rather than guessed - this is a quality gate, not a safety one.
        """
        if strategy.kind != "TREND_FOLLOW":
            return None
        reference = strategy.parameters.get("reference_price")
        if not isinstance(reference, (int, float)) or isinstance(reference, bool) or reference <= 0:
            return None
        threshold = strategy.parameters.get("threshold_pct")
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
            if (
                isinstance(threshold, (int, float))
                and not isinstance(threshold, bool)
                and threshold > 0
            ):
                low = reference * (1 - threshold)
                high = reference * (1 + threshold)
                if low <= price <= high:
                    return (
                        f"reference_price {reference} puts the trigger band "
                        f"[{low:.2f}, {high:.2f}] around the real {symbol} price "
                        f"{price}, so the strategy can only ever HOLD. Anchor it "
                        f"outside that band - below {low:.2f} or above {high:.2f} - "
                        f"so it can express a view."
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
        twin = self._behavioural_duplicate(strategy, strategies)
        if twin is not None:
            return (
                f"behaviourally identical to existing strategy {twin!r}: same kind, "
                f"symbols, action, quantity and max_position_value. Propose a strategy "
                f"that differs in one of those, or explain what problem it solves."
            )
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

    def _behavioural_duplicate(self, strategy: StrategySpec, strategies: list[StrategySpec]) -> str | None:
        """The id of a strategy this one would behave identically to, if any.

        `confidence` and `name` are deliberately excluded. `confidence` is only an
        admission gate - Guardian refuses anything under min_confidence, so every
        admitted strategy is already past it - and it is copied straight into the
        TradeDecision without altering the action or the quantity. Nine strategies
        in the live library were FIXED_SIZE/SPY/BUY/qty=1/5000 differing only in
        confidence 0.6-0.95, and all nine emit a byte-identical order. The library
        had 13 selectable strategies of which 9 were the same strategy, because the
        only duplicate rules were id-equality and TREND_FOLLOW parameter equality.
        """
        mine = behavioural_signature(strategy)
        if mine is None:
            return None
        for existing in strategies:
            if existing.strategy_id == strategy.strategy_id:
                continue
            if behavioural_signature(existing) == mine:
                return existing.strategy_id
        return None

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
