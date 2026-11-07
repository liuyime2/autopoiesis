"""Four-arm experiment on the production-faithful replay (research only).

Arms:

  A: core sleeve only - the HOLD baseline.
  B: core sleeve + fixed SMA rule.
  C: core sleeve + non-LLM search rule.
  D: core sleeve + LLM weekly proposals. Not run until a real weekly engine
     exists; an arm that reuses C's rule is not D and must not be reported as one.

The replay is ``autopoiesis.replay``: ``ReplayBook``, ``ReplayDataGateway`` and
``ReplayExecutor``, with the real ``Guardian``. A decision made from bar ``t``
fills at ``t+1`` open, and the final bar cannot fill. This is deliberately the
same path the repository already uses for its production-faithful replays;
there is no second account or clock here.

Costs are explicit. A replay profit is not a profit, and a replay fill is not
trading evidence.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from autopoiesis.guardian import Guardian
from autopoiesis.models import DataSnapshot, TradeDecision
from autopoiesis.replay import ReplayBar, ReplayBook, ReplayDataGateway, ReplayExecutor
from autopoiesis.risk_judgment import ewma_series
from autopoiesis.sleeve import SLEEVE_POOL_CAP, SLEEVE_STRATEGY_ID, CoreSleeve

from .statistics import daily_returns, paired_block_bootstrap


@dataclass(frozen=True)
class RiskLimits:
    """The paper account's risk mandate, passed to the real Guardian."""

    allowlist: frozenset[str] = frozenset({"SPY"})
    max_position_value: float = 25_000.0
    max_daily_loss: float = 500.0
    max_total_exposure: float = 90_000.0
    max_trades_per_day: int = 100


@dataclass(frozen=True)
class CostModel:
    """Explicit costs. The default is zero so a structural replay stays unchanged."""

    slippage_pct: float = 0.0
    commission_pct: float = 0.0


@dataclass
class ArmOutcome:
    arm: str
    seed: int = 0
    bars: int = 0
    equity_curve: list[float] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)
    final_equity: float = 0.0
    max_drawdown: float = 0.0
    max_gross_exposure: float = 0.0
    min_cash: float = 0.0


@dataclass
class ExperimentReport:
    arms: dict[str, list[ArmOutcome]] = field(default_factory=dict)
    benchmarks: dict[str, ArmOutcome] = field(default_factory=dict)
    controls: dict[str, ArmOutcome] = field(default_factory=dict)
    comparisons: list[dict] = field(default_factory=list)
    benchmark_comparisons: list[dict] = field(default_factory=list)
    conclusion: str = "INSUFFICIENT"
    notes: list[str] = field(default_factory=list)


#: A decision engine is a callable that sees one snapshot and returns a decision.
Engine = Callable[[DataSnapshot], TradeDecision]


