from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from min_agent.atomicio import file_lock, write_text_atomic
from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
from min_agent.models import DataSnapshot, StrategyResult, StrategySpec, TradeDecision


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
            return StrategyLifecycleDecision(strategy, "ACTIVE", "probation completed with acceptable operational metrics")
        # A promoted strategy that has stopped acting is just as stuck as one
        # that never started. This used to be checked only during PROBATION.
        if self._is_degenerate_no_exploration(strategy, result):
            return StrategyLifecycleDecision(strategy, "PAUSED", "no exploration evidence over the evaluation window")
        return None

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
        min_probation_submitted_orders: int = 1,
        exploration_floor_cycles: int = 5,
    ):
        self.min_probation_cycles = min_probation_cycles
        self.min_probation_submitted_orders = min_probation_submitted_orders
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
        if probation:
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
                for strategy in sorted(probation, key=lambda s: (s.created_at, s.strategy_id)):
                    if self._supplies_capability(strategy, unexercised, last_price):
                        return strategy
            return sorted(probation, key=lambda strategy: (strategy.created_at, strategy.strategy_id))[0]

        candidates = tradable or eligible
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
        action = str(strategy.parameters["action"]).upper()
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
