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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from autopoiesis.guardian import Guardian
from autopoiesis.models import DataSnapshot, TradeDecision
from autopoiesis.replay import ReplayBar, ReplayBook, ReplayDataGateway, ReplayExecutor

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
    comparisons: list[dict] = field(default_factory=list)
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


def default_engines() -> dict[str, Callable[[], Engine]]:
    """The three arms that can run without an LLM. D is deliberately absent."""
    return {
        "A": HoldEngine,
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
) -> ExperimentReport:
    """Run every supplied arm for every seed, then compare D-C, C-A and B-A."""
    engines = engines if engines is not None else default_engines()
    report = ExperimentReport()

    for arm, factory in engines.items():
        report.arms[arm] = []
        for seed in seeds:
            report.arms[arm].append(
                run_arm(
                    arm=arm,
                    bars=bars,
                    engine=factory(),
                    seed=seed,
                    starting_equity=starting_equity,
                    symbol=symbol,
                    risk=risk,
                    costs=costs,
                )
            )

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
    elif all(c["ci_low"] > 0 and c["mean_diff"] > 0 for c in primary):
        report.conclusion = "PROVEN"
    elif all(c["ci_high"] < 0 for c in primary):
        report.conclusion = "NOT PROVEN"
    else:
        report.conclusion = "INSUFFICIENT"
    return report
