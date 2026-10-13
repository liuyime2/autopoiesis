from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from autopoiesis.guardian import Guardian
from autopoiesis.models import StrategyResult, StrategySpec
from autopoiesis.strategy_engine import (
    PROBATION_CYCLES,
    StrategyLibrary,
    StrategySelector,
    behavioural_signature,
)


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


#: Retirement reasons the current evidence still supports, whatever is proposed afterwards.
#:
#: `broker-verified negative realized PnL` is a statement about money that has already
#: happened, broker-confirmed, so nothing about the present makes it untrue. A strategy
#: retired for it stays retired, and allowing otherwise would turn this path into a way to
#: re-run a strategy the account has already been charged for losing on.
#:
#: Deliberately not here: "severe operational failure rate N", which is what retired
#: `fixed-size-bil-sell-001` on 2026-10-13. That reason is a rate over refusals, and the
#: staleness fix removed an entire class of refusal that was being charged into it. A
#: recorded reason is only as durable as the attribution behind it.
NON_EXPIRING_RETIREMENT_REASONS = ("broker-verified negative realized PnL",)

#: How many times a retired strategy may be put back on PROBATION through this gate.
#:
#: One. Re-admission is meant to undo a retirement that turned out to be wrong; a second one
#: means the first was not an error but a disagreement with the gate, which is what the
#: screen and the promotion path are for. Unbounded, this becomes a retire-and-reinstate loop
#: in which the lifecycle events themselves become the learning evidence.
MAX_READMISSIONS = 1