@dataclass
class SmaEngine:
    """A deterministic rule engine. It never sees a future bar.

    ``slow == 0`` selects the fixed-rule form (price versus one SMA); a non-zero
    ``slow`` selects the crossover form. The engine keeps only prices it has
    already been shown, so look-ahead would require a caller to pass a snapshot
    built from a future bar.
    """

    fast: int
    slow: int = 0
    band: float = 0.0
    quantity: int = 1
    confidence: float = 0.6
    strategy_id: str = "sma"
    _prices: list[float] = field(default_factory=list, init=False, repr=False)

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        self._prices.append(snapshot.last_price)
        need = max(self.fast, self.slow)
        if len(self._prices) < need:
            return self._hold(snapshot, f"not enough history: {len(self._prices)}/{need}")
        fast = sum(self._prices[-self.fast:]) / self.fast
        if self.slow:
            slow = sum(self._prices[-self.slow:]) / self.slow
            if fast > slow and snapshot.last_price > fast:
                return self._decide(
                    snapshot,
                    "BUY",
                    f"SMA{self.fast} {fast:.2f} above SMA{self.slow} {slow:.2f} and price above fast",
                )
            if fast < slow and snapshot.last_price < fast:
                return self._decide(
                    snapshot,
                    "SELL",
                    f"SMA{self.fast} {fast:.2f} below SMA{self.slow} {slow:.2f} and price below fast",
                )
            return self._hold(snapshot, "crossover not aligned")
        if snapshot.last_price > fast * (1 + self.band):
            return self._decide(
                snapshot,
                "BUY",
                f"price {snapshot.last_price:.2f} above SMA{self.fast} {fast:.2f} by more than {self.band:.2%}",
            )
        if snapshot.last_price < fast * (1 - self.band):
            return self._decide(
                snapshot,
                "SELL",
                f"price {snapshot.last_price:.2f} below SMA{self.fast} {fast:.2f} by more than {self.band:.2%}",
            )
        return self._hold(snapshot, "inside the band")

    def _decide(
        self, snapshot: DataSnapshot, action: Literal["BUY", "SELL"], why: str
    ) -> TradeDecision:
        return TradeDecision(
            symbol=snapshot.symbol,
            action=action,
            quantity=self.quantity,
            confidence=self.confidence,
            rationale=why,
            strategy_id=self.strategy_id,
        )

    def _hold(self, snapshot: DataSnapshot, why: str) -> TradeDecision:
        return TradeDecision(
            symbol=snapshot.symbol,
            action="HOLD",
            quantity=0,
            confidence=0.0,
            rationale=why,
            strategy_id=self.strategy_id,
        )


@dataclass
class HoldEngine:
    strategy_id: str = "hold"

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        return TradeDecision(
            symbol=snapshot.symbol,
            action="HOLD",
            quantity=0,
            confidence=0.0,
            rationale="arm A holds by construction",
            strategy_id=self.strategy_id,
        )


@dataclass
class SleeveEngine:
    """Arm A: the production core sleeve, sized by a point-in-time volatility forecast.

    The sleeve is reused rather than reimplemented. Its only injected dependency is
    the risk block: the replay supplies an EWMA forecast computed from closes the
    engine has already seen, so a day is never sized with a later day's volatility.
    The live path uses `RiskJudgment`; that path reads a VIX cache whose last point
    is today's, which would leak the future into every historical day.
    """

    pool: float = 20_000.0
    target_vol_pct: float = 10.0
    band: float = 0.10
    symbol: str = "SPY"
    min_closes: int = 30
    _closes: list[float] = field(default_factory=list, init=False, repr=False)
    _vol: float | None = field(default=None, init=False, repr=False)
    _sleeve: CoreSleeve = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 0 < self.pool <= SLEEVE_POOL_CAP:
            raise ValueError(f"sleeve pool must be in (0, {SLEEVE_POOL_CAP}], got {self.pool}")
        if self.target_vol_pct <= 0:
            raise ValueError("sleeve target volatility must be positive")
        self._sleeve = CoreSleeve(
            symbol=self.symbol,
            pool=self.pool,
            target_vol_pct=self.target_vol_pct,
            risk_block=self._risk_block,
            band=self.band,
        )

    def _risk_block(self, symbol: str) -> dict | None:
        del symbol
        if self._vol is None:
            return None
        # UNVERIFIED is usable for the sleeve and is the honest status for a
        # forecast whose quality has not been graded on this replay.
        return {"status": "UNVERIFIED", "forecast_vol_pct": self._vol}

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        self._closes.append(snapshot.last_price)
        series = ewma_series(self._closes)
        self._vol = series[-1] if series and len(self._closes) >= self.min_closes else None
        decision = self._sleeve.plan(snapshot)
        if decision is not None:
            return decision
        return TradeDecision(
            symbol=snapshot.symbol,
            action="HOLD",
            quantity=0,
            confidence=0.0,
            rationale="core sleeve: inside the band, or the volatility forecast is not ready",
            strategy_id=SLEEVE_STRATEGY_ID,
        )


