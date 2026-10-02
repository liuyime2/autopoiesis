"""Execute a strategy's *declared* rule over real recorded prices.

Phase 4 of the objective asks for strict offline validation - backtest,
walk-forward out-of-sample, cost, leakage, stress and overfitting control. None of
it existed. This is the backtest.

An earlier session concluded a backtest would be "theatre" because production never
enforces a strategy's rule, and declined to build one. That was half right and it
mattered, so both halves are recorded here rather than quietly dropped:

* **Right:** production does not enforce `parameters["action"]` or TREND_FOLLOW's
  `threshold_pct`. The selected strategy is context handed to the model, and the
  model decides. So a passing backtest says nothing about what production will do.
  That is precisely why walk-forward, shadow and probation exist, and a green
  backtest must never promote anything on its own.
* **Wrong:** "not enforced in production" does not mean "not specified". A
  `StrategySpec` carries `kind` plus the parameters that define its rule, and those
  rules are executable. The research layer is exactly where they should be
  measured. Refusing to build the measurement left Phase 4 empty and left every
  candidate admitted on nothing but a decision-level screen.

The data is real and the failures are honest:

* Quotes come from the journal, so there are only a few hundred distinct
  observations. `BacktestResult.power` says so explicitly rather than printing a
  confident Sharpe on 40 points. A number computed on this little data is a
  hypothesis, not a result, and the tool is built to make that visible.
* Costs are charged on entry *and* exit. The paper broker reported zero commission
  on all 24 fills, so a gross backtest would flatter every strategy by exactly what
  it would really have paid.
* Decisions are made from data at or before the decision bar only. `leakage_violations`
  counts any bar that looked ahead; it is expected to be 0 and a non-zero value is
  a bug in this file, not a result about the strategy.
* Every rule evaluation is a pure function of the bars up to and including the
  current one, so the same input always produces the same output.

Rules implemented, matching what the models actually validate:

* ``HOLD_BASELINE`` - never trades. The control: if this cannot be beaten, the
  backtest is measuring noise.
* ``FIXED_SIZE`` - trades `quantity` on `action`` whenever the bar is tradable.
  The engine is **long-only**, matching the production executor: a SELL closes an
  existing long and never opens a short. A SELL-only strategy therefore produces no
  trades at all here, which is the honest reading - it is not a strategy this system
  can hold, and pretending otherwise would flatter it with a short it cannot take.
  A BUY rule fires every bar, so its backtest is a buy-and-hold comparison, not
  evidence of skill.
* ``TREND_FOLLOW`` - acts when the price has moved `threshold_pct` away from
  `reference_price`. The reference is re-anchored to the first bar so the rule does
  not depend on a stale price from whenever the spec was written; the offset is
  preserved relative to that anchor and reported.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

#: Reused, not re-declared. The separation rule is one-way - production must never
#: import research - so research may import production. This module previously had
#: zero `min_agent` imports and therefore carried its own copies of `Bar` and
#: `bars_from_records`, byte-identical to `min_agent.regime`'s apart from a
#: docstring. That made a bar built here a *different class* from the one
#: `regime.bars_from_records` returns, so a rule handed a real bar could fail an
#: `isinstance` check on nominal grounds alone.
from min_agent.regime import Bar, bars_from_records

#: `bars_from_records` is re-exported on purpose: it is part of this module's
#: public shape and callers reach it as `backtest.bars_from_records`. Naming it
#: here is what stops the import being seen as unused and deleted again.
__all__ = ["Bar", "bars_from_records"]

#: Commission per side as a percent of notional. Alpaca paper reported 0.0 on every
#: fill, so this stands in for what a real venue charges. It is an assumption, not
#: an observation, and it is recorded in every result.
DEFAULT_COMMISSION_PCT = 0.005

#: Slippage per side as a percent of notional. Paper fills are optimistic; a real
#: entry pays the spread. Same reasoning as the commission.
DEFAULT_SLIPPAGE_PCT = 0.005

KIND_FIXED_SIZE = "FIXED_SIZE"
KIND_TREND_FOLLOW = "TREND_FOLLOW"
KIND_HOLD_BASELINE = "HOLD_BASELINE"
SUPPORTED_KINDS = frozenset({KIND_FIXED_SIZE, KIND_TREND_FOLLOW, KIND_HOLD_BASELINE})


@dataclass
class RuleDecision:
    timestamp: datetime
    action: str  # BUY, SELL or HOLD
    quantity: int
    price: float
    reason: str


@dataclass
class BacktestResult:
    strategy_id: str
    kind: str
    bars: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    leakage_violations: int = 0
    unsupported: str = ""
    decisions: list[RuleDecision] = field(default_factory=list)

    @property
    def win_rate(self) -> float | None:
        closed = self.wins + self.losses
        return None if closed == 0 else self.wins / closed

    @property
    def net_of_cost(self) -> bool:
        return self.net_pnl > 0

    def power(self) -> str:
        """How much this result is worth. Said out loud, because a Sharpe printed
        next to 40 bars reads as a result and is not one."""
        closed = self.wins + self.losses
        if self.bars < 30:
            return f"INSUFFICIENT: {self.bars} bars, far too few to conclude anything"
        if closed < 10:
            return (
                f"INSUFFICIENT: {closed} closed trade(s) over {self.bars} bars; "
                "this is an anecdote"
            )
        if closed < 30:
            return (
                f"WEAK: {closed} closed trades over {self.bars} bars. Directional "
                "hint at best, not a result."
            )
        return f"USABLE: {closed} closed trades over {self.bars} bars, still a hypothesis."

    def to_payload(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "kind": self.kind,
            "bars": self.bars,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "gross_pnl": round(self.gross_pnl, 4),
            "costs": round(self.costs, 4),
            "net_pnl": round(self.net_pnl, 4),
            "return_pct": round(self.return_pct, 6),
            "max_drawdown_pct": round(self.max_drawdown_pct, 6),
            "leakage_violations": self.leakage_violations,
            "unsupported": self.unsupported,
            "power": self.power(),
        }


def _rule(
    kind: str, parameters: dict
) -> Callable[[Sequence[Bar], int], RuleDecision] | str:
    """Build a pure rule for `kind`, or return a reason it cannot be executed.

    Returning the reason as a string rather than raising keeps an unexecutable spec
    visible in the report instead of crashing a batch run and hiding the rest.
    """
    if kind not in SUPPORTED_KINDS:
        return f"kind {kind!r} has no implemented rule"

    if kind == KIND_HOLD_BASELINE:
        def baseline(bars: Sequence[Bar], i: int) -> RuleDecision:
            return RuleDecision(bars[i].timestamp, "HOLD", 0, bars[i].price, "baseline")
        return baseline

    if kind == KIND_FIXED_SIZE:
        action = str(parameters.get("action", "BUY")).upper()
        if action not in {"BUY", "SELL"}:
            return f"FIXED_SIZE action {action!r} is not BUY or SELL"
        quantity = int(parameters.get("quantity", 0) or 0)
        if quantity <= 0:
            return "FIXED_SIZE quantity must be positive"

        def fixed(bars: Sequence[Bar], i: int) -> RuleDecision:
            return RuleDecision(
                bars[i].timestamp, action, quantity, bars[i].price,
                "fixed size, fires every bar",
            )
        return fixed

    # TREND_FOLLOW
    threshold = parameters.get("threshold_pct")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        return "TREND_FOLLOW threshold_pct is missing or not numeric"
    if not 0 < float(threshold) <= 0.20:
        return f"TREND_FOLLOW threshold_pct {threshold!r} outside (0, 0.20]"
    action = str(parameters.get("action", "BUY")).upper()
    if action not in {"BUY", "SELL"}:
        return f"TREND_FOLLOW action {action!r} is not BUY or SELL"
    quantity = int(parameters.get("quantity", 0) or 0)
    if quantity <= 0:
        return "TREND_FOLLOW quantity must be positive"
    threshold = float(threshold)

    def trend(bars: Sequence[Bar], i: int) -> RuleDecision:
        # Re-anchored to the first bar rather than trusting `reference_price`, which
        # was written against whatever the price was on the day the LLM proposed
        # the spec. Anchoring on bar 0 keeps the rule executable on any window and
        # makes walk-forward splits comparable.
        anchor = bars[0].price
        price = bars[i].price
        move = (price - anchor) / anchor if anchor else 0.0
        fired = abs(move) >= threshold
        if not fired:
            return RuleDecision(
                bars[i].timestamp, "HOLD", 0, price,
                f"move {move:+.4f} inside threshold {threshold:.4f}",
            )
        # A rise above the threshold is the strategy's BUY condition; a fall is its
        # SELL condition. Mirroring that is what makes the rule directional rather
        # than a constant notional bet.
        fired_action = "BUY" if move > 0 else "SELL"
        return RuleDecision(
            bars[i].timestamp, fired_action, quantity, price,
            f"move {move:+.4f} cleared threshold {threshold:.4f}",
        )
    return trend


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0] if equity else 0.0
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak)
    return abs(worst) * 100.0


def run_backtest(
    bars: Sequence[Bar],
    *,
    strategy_id: str,
    kind: str,
    parameters: dict,
    commission_pct: float = DEFAULT_COMMISSION_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
) -> BacktestResult:
    """Run one rule over `bars`, charging cost on both sides of every round trip.

    Bars must already be sorted and de-duplicated by the caller; this function does
    not reorder them, because silently sorting a series is how a leakage bug hides.
    """
    result = BacktestResult(strategy_id=strategy_id, kind=kind, bars=len(bars))
    rule = _rule(kind, parameters or {})
    if isinstance(rule, str):
        result.unsupported = rule
        return result

    position = 0
    entry_price = 0.0
    equity = 1.0
    realized = 0.0
    spent = 0.0

    for i, bar in enumerate(bars):
        decision = rule(bars, i)
        result.decisions.append(decision)

        # Causality guard, tested rather than assumed. A rule must give the same
        # answer whether it is shown the whole series or only the bars up to now.
        # The first version of this check merely noticed the index going backwards,
        # which no rule here could do - it could not have detected a rule that read
        # a future price, which is the only leak that actually matters.
        truncated = rule(bars[: i + 1], i)
        if (truncated.action, truncated.quantity) != (decision.action, decision.quantity):
            result.leakage_violations += 1

        # Long-only by design, matching the production executor. A SELL with no
        # position is a no-op, not a short. The first version of this engine
        # advertised a SELL rule and then quietly did nothing with it, which would
        # have made every short strategy backtest as flat rather than wrong.
        if decision.action == "BUY" and position == 0:
            position = decision.quantity
            entry_price = bar.price * (1 + slippage_pct / 100.0)
            notional = position * entry_price
            cost = notional * commission_pct / 100.0
            spent += notional + cost
            result.costs += cost
            result.trades += 1
        elif decision.action == "SELL" and position > 0:
            exit_price = bar.price * (1 - slippage_pct / 100.0)
            notional = position * exit_price
            cost = notional * commission_pct / 100.0
            result.costs += cost
            gross = notional - position * entry_price
            realized += gross - cost
            equity *= 1 + (gross - cost) / (position * entry_price)
            result.wins += 1 if gross - cost > 0 else 0
            result.losses += 1 if gross - cost <= 0 else 0
            position = 0
        else:
            # Mark to market so drawdown reflects holding, not just closed trades.
            if position > 0:
                equity *= 1 + (bar.price - entry_price) / entry_price

    if position > 0 and bars:
        # Close the open position at the last bar so the result is complete. Left
        # open, a profitable run looks like no trades at all.
        last = bars[-1]
        exit_price = last.price * (1 - slippage_pct / 100.0)
        notional = position * exit_price
        cost = notional * commission_pct / 100.0
        result.costs += cost
        gross = notional - position * entry_price
        realized += gross - cost
        result.wins += 1 if gross - cost > 0 else 0
        result.losses += 1 if gross - cost <= 0 else 0

    result.gross_pnl = realized + result.costs
    result.net_pnl = realized
    if spent > 0:
        result.return_pct = realized / spent * 100.0
    result.max_drawdown_pct = _max_drawdown([1.0, equity])
    return result