class StrategyAdmission:
    def __init__(
        self,
        *,
        guardian: Guardian,
        strategy_library: StrategyLibrary,
        market_prices: Mapping[str, float] | None = None,
        max_reference_price_deviation: float = 0.5,
        probation_selector: StrategySelector | None = None,
        max_probation_queue: int = PROBATION_CYCLES,
    ):
        self.guardian = guardian
        self.strategy_library = strategy_library
        self.market_prices = dict(market_prices or {})
        self.max_reference_price_deviation = max_reference_price_deviation
        self.probation_selector = probation_selector or StrategySelector()
        self.max_probation_queue = max_probation_queue

    def set_market_prices(self, prices: Mapping[str, float]) -> None:
        self.market_prices = dict(prices)

    def probation_backlog(self, results: Sequence[StrategyResult] | None = None) -> int:
        """How many strategies are in probation, short of budget, AND servable.

        This is the population the queue actually has to serve, and it is not the
        count of `PROBATION` files: a candidate that has served its
        `PROBATION_CYCLES` leaves the queue even while its lifecycle still says
        PROBATION, so counting files would hold the backlog at every candidate ever
        admitted and the gate could never reopen.

        Nor is it every candidate short of budget. `StrategySelector` filters
        non-actionable strategies *before* serving them, so a candidate that cannot
        emit an order at the current price is never served and can never spend the 13
        cycles it is holding. Measured on the live library: 14 candidates are in
        probation and short of budget, of which **6 cannot act** at SPY 769.72 -
        TREND_FOLLOW strategies whose trigger band brackets the market, all admitted
        2026-10-02, the day before `26c341d` began refusing that shape. They are a
        standing claim on 78 cycles of budget that will never be spent, and since this
        number gates admission, they also help refuse new candidates:

            24  17 candidate(s) are in probation and still short of the 13-c[ycle]
             4  13 candidate(s) are in probation and still short of the 13-c[ycle]

        They are not retired here, and deliberately: `_declared_actions` is
        price-dependent and reversible, so a market move out of the band makes a
        candidate servable again, and throwing it away would destroy something a
        later regime can use.

        `_declared_actions` is called rather than reimplemented, because that predicate
        is already shared with the selector and with `_reference_price_rejection_reason`;
        three rules agreeing by construction beats three implementations agreeing by
        review. With no market price on hand every candidate is counted, matching the
        rule already used in this class: the checks are skipped rather than guessed,
        because refusing admission on missing data would be a worse failure than
        counting too many.
        """
        by_id = {result.strategy_id: result for result in results or []}
        price = next(iter(self.market_prices.values()), None)
        selector = self.probation_selector
        return sum(
            1
            for strategy in self.strategy_library.list()
            if selector._needs_probation(strategy, by_id.get(strategy.strategy_id))
            and (price is None or selector._declared_actions(strategy, price))
        )

    def dormant_probation(self, results: Sequence[StrategyResult] | None = None) -> int:
        """Probation candidates short of budget that cannot act at the current price.

        Reported alongside the backlog so the capacity message can name both figures.
        Without it an operator comparing "14 candidates in probation" against a backlog
        of 8 has no way to tell whether the difference is a bug or the dormancy above.
        """
        by_id = {result.strategy_id: result for result in results or []}
        price = next(iter(self.market_prices.values()), None)
        if price is None:
            return 0
        selector = self.probation_selector
        return sum(
            1
            for strategy in self.strategy_library.list()
            if selector._needs_probation(strategy, by_id.get(strategy.strategy_id))
            and not selector._declared_actions(strategy, price)
        )

    def _capacity_rejection_reason(
        self, backlog: int, dormant: int = 0, total: int | None = None
    ) -> str | None:
        """Refuse a candidate the loop has no cycles to evaluate.

        Every other refusal here is candidate-intrinsic: is this id a duplicate, is
        this price anchored sensibly, does this progression make sense, is it a
        behavioural twin. None of them asks whether the loop can *use* the result,
        and the measured answer was that it cannot. Over the whole record 716
        market-open cycles could serve about 51 candidates and 68 were admitted, so
        the queue grew by 17 - including 7 admitted across a weekend, when there is
        no market and therefore no capacity at all.

        The cost is not merely a longer queue. `StrategySelector.select` serves the
        probation queue first and only considers the incumbent otherwise, so a
        candidate that cannot be served is a claim on the cycles the one ACTIVE
        strategy needs. That strategy has been selected 0 times in the 146 cycles
        since its promotion.

        The cap is in candidates rather than in cycles-per-day so it does not encode
        this machine's interval into what is otherwise a capacity rule.
        """
        if backlog < self.max_probation_queue:
            return None
        # Both figures, because "N candidates are in probation" and a backlog smaller than
        # N is otherwise unexplainable from the outside. The dormant count is the
        # difference: candidates short of budget that the selector will not serve at this
        # price, and which therefore release their budget when the market leaves the band.
        held = (
            f" {total - backlog} further candidate(s) in probation cannot act at the "
            f"current price and are not counted, so they are not waiting for cycles."
            if dormant and total is not None
            else ""
        )
        return (
            f"{backlog} candidate(s) are in probation, still short of the "
            f"{self.probation_selector.min_probation_cycles}-cycle budget and able to "
            f"act at the current price, which is the cap; admitting another cannot be "
            f"evaluated and would further delay the strategies already waiting. Let the "
            f"queue drain first.{held}"
        )

    def admit(
        self, strategy: StrategySpec, results: Sequence[StrategyResult] | None = None
    ) -> StrategyAdmissionResult:
        # An id the library already holds is only a duplicate if the strategy behind it is
        # still in play. A RETIRED one is not: it was removed on evidence, and if that evidence
        # turns out to have been wrong there is no other way back through this gate, because
        # a different id cannot take a behavioural slot a retired strategy still occupies.
        #
        # Measured 2026-10-13. `fixed-size-bil-sell-001` was retired with
        # "severe operational failure rate 1.00", charged for one cycle in which Guardian
        # correctly refused a 1546-second-old quote - a refusal the market caused and the
        # strategy could not have influenced. The staleness attribution behind that is fixed.
        # At 14:55:54 EDT the curriculum proposed this exact strategy id, rediscovering the
        # need for a BIL exit on its own, and this branch refused it as
        # "strategy_id already exists; duplicate admission rejected". The gate was blocking
        # the system's own repair of a mistake the gate had caused.
        retired: StrategySpec | None = None
        if self.strategy_library.exists(strategy.strategy_id):
            # A file that exists but will not parse counts as held: overwriting it would
            # destroy the record of whatever is wrong with it.
            existing = self.strategy_library.try_load(strategy.strategy_id)
            if existing is None or existing.lifecycle != "RETIRED":
                return StrategyAdmissionResult(
                    accepted=False,
                    reason="strategy_id already exists; duplicate admission rejected",
                    strategy_id=strategy.strategy_id,
                )
            blocked = self._readmission_refusal(existing)
            if blocked is not None:
                return StrategyAdmissionResult(
                    accepted=False, reason=blocked, strategy_id=strategy.strategy_id,
                )
            retired = existing

        backlog = self.probation_backlog(results)
        dormant = self.dormant_probation(results)
        capacity_reason = self._capacity_rejection_reason(
            backlog, dormant=dormant, total=backlog + dormant
        )
        if capacity_reason is not None:
            return StrategyAdmissionResult(
                accepted=False,
                reason=capacity_reason,
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
        updates: dict[str, object] = {"lifecycle": lifecycle}
        if retired is not None:
            # Both reasons, because the file is the only place a later reader learns why a
            # retired id is serving again. Overwriting `lifecycle_reason` with the new
            # verdict would erase the mistake the re-admission is correcting.
            updates["lifecycle_reason"] = (
                f"re-admitted after retirement: {retired.lifecycle_reason}; {review.reason}"
            )
            updates["readmissions"] = retired.readmissions + 1
        else:
            updates["lifecycle_reason"] = review.reason
        self.strategy_library.save(strategy.model_copy(update=updates))
        return StrategyAdmissionResult(
            accepted=True,
            reason=(
                f"re-admitted after retirement: {retired.lifecycle_reason}"
                if retired is not None else "approved"
            ),
            strategy_id=strategy.strategy_id,
        )

    def _readmission_refusal(self, existing: StrategySpec) -> str | None:
        """Why this retired strategy may not be put back, or None if it may.

        A retirement the evidence still supports must not be reversible by proposing the
        strategy again, and a re-admission that can happen twice is a loop rather than a
        repair.
        """
        if existing.readmissions >= MAX_READMISSIONS:
            return (
                f"re-admission refused: already re-admitted {existing.readmissions} time(s) "
                f"and the limit is {MAX_READMISSIONS}; this is a disagreement with the gate, "
                f"not an error in it"
            )
        for reason in NON_EXPIRING_RETIREMENT_REASONS:
            if existing.lifecycle_reason.startswith(reason):
                return (
                    f"re-admission refused: retired for '{reason}', which the evidence still "
                    f"supports; nothing about the present makes it untrue"
                )
        return None

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
