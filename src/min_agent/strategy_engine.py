from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from min_agent.atomicio import file_lock, write_text_atomic
from min_agent.coerce import is_number
from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
from min_agent.models import Action, DataSnapshot, StrategyResult, StrategySpec, TradeDecision


class StrategyLibrary:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.rejected: list[tuple[str, str]] = []
        """(filename, error) for files that failed to parse.

        Previously these were `continue`d in silence, so a file torn by a
        non-atomic write simply made the strategy vanish from the selector for
        that cycle - which surfaced as a phantom HOLD with no diagnosis.
        """

    def save(self, strategy: StrategySpec) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with file_lock(self._lock_path()):
            write_text_atomic(self._path(strategy.strategy_id), strategy.model_dump_json(indent=2) + "\n")

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
        self.rejected = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                strategies.append(StrategySpec.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception as exc:
                self.rejected.append((path.name, f"{type(exc).__name__}: {exc}"))
        return strategies

    def _path(self, strategy_id: str) -> Path:
        return self.directory / f"{strategy_id}.json"

    def _lock_path(self) -> Path:
        return self.directory / ".library.lock"


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
        max_error_rate: float = 0.25,
        max_rejection_rate: float = 0.5,
        severe_failure_rate: float = 0.75,
        #: Scored decisions required before decision quality alone can promote a
        #: strategy. Matches the offline screen's own minimum, so the two cannot
        #: disagree about what counts as evidence.
        min_promotion_scored_decisions: int = 10,
    ):
        self.min_active_cycles = min_active_cycles
        self.max_error_rate = max_error_rate
        self.max_rejection_rate = max_rejection_rate
        self.severe_failure_rate = severe_failure_rate
        self.min_promotion_scored_decisions = min_promotion_scored_decisions

    def review(
        self,
        strategies: list[StrategySpec],
        results: list[StrategyResult],
        evidence: dict[str, object] | None = None,
    ) -> list[StrategyLifecycleDecision]:
        result_by_id = {result.strategy_id: result for result in results}
        evidence_by_id = dict(evidence or {})
        decisions = []
        for strategy in strategies:
            result = result_by_id.get(strategy.strategy_id)
            decision = self._review_one(
                strategy, result, evidence_by_id.get(strategy.strategy_id)
            )
            if decision is not None:
                decisions.append(decision)
        decisions.extend(self._retire_behavioural_duplicates(strategies, result_by_id))
        return decisions

    def _retire_behavioural_duplicates(
        self, strategies: list[StrategySpec], result_by_id: dict[str, StrategyResult]
    ) -> list[StrategyLifecycleDecision]:
        """Retire the weaker copy of any pair that would emit an identical order.

        Nine of the thirteen selectable strategies in the live library were
        FIXED_SIZE/SPY/BUY/qty=1/5000 differing only in confidence, and all nine
        emit a byte-identical order. Admission now refuses such a duplicate, but the
        nine already in the library have to go, and hand-editing the files is
        exactly the bypass this project removed.

        The survivor is chosen by evidence - most cycles, then most submitted
        orders, then oldest - so the copy with the history is the one kept, and the
        decision is journaled with the reason. It is a retirement rather than a
        deletion: the file stays, the lifecycle is terminal, and provenance is
        auditable.
        """
        groups: dict[tuple, list[StrategySpec]] = {}
        for strategy in strategies:
            if strategy.lifecycle in {"PAUSED", "RETIRED", "BASELINE"} or not strategy.enabled:
                continue
            signature = behavioural_signature(strategy)
            if signature is not None:
                groups.setdefault(signature, []).append(strategy)

        def evidence_rank(strategy: StrategySpec) -> tuple:
            """Rank by verifiable evidence, in the order the evidence actually counts.

            The first cut ranked on cycles first and was wrong in a way only real
            data revealed: fixed-size-probe-0002 won with 3 cycles, 0 submitted
            orders and no PnL, over fixed-size-buy-001 which holds +120.37 of
            broker-verified realized PnL. Cycles in a reflection window are an
            artefact of which records happen to be in that window; orders actually
            placed and PnL actually realized are the evidence.
            """
            result = result_by_id.get(strategy.strategy_id)
            # Read the verified PnL once, so "is this copy the evidenced one" and
            # "what did it earn" cannot drift apart the way a `verified` flag plus
            # a second `result.realized_pnl` lookup could.
            verified_pnl = (
                result.realized_pnl
                if result is not None and result.pnl_evidence == PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
                else None
            )
            return (
                1 if verified_pnl is not None else 0,
                verified_pnl if verified_pnl is not None else 0.0,
                result.submitted_orders if result is not None else 0,
                result.filled_quantity if result is not None else 0.0,
                result.cycles if result is not None else 0,
                # Oldest first, so the longest-lived copy survives a full tie.
                -strategy.created_at.timestamp(),
            )
        decisions: list[StrategyLifecycleDecision] = []
        for signature, members in groups.items():
            if len(members) < 2:
                continue
            ordered = sorted(members, key=evidence_rank, reverse=True)
            keeper, duplicates = ordered[0], ordered[1:]
            for duplicate in duplicates:
                keeper_cycles = result_by_id.get(keeper.strategy_id)
                duplicate_cycles = result_by_id.get(duplicate.strategy_id)
                detail = (
                    f"; kept {keeper.strategy_id} which has "
                    f"{keeper_cycles.cycles if keeper_cycles is not None else 0} cycle(s) "
                    f"against this one's "
                    f"{duplicate_cycles.cycles if duplicate_cycles is not None else 0}"
                    if keeper_cycles is not None and duplicate_cycles is not None
                    else f"; kept {keeper.strategy_id}"
                )
                decisions.append(
                    StrategyLifecycleDecision(
                        duplicate,
                        "RETIRED",
                        f"behavioural duplicate of {keeper.strategy_id}: identical kind, "
                        f"symbols, action, quantity and max_position_value{detail}",
                    )
                )
        return decisions

    def _review_one(
        self,
        strategy: StrategySpec,
        result: StrategyResult | None,
        evidence: object | None = None,
    ) -> StrategyLifecycleDecision | None:
        if strategy.lifecycle in {"BASELINE", "PAUSED", "RETIRED"}:
            return None
        if result is None:
            return None

        # Broker-verified PnL stands on its own, so the PnL rule is evaluated
        # before the cycle-count gate. A strategy can open a lot in one window and
        # have it closed in a later one, leaving zero cycles in the reflection
        # window; requiring cycles > 0 first meant the only PnL-driven transition
        # in the system was unreachable for exactly the strategies that had real
        # PnL to report. Every other rule below is cycle-based and keeps its gate.
        if (
            result.pnl_evidence == PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
            and result.realized_pnl is not None
            and result.realized_pnl < 0
        ):
            return StrategyLifecycleDecision(strategy, "RETIRED", "broker-verified negative realized PnL")

        if result.cycles <= 0:
            return None

        # Charge the strategy only for rejections that are its own fault. Using
        # rejected_orders here is what retired strategies for obeying the daily
        # trade limit, after which auto-fix.sh force-re-enabled them outside the
        # admission gate. A result that does not carry the split is read as
        # "all rejections are the strategy's fault", the conservative direction.
        fault_rejections = (
            result.strategy_fault_rejections
            if result.strategy_fault_rejections is not None
            else result.rejected_orders
        )
        failure_rate = (result.errors + fault_rejections) / result.cycles
        rejection_rate = fault_rejections / result.cycles
        if failure_rate >= self.severe_failure_rate:
            return StrategyLifecycleDecision(strategy, "RETIRED", f"severe operational failure rate {failure_rate:.2f}")
        if result.errors / result.cycles > self.max_error_rate:
            return StrategyLifecycleDecision(strategy, "PAUSED", "error rate above lifecycle threshold")
        if rejection_rate > self.max_rejection_rate:
            return StrategyLifecycleDecision(strategy, "PAUSED", "Guardian rejection rate above lifecycle threshold")
        if strategy.lifecycle == "PROBATION" and result.cycles >= self.min_active_cycles:
            if self._is_degenerate_no_exploration(strategy, result):
                return StrategyLifecycleDecision(strategy, "PAUSED", "probation produced no exploration evidence")
            # PnL has to be able to *promote* as well as retire, or it is only a
            # veto and the lifecycle is not evidence-driven in the direction that
            # matters. Probation is still earned by cycles - a single lucky trade
            # does not shorten it - but once the cycle bar is met, broker-verified
            # realized PnL is what decides the outcome, and the journal records
            # which. Without a closed lot there is no PnL to judge on, so
            # promotion falls back to the operational metrics rather than blocking
            # a strategy that may simply have nothing to close.
            if (
                result.pnl_evidence == PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
                and result.realized_pnl is not None
            ):
                return StrategyLifecycleDecision(
                    strategy,
                    "ACTIVE",
                    (
                        f"probation completed on {result.cycles} cycles with "
                        f"broker-verified realized PnL {result.realized_pnl:+.2f}"
                    ),
                )
            # Phase 6: promotion must rest on verifiable evidence of having *acted*
            # and of the outcome being *known*.
            #
            # The fallback this replaces returned ACTIVE with the reason "probation
            # completed with acceptable operational metrics" - which means only that
            # the strategy did not error and was not rejected much. A strategy that
            # existed, never submitted an order, never opened a lot and therefore
            # has no PnL of any kind was being promoted to ACTIVE on the grounds
            # that it had not crashed. The objective requires every promotion to rest
            # on verifiable evidence rather than on the absence of trouble, and a
            # strategy with no orders has no evidence at all.
            #
            # The remaining path is decision-quality evidence: the counterfactual
            # ledger's scored verdicts for the decisions this strategy actually
            # produced. That is a real bar rather than an infinite probation - a
            # long-only strategy may never close a lot, but its decisions are still
            # scored against the market.
            if result.submitted_orders <= 0:
                return None
            gate = self._promotion_evidence_gate(evidence)
            if gate is not None:
                return None
            return StrategyLifecycleDecision(
                strategy,
                "ACTIVE",
                (
                    f"probation completed on {result.cycles} cycles with "
                    f"{result.submitted_orders} submitted order(s) and decision "
                    "quality that cleared the evidence gate"
                ),
            )
        # A promoted strategy that has stopped acting is just as stuck as one
        # that never started. This used to be checked only during PROBATION.
        if self._is_degenerate_no_exploration(strategy, result):
            return StrategyLifecycleDecision(strategy, "PAUSED", "no exploration evidence over the evaluation window")
        return None

    def _promotion_evidence_gate(self, evidence: object | None) -> str | None:
        """Why this strategy may not be promoted, or None if it may.

        Returns a *reason* rather than a boolean so the lifecycle journal can say
        what is missing. A gate that can only say no leaves an operator - or a later
        reader of the journal - with a strategy stuck in probation and no account of
        why.

        Deliberately strict about the absent case. No evidence at all is treated as
        "not yet", never as "assumed fine": this is the direction that is safe, and
        it is the direction a previous version got wrong by defaulting to promotion.
        """
        if evidence is None:
            return "no offline validation evidence has been computed for it yet"
        verdict = getattr(evidence, "verdict", None)
        scored = getattr(evidence, "scored", 0) or 0

        # Volume is checked *before* the verdict. A PASS_SCREENED verdict carrying
        # three scored decisions promoted a strategy on the first version of this
        # gate, because the verdict short-circuited the count. The screen does not
        # issue such a verdict - it requires ten - so the two were believed to agree.
        # A gate that depends on an upstream invariant nobody documented is one
        # refactor away from promoting on three data points.
        if scored < self.min_promotion_scored_decisions:
            return (
                f"only {scored} scored decision(s), below the "
                f"{self.min_promotion_scored_decisions} needed to judge"
            )
        if verdict == "PASS_SCREENED":
            return None
        if verdict == "REJECT_POOR_DECISIONS":
            return "offline validation rejected its recorded decisions"
        return (
            f"offline validation verdict is {verdict!r}, which is not a pass; "
            "promotion requires either broker-verified PnL from a closed lot or "
            "decision quality that cleared the evidence gate"
        )

    def _is_degenerate_no_exploration(self, strategy: StrategySpec, result: StrategyResult) -> bool:
        """A tradable strategy that never tried to trade is not working.

        Previously guarded by `if strategy.kind != "FIXED_SIZE": return False`,
        which exempts every TREND_FOLLOW strategy, and by a
        `lifecycle == "PROBATION"` requirement that is permanently false once
        the strategy is promoted. The strategy holding the top score in the
        recorded run was a promoted TREND_FOLLOW, so both guards missed it.
        """
        if strategy.kind == "HOLD_BASELINE" or strategy.lifecycle == "BASELINE":
            return False
        if result is None:
            return False
        return result.cycles >= self.min_active_cycles and result.trade_attempts == 0