@dataclass
class BuyAndHoldEngine:
    """Benchmark: buy SPY once at the same position limit, then hold.

    Without this row a positive return reads as skill. The benchmark uses the same
    Guardian and the same `max_position_value`, so the comparison is risk-budgeted
    rather than a full-capital buy-and-hold that the mandate does not permit.
    """

    max_position_value: float = 25_000.0
    quantity_cap: int | None = None
    strategy_id: str = "buy-and-hold-spy"
    _bought: bool = field(default=False, init=False)

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        held = sum(
            position.quantity
            for position in snapshot.positions
            if position.symbol.upper() == snapshot.symbol.upper()
        )
        if held > 0 or self._bought:
            return TradeDecision(
                symbol=snapshot.symbol,
                action="HOLD",
                quantity=0,
                confidence=0.0,
                rationale="benchmark already invested",
                strategy_id=self.strategy_id,
            )
        budget = min(self.max_position_value, snapshot.account.buying_power)
        quantity = int(budget // snapshot.last_price)
        if self.quantity_cap is not None:
            quantity = min(quantity, self.quantity_cap)
        if quantity <= 0:
            return TradeDecision(
                symbol=snapshot.symbol,
                action="HOLD",
                quantity=0,
                confidence=0.0,
                rationale="benchmark budget cannot buy one share",
                strategy_id=self.strategy_id,
            )
        self._bought = True
        return TradeDecision(
            symbol=snapshot.symbol,
            action="BUY",
            quantity=quantity,
            confidence=0.6,
            rationale=f"benchmark buys {quantity} shares at the position limit and holds",
            strategy_id=self.strategy_id,
        )


@dataclass
class RandomEngine:
    """Negative control: seeded random direction at a fixed cadence."""

    seed: int = 0
    quantity: int = 1
    every: int = 20
    strategy_id: str = "random-control"
    _rng: random.Random = field(init=False, repr=False)
    _calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        self._calls += 1
        action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
        if self._calls % self.every == 0:
            action = "BUY" if self._rng.random() < 0.5 else "SELL"
        return TradeDecision(
            symbol=snapshot.symbol,
            action=action,
            quantity=self.quantity if action != "HOLD" else 0,
            confidence=0.6 if action != "HOLD" else 0.0,
            rationale=f"negative control: seeded random ({self.seed})",
            strategy_id=self.strategy_id,
        )


@dataclass
class ReversedSmaEngine:
    """Negative control: the fixed rule's signals, inverted."""

    inner: SmaEngine
    strategy_id: str = "reversed-control"

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        decision = self.inner(snapshot)
        if decision.action == "BUY":
            return decision.model_copy(
                update={
                    "action": "SELL",
                    "rationale": f"reversed control: {decision.rationale}",
                }
            )
        if decision.action == "SELL":
            return decision.model_copy(
                update={
                    "action": "BUY",
                    "rationale": f"reversed control: {decision.rationale}",
                }
            )
        return decision


@dataclass
class ShuffledSmaEngine:
    """Negative control: the same SMA rule over a shuffled history.

    If a rule's result survives destroying the order of its own inputs, the result
    was not produced by the rule. The price used for the fill is still real; only
    the history the signal reads is shuffled.
    """

    fast: int = 20
    band: float = 0.01
    seed: int = 0
    quantity: int = 1
    confidence: float = 0.6
    strategy_id: str = "shuffled-control"
    _prices: list[float] = field(default_factory=list, init=False, repr=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        self._prices.append(snapshot.last_price)
        history = list(self._prices)
        self._rng.shuffle(history)
        if len(history) < self.fast:
            return TradeDecision(
                symbol=snapshot.symbol,
                action="HOLD",
                quantity=0,
                confidence=0.0,
                rationale="negative control: not enough history",
                strategy_id=self.strategy_id,
            )
        sma = sum(history[-self.fast:]) / self.fast
        action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
        if snapshot.last_price > sma * (1 + self.band):
            action = "BUY"
        elif snapshot.last_price < sma * (1 - self.band):
            action = "SELL"
        return TradeDecision(
            symbol=snapshot.symbol,
            action=action,
            quantity=self.quantity if action != "HOLD" else 0,
            confidence=self.confidence if action != "HOLD" else 0.0,
            rationale=f"negative control: shuffled history ({self.seed})",
            strategy_id=self.strategy_id,
        )


def default_engines() -> dict[str, Callable[[], Engine]]:
    """The three arms that can run without an LLM. D is deliberately absent."""
    return {
        "A": SleeveEngine,
        "B": lambda: SmaEngine(fast=20, band=0.01, strategy_id="fixed-sma20"),
        "C": lambda: SmaEngine(fast=20, slow=50, strategy_id="search-sma20-50"),
    }


def run_arm(
    *,
    arm: str,
    bars: Sequence[ReplayBar],
    engine: Engine,
    seed: int = 0,
    starting_equity: float = 100_000.0,
    symbol: str = "SPY",
    risk: RiskLimits | None = None,
    costs: CostModel | None = None,
) -> ArmOutcome:
    """Run one arm through the real Guardian and the next-open replay executor."""
    if not bars:
        raise ValueError("a replay over zero bars would prove nothing")
    risk = risk or RiskLimits()
    costs = costs or CostModel()
    symbol = symbol.upper()
    if symbol not in risk.allowlist:
        raise ValueError(f"{symbol} is outside the replay allowlist {sorted(risk.allowlist)}")

    book = ReplayBook(starting_equity=starting_equity, symbol=symbol)
    gateway = ReplayDataGateway(bars=list(bars), book=book, symbol=symbol)
    guardian = Guardian(
        allowlist=risk.allowlist,
        max_position_value=risk.max_position_value,
        max_daily_loss=risk.max_daily_loss,
        max_total_exposure=risk.max_total_exposure,
        max_trades_per_day=risk.max_trades_per_day,
    )
    executor = ReplayExecutor(
        gateway=gateway,
        book=book,
        slippage_pct=costs.slippage_pct,
        commission_pct=costs.commission_pct,
    )

    outcome = ArmOutcome(arm=arm, seed=seed, min_cash=book.cash)
    peak = starting_equity
    current_day = bars[0].timestamp.date()
    trades_today = 0

    for index, bar in enumerate(bars):
        day = bar.timestamp.date()
        if day != current_day:
            current_day = day
            trades_today = 0
            # The daily-loss limit is a per-day limit. Leaving it anchored at the
            # starting equity turns any drawdown since inception into a permanent
            # trading ban, which is not what the live Guardian does.
            book.day_start_equity = book.equity(bar.open)

        price = bar.close
        equity = book.equity(price)
        outcome.equity_curve.append(equity)
        peak = max(peak, equity)
        if peak:
            outcome.max_drawdown = max(outcome.max_drawdown, (peak - equity) / peak)
        outcome.max_gross_exposure = max(
            outcome.max_gross_exposure, abs(book.quantity) * price
        )

        snapshot = gateway.snapshot(symbol)
        decision = engine(snapshot)

        if decision.action in {"BUY", "SELL", "SHORT", "COVER"} and decision.quantity > 0:
            if index == len(bars) - 1:
                outcome.refusals.append(
                    {
                        "timestamp": bar.timestamp.isoformat(),
                        "action": decision.action,
                        "quantity": decision.quantity,
                        "reason": "last bar cannot fill: no next open exists",
                    }
                )
            else:
                guardian_result = guardian.review(
                    decision,
                    snapshot,
                    mode="paper",
                    trades_today=trades_today,
                    now=bar.timestamp,
                    agent_position_quantity=book.quantity,
                )
                if guardian_result.approved:
                    execution = executor.execute(
                        decision, guardian_result, cycle_id=f"{arm}-{seed}-{index}"
                    )
                    if execution.status == "REPLAYED":
                        trades_today += 1
                        outcome.fills.append(
                            {
                                "decision_timestamp": bar.timestamp.isoformat(),
                                "fill_timestamp": bars[index + 1].timestamp.isoformat(),
                                "action": decision.action,
                                "quantity": execution.filled_quantity,
                                "price": execution.filled_avg_price,
                            }
                        )
                else:
                    outcome.refusals.append(
                        {
                            "timestamp": bar.timestamp.isoformat(),
                            "action": decision.action,
                            "quantity": decision.quantity,
                            "reason": guardian_result.reason,
                        }
                    )

        outcome.min_cash = min(outcome.min_cash, book.cash)
        outcome.bars += 1
        gateway.advance()

    outcome.final_equity = outcome.equity_curve[-1] if outcome.equity_curve else starting_equity
    outcome.min_cash = min(outcome.min_cash, book.cash)
    return outcome


def run_experiment(
    *,
    bars: Sequence[ReplayBar],
    engines: dict[str, Callable[[], Engine]] | None = None,
    seeds: Sequence[int] = (0,),
    starting_equity: float = 100_000.0,
    symbol: str = "SPY",
    risk: RiskLimits | None = None,
    costs: CostModel | None = None,
    benchmarks: dict[str, Callable[[], Engine]] | None = None,
    controls: dict[str, Callable[[], Engine]] | None = None,
    min_effect: float = 0.0,
) -> ExperimentReport:
    """Run every supplied arm for every seed, then compare D-C, C-A and B-A.

    Benchmarks and controls are reported beside the arms but are never part of the
    pre-registered family, so they cannot turn a comparison into a pass. Each arm is
    also compared with each benchmark; those comparisons are descriptive and carry no
    Holm adjustment.
    """

    engines = engines if engines is not None else default_engines()
    report = ExperimentReport()

    def run_one(arm: str, engine: Engine, seed: int) -> ArmOutcome:
        return run_arm(
            arm=arm,
            bars=bars,
            engine=engine,
            seed=seed,
            starting_equity=starting_equity,
            symbol=symbol,
            risk=risk,
            costs=costs,
        )

    for arm, factory in engines.items():
        report.arms[arm] = [run_one(arm, factory(), seed) for seed in seeds]

    for name, factory in (benchmarks or {}).items():
        report.benchmarks[name] = run_one(name, factory(), seeds[0] if seeds else 0)
    for name, factory in (controls or {}).items():
        report.controls[name] = run_one(name, factory(), seeds[0] if seeds else 0)

    for left, right in (("D", "C"), ("C", "A"), ("B", "A")):
        if left not in report.arms or right not in report.arms:
            continue
        for left_run, right_run in zip(
            report.arms[left], report.arms[right], strict=False
        ):
            boot = paired_block_bootstrap(
                daily_returns(right_run.equity_curve),
                daily_returns(left_run.equity_curve),
            )
            report.comparisons.append(
                {
                    "left": left,
                    "right": right,
                    "seed": left_run.seed,
                    "mean_diff": boot["mean_diff"],
                    "ci_low": boot["ci_low"],
                    "ci_high": boot["ci_high"],
                    "p_value": boot.get("p_value"),
                    "p_two_sided": boot.get("p_two_sided"),
                    "n": boot["n"],
                }
            )

    for arm, outcomes in report.arms.items():
        for benchmark_name, benchmark_outcome in report.benchmarks.items():
            benchmark_returns = daily_returns(benchmark_outcome.equity_curve)
            for arm_run in outcomes:
                boot = paired_block_bootstrap(
                    benchmark_returns, daily_returns(arm_run.equity_curve)
                )
                report.benchmark_comparisons.append(
                    {
                        "arm": arm,
                        "benchmark": benchmark_name,
                        "seed": arm_run.seed,
                        "mean_diff": boot["mean_diff"],
                        "ci_low": boot["ci_low"],
                        "ci_high": boot["ci_high"],
                        "p_value": boot.get("p_value"),
                        "p_two_sided": boot.get("p_two_sided"),
                        "n": boot["n"],
                    }
                )

    primary = [c for c in report.comparisons if c["left"] == "D" and c["right"] == "C"]
    if not primary:
        report.notes.append(
            "D NOT_RUN: no LLM weekly engine was supplied; the primary comparison D-C "
            "was not evaluated"
        )
        report.conclusion = "INSUFFICIENT"
    elif all(c["ci_low"] > 0 and c["mean_diff"] >= min_effect for c in primary):
        report.conclusion = "PROVEN"
    elif all(c["ci_high"] < 0 for c in primary):
        report.conclusion = "NOT PROVEN"
    else:
        report.conclusion = "INSUFFICIENT"
    return report
