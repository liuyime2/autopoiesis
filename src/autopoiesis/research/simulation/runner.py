"""Replay runner over historical bars using a decision callback.

Step 3 of docs/EXECUTION_PLAN_2026-11-05.md. The decision callback receives
the current bar context and returns an action string. Fills are simulated with
slippage and commission. LLM-derived decisions must be tagged
MODEL_PRIOR_LEAKAGE_RISK by the caller.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .account import SimulatedAccount
from .clock import SimulationClock


@dataclass
class ReplayResult:
    bars: int = 0
    fills: list[dict] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    max_drawdown: float = 0.0
    hard_risk_breaches: int = 0
    reproducible: bool = True
    notes: list[str] = field(default_factory=list)


def run_replay(
    *,
    bars: Iterable[dict],
    decision_fn: Callable[[dict], str],
    starting_cash: float = 100000.0,
    slippage_pct: float = 0.001,
    commission_pct: float = 0.005,
    llm_used: bool = False,
) -> ReplayResult:
    """Replay bars through decision_fn. Bars are dicts with keys: timestamp, symbol, price."""
    account = SimulatedAccount(
        cash=starting_cash, slippage_pct=slippage_pct, commission_pct=commission_pct
    )
    result = ReplayResult()
    if llm_used:
        result.notes.append("MODEL_PRIOR_LEAKAGE_RISK")
    prices: dict[str, float] = {}
    peak = starting_cash
    bar_list = list(bars)
    if not bar_list:
        return result
    clock = SimulationClock(bar_list[0]["timestamp"])
    for bar in bar_list:
        clock.advance_to(bar["timestamp"])
        symbol = bar["symbol"]
        price = float(bar["price"])
        prices[symbol] = price
        action = decision_fn({"timestamp": clock.now, "symbol": symbol, "price": price, "prices": dict(prices)})
        if action in {"BUY", "SELL", "SHORT", "COVER"}:
            qty = 1
            account.apply_fill(symbol, action, qty, price)
            result.fills.append({"timestamp": clock.now.isoformat(), "symbol": symbol, "action": action, "price": price})
        equity = account.mark_to_market(prices)
        result.equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak else 0.0
        if dd > result.max_drawdown:
            result.max_drawdown = dd
        result.bars += 1
    return result