class StrategySelector:
    def __init__(
        self,
        *,
        min_probation_cycles: int = 3,
        exploration_floor_cycles: int = 5,
    ):
        self.min_probation_cycles = min_probation_cycles
        self.exploration_floor_cycles = exploration_floor_cycles

    def select(
        self,
        strategies: list[StrategySpec],
        results: list[StrategyResult] | None = None,
        last_price: float | None = None,
    ) -> StrategySpec | None:
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
        # A candidate that cannot trade at this price would spend the cycle on a HOLD.
        #
        # The queue is oldest-first, so it is the one place in this method that is blind
        # to whether a strategy can act: argmax over scores naturally avoids a strategy
        # that has never traded, while the queue serves whatever has been waiting
        # longest. Measured on the live library, 8 of the 13 probation candidates could
        # not have fired anywhere in the recent price range (759.37-772.39) because
        # their trigger band contains it, so those cycles could produce nothing at any
        # queue depth.
        #
        # That is also why raising `min_probation_cycles` was measured and rejected: at a
        # budget of 13 the candidates reaching the ten-scored gate fell from 3 to 2 and
        # the informative decisions produced by probation candidates fell from 62.8 to
        # 35.8, because the extra cycles went to strategies that cannot use them.
        #
        # `_declared_actions` is the same predicate the admission gate uses to refuse a
        # TREND_FOLLOW anchored so close to spot that its band swallows the price, so the
        # two agree by construction rather than by two implementations agreeing. It is
        # price-dependent and reversible: when the market leaves the band the strategy
        # becomes selectable again. Nothing is retired here.
        actionable = [
            strategy for strategy in probation if self._declared_actions(strategy, last_price)
        ]
        if actionable:
            # Fair rotation, but a strategy that supplies a capability the library
            # otherwise lacks is not an interchangeable peer. Curriculum builds a
            # SELL strategy precisely because the library could not sell, and then
            # a strict oldest-first queue defers exercising it behind five
            # near-duplicate BUY strategies - three probation cycles each, about
            # 105 minutes at the shipped 5-minute interval. A capability the
            # library is otherwise missing has no other route to being tried, so it
            # is served first, and only then does the queue return to oldest-first.
            unexercised = self._uncovered_capabilities(tradable, last_price)
            if unexercised:
                for strategy in sorted(actionable, key=lambda s: (s.created_at, s.strategy_id)):
                    if self._supplies_capability(strategy, unexercised, last_price):
                        return strategy
            return sorted(actionable, key=lambda strategy: (strategy.created_at, strategy.strategy_id))[0]

        candidates = tradable or eligible
        # Same reasoning as the probation queue, applied to the path it falls through
        # to. Without this, skipping an untradeable probation queue buys nothing: the
        # argmax below has no score to separate the candidates yet, `max` returns the
        # first of equals, and the untradeable strategy is served anyway.
        #
        # Falls back to the unfiltered list when nothing can act, because an empty
        # candidate set would raise rather than trade. Skipping must never become
        # stopping.
        actionable_candidates = [
            strategy for strategy in candidates if self._declared_actions(strategy, last_price)
        ]
        if actionable_candidates:
            candidates = actionable_candidates
        scores = {result.strategy_id: result.score for result in results or []}
        # A candidate counts as explored only if it has a history that shows it
        # tried. Having no result at all is unevaluated, not explored - otherwise
        # a brand-new strategy suppresses the floor and the argmax falls back to
        # a stale score.
        explored = [
            s for s in candidates
            if (result := result_by_id.get(s.strategy_id)) is not None and result.trade_attempts > 0
        ]
        if not explored and self._exploration_floor_engaged(result_by_id):
            probe = self._probe_strategy(candidates)
            if probe is not None:
                return probe
        return max(candidates, key=lambda strategy: scores.get(strategy.strategy_id, 0.0))

    def _exploration_floor_engaged(self, result_by_id: dict[str, StrategyResult]) -> bool:
        """Only take over once there is a real window to have learned from.

        With no history at all the agent has no basis for scoring anything, and
        waiting for a score it can never earn is how the previous version
        latched. Once several full windows have passed with zero attempts
        anywhere, a bounded probe is the correct research move.
        """
        results = list(result_by_id.values())
        if not results:
            return False
        return any(r.cycles >= self.exploration_floor_cycles for r in results)

    def _probe_strategy(self, candidates: list[StrategySpec]) -> StrategySpec | None:
        """Smallest, most predictable BUY, so the cost of evidence is bounded.

        Preference order: an explicit FIXED_SIZE buy, then anything else that
        buys. Never a SELL: there is no position evidence to sell, and an
        unhedged short is not a probe. A TREND_FOLLOW spec has no `action` key,
        so its direction is implied by price versus reference_price and may be a
        SELL - it is only eligible when its parameters say so explicitly.
        """
        buys = [s for s in candidates if _is_buy_strategy(s)]
        if not buys:
            return None
        return min(buys, key=lambda s: (0 if s.kind == "FIXED_SIZE" else 1, _probe_quantity(s), s.created_at, s.strategy_id))

    @staticmethod
    def _declared_actions(strategy: StrategySpec, last_price: float | None = None) -> set[str]:
        """The actions a strategy can express.

        With a real price, coverage is what the strategy would actually emit right
        now. A TREND_FOLLOW derives its side from price, so it covers both actions
        only in the abstract: the live one is anchored to a June reference of 735.01
        and SPY trades at 767, so it emits BUY and would keep emitting BUY while
        price stays above 727.6. Counting it as an exit is what let the library
        report SELL as covered while being unable to sell - the same
        nominal-versus-executable error already fixed in the curriculum.
        """
        if strategy.kind == "HOLD_BASELINE":
            return set()
        if strategy.kind == "TREND_FOLLOW":
            if last_price is None:
                return {"BUY", "SELL"}
            produced = _produced_action(strategy, last_price)
            return {produced} if produced else set()
        action = str(strategy.parameters.get("action", "")).upper()
        return {action} if action in {"BUY", "SELL"} else set()

    @classmethod
    def _uncovered_capabilities(
        cls, strategies: list[StrategySpec], last_price: float | None = None
    ) -> set[str]:
        """Actions the whole library expresses exactly once, or not at all.

        A capability with several routes has other chances to be tried. One with a
        single route - or none - does not, and that is the strategy the queue was
        about to keep deferring.
        """
        counts: dict[str, int] = {"BUY": 0, "SELL": 0}
        for strategy in strategies:
            for action in cls._declared_actions(strategy, last_price):
                counts[action] = counts.get(action, 0) + 1
        return {action for action, count in counts.items() if count <= 1}

    @classmethod
    def _supplies_capability(
        cls, strategy: StrategySpec, uncovered: set[str], last_price: float | None = None
    ) -> bool:
        return bool(cls._declared_actions(strategy, last_price) & uncovered)

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
        # `TradeDecision.action` is a Literal, so pydantic rejects an action this
        # strategy never declared. Validating here instead would duplicate that
        # rule in a second place; the cast only records that the check exists.
        action = cast("Action", str(strategy.parameters["action"]).upper())
        quantity = int(strategy.parameters["quantity"]) if action != "HOLD" else 0
        if action == "BUY":
            # The cap bounds new exposure, so it applies to entries only. Applying
            # it to a SELL meant a strategy whose max_position_value was below the
            # share price could never exit: the cap zeroed the quantity and the
            # exit was silently rewritten to HOLD, and a larger exit was silently
            # truncated. A limit on opening risk must never be a reason to stay in
            # the position. Guardian still bounds the SELL by real holdings.
            max_quantity = int(strategy.max_position_value // snapshot.last_price)
            quantity = max(0, min(quantity, max_quantity))
            if quantity <= 0:
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
        action: Action
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


def _is_buy_strategy(strategy: StrategySpec) -> bool:
    action = str(strategy.parameters.get("action", "")).upper()
    if action:
        return action == "BUY"
    # A TREND_FOLLOW spec carries no `action`. Its direction is implied by price
    # versus reference_price, so it is only an explicit buy if it says so.
    return str(strategy.parameters.get("direction", "")).upper() == "BUY"


def _probe_quantity(strategy: StrategySpec) -> int:
    for key in ("quantity", "shares"):
        value = strategy.parameters.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(1, int(value))
    return 1


def _produced_action(strategy: StrategySpec, last_price: float) -> str | None:
    """The action this strategy would emit at `last_price`, or None if it holds.

    Only the strategy's own sizing and threshold logic is consulted. Guardian is
    not simulated: it stays authoritative at execution time, and the selector is
    deciding what to exercise, not what would be approved.
    """
    from min_agent.models import AccountSnapshot, DataSnapshot

    try:
        symbol = strategy.symbols[0]
        snapshot = DataSnapshot(
            symbol=symbol,
            timestamp=datetime.now(tz=timezone.utc),
            market_open=True,
            last_price=last_price,
            source="selector_capability_probe",
            account=AccountSnapshot(
                equity=0.0, cash=0.0, buying_power=0.0, portfolio_value=0.0, daily_loss=0.0
            ),
        )
        decision = StrategyExecutor().decide(strategy, snapshot)
    except Exception:
        return None
    return decision.action if decision.action in {"BUY", "SELL"} else None


def behavioural_signature(strategy: StrategySpec) -> tuple | None:
    """What a strategy would actually do, or None if it cannot be reduced to one.

    Two strategies with the same signature emit the same order on the same
    snapshot, so keeping both is a zoo rather than an ensemble. `name` and
    `confidence` are excluded: the first is cosmetic and the second is an admission
    gate that every admitted strategy has already passed, and neither changes the
    action or the quantity.

    TREND_FOLLOW used to return None, on the grounds that its side is derived from
    the price at decision time and so could not be compared without a snapshot. The
    consequence was that *no* TREND_FOLLOW was ever compared to *any* other one: 12
    of them sat in the registry and the zoo check was blind to all of it. Three were
    exact duplicates - `trend-follow-20260611-001/-002/-003` shared
    reference_price 100.0, threshold 0.02 and quantity 1, and `-004/-005` shared
    150.0 and 0.01.

    The side being dynamic does not make two strategies uncomparable, because the
    *rule* is fully determined by the declared parameters: the same reference, the
    same threshold and the same quantity produce the same action on every snapshot.
    So the signature is built from those, and `confidence` is still excluded for the
    same reason as above - it is an admission gate, not a behaviour.
    """
    if strategy.kind == "HOLD_BASELINE":
        return ("HOLD_BASELINE", tuple(sorted(strategy.symbols)))
    if strategy.kind == "TREND_FOLLOW":
        reference = strategy.parameters.get("reference_price")
        threshold = strategy.parameters.get("threshold_pct")
        quantity = strategy.parameters.get("quantity")
        if not (is_number(reference) and is_number(threshold) and is_number(quantity)):
            return None
        return (
            strategy.kind,
            tuple(sorted(strategy.symbols)),
            round(float(reference), 6),
            round(float(threshold), 6),
            int(quantity),
            round(float(strategy.max_position_value), 2),
        )
    action = str(strategy.parameters.get("action", "")).upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        return None
    return (
        strategy.kind,
        tuple(sorted(strategy.symbols)),
        action,
        int(strategy.parameters.get("quantity", 0) or 0),
        round(float(strategy.max_position_value), 2),
    )
