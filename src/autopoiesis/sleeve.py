"""The core sleeve: hold SPY at a volatility-targeted size, inside a fixed pool of capital.

Why it exists. The 2026-10-24 review found the account mostly in cash-like holdings and the agent
behind SPY, with no price signal that survives testing. What is left that has evidence is that
volatility persists. Held at a size that targets 10% annualised volatility, SPY has halved the
worst drawdown and lifted the Sharpe ratio over 33 years of real daily data
(docs/evidence/goal-2026-10-24/). It does not beat SPY's return; it is a risk product, and it is
reported as one.

The account holder authorised it on 2026-10-24 as the one exception to the rule that a strategy
which has not passed the screen may not add (the screen scores 24-hour direction, which a sizing
rule cannot be graded by). The exception is this module's alone: it is off unless the env file
names a symbol and a pool, the pool can never exceed `SLEEVE_POOL_CAP`, and the decision it makes
goes through Guardian, the executor and the journal exactly like any other. Nothing here sends an
order.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, Literal
from zoneinfo import ZoneInfo

from autopoiesis.learning_evidence import capture_behavior
from autopoiesis.models import DataSnapshot, JournalEvent, TradeDecision
from autopoiesis.risk_judgment import vol_scaled_fraction

NEW_YORK = ZoneInfo("America/New_York")
SLEEVE_STRATEGY_ID = "core-sleeve"
#: The ceiling the account holder authorised. Raising it is a code change they make, not a setting.
SLEEVE_POOL_CAP = 20_000.0
#: Risk statuses under which the forecast may size the position. SUSPENDED means the forecast has
#: been shown to be poor, and a sizing rule built on it stands down.
USABLE_STATUSES = frozenset({"ACTIVE", "UNVERIFIED"})
CONFIDENCE = 0.8


class CoreSleeve:
    def __init__(
        self,
        *,
        symbol: str,
        pool: float,
        target_vol_pct: float,
        risk_block: Callable[[str], dict[str, Any] | None],
        band: float = 0.10,
    ):
        if not 0 < pool <= SLEEVE_POOL_CAP:
            raise ValueError(f"sleeve pool must be in (0, {SLEEVE_POOL_CAP:.0f}], got {pool}")
        if target_vol_pct <= 0:
            raise ValueError("sleeve target volatility must be positive")
        self.symbol = symbol.upper()
        self.pool = pool
        self.target_vol_pct = target_vol_pct
        self.risk_block = risk_block
        #: Share of the pool the position may drift from target before it is traded. Measured in
        #: the evaluation at 0.10; it keeps turnover near 1.6x of the pool a year.
        self.band = band
        #: What the journal records as the behaviour behind every sleeve order; the verifier and the
        #: attribution code require one.
        self.provenance = capture_behavior(
            SLEEVE_STRATEGY_ID, "CORE_SLEEVE", (self.symbol,),
            {"pool": pool, "target_vol_pct": target_vol_pct, "band": band}, pool,
        )

    def plan(self, snapshot: DataSnapshot) -> TradeDecision | None:
        """A rebalance for this snapshot, or None when the sleeve has nothing to say.

        None means "carry on with the ordinary decision", not HOLD: inside the band, or without a
        usable forecast, the sleeve stays out of the way.

        The target is for the whole position in the symbol, whoever bought it: the account holder
        delegated management of every position. The pool is a fixed-dollar ceiling, so a position
        that has appreciated past it is brought back to `pool x fraction`. (A first version stood
        down whenever the holding exceeded the pool; replayed on real 2024-2026 bars it stopped
        derisking after the first gains, which is exactly when it is needed.)
        """
        if snapshot.symbol.upper() != self.symbol or not snapshot.market_open or snapshot.last_price <= 0:
            return None
        if any(order.symbol.upper() == self.symbol for order in snapshot.open_orders):
            return None
        block = self.risk_block(self.symbol)
        if not block or block.get("status") not in USABLE_STATUSES:
            return None
        vol = block.get("forecast_vol_pct")
        if not isinstance(vol, int | float) or vol <= 0:
            return None
        held = sum(p.quantity for p in snapshot.positions if p.symbol.upper() == self.symbol)
        held_shares = max(0, math.floor(held))
        price = snapshot.last_price
        fraction = vol_scaled_fraction(float(vol), self.target_vol_pct)
        target_shares = int(self.pool * fraction // price)
        gap = (target_shares - held_shares) * price
        if abs(gap) <= self.band * self.pool:
            return None
        action: Literal["BUY", "SELL"] = "BUY" if gap > 0 else "SELL"
        return TradeDecision(
            symbol=self.symbol,
            action=action,
            quantity=abs(target_shares - held_shares),
            confidence=CONFIDENCE,
            rationale=(
                f"core sleeve: forecast vol {float(vol):.1f}% vs target {self.target_vol_pct:.1f}% -> "
                f"{fraction:.2f} of a ${self.pool:,.0f} pool = {target_shares} shares, holding {held_shares}"
            ),
            strategy_id=SLEEVE_STRATEGY_ID,
            strategy_provenance=self.provenance,
            decision_source="policy_engine",
        )


def sleeve_performance(
    fills: Sequence[JournalEvent], daily_prices: Mapping[date, float], pool: float
) -> dict[str, Any] | None:
    """How the sleeve did against holding the whole pool, from broker-confirmed fills.

    `fills` is `fill_reconciler.confirmed_fill_events` output; `daily_prices` the symbol's last
    journalled price by New York date. The comparator is the pool bought in full at the price of
    the first sleeve fill and held, so the difference between the two lines is the cost (or gain)
    of sizing down. None until a sleeve fill exists. A day with no price is skipped, never filled in.
    """
    mine = []
    for event in fills:
        payload = event.payload
        action = str(payload.get("action", payload.get("side", ""))).upper()
        qty = payload.get("cumulative_filled_quantity", payload.get("filled_quantity"))
        px = payload.get("cumulative_filled_avg_price", payload.get("filled_avg_price"))
        if event.strategy_id != SLEEVE_STRATEGY_ID or action not in {"BUY", "SELL"}:
            continue
        if not isinstance(qty, int | float) or not isinstance(px, int | float) or qty <= 0 or px <= 0:
            continue
        mine.append((event.timestamp.astimezone(NEW_YORK).date(), (1 if action == "BUY" else -1) * float(qty), float(px)))
    if not mine:
        return None
    mine.sort(key=lambda row: row[0])
    first_day, _, first_px = mine[0]
    days = sorted(d for d in daily_prices if d >= first_day)
    shares = cash = 0.0
    cash = pool
    values: list[float] = []
    held_values: list[float] = []
    deployed: list[float] = []
    cursor = 0
    for day in days:
        while cursor < len(mine) and mine[cursor][0] <= day:
            _, signed, px = mine[cursor]
            shares += signed
            cash -= signed * px
            cursor += 1
        price = daily_prices[day]
        values.append(cash + shares * price)
        held_values.append(pool * price / first_px)
        deployed.append(shares * price / pool)
    if not values:
        return None
    return {
        "first_fill": first_day.isoformat(), "days": len(values), "fills": len(mine),
        "pool": pool, "sleeve_pnl": round(values[-1] - pool, 2),
        "hold_pool_pnl": round(held_values[-1] - pool, 2),
        "sleeve_max_drawdown_pct": round(_max_drawdown(values) * 100, 2),
        "hold_pool_max_drawdown_pct": round(_max_drawdown(held_values) * 100, 2),
        "average_deployed_fraction": round(sum(deployed) / len(deployed), 3),
    }


def _max_drawdown(values: Sequence[float]) -> float:
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst
