#!/usr/bin/env python3
"""The smallest useful thing this system does, in one file.

Run it:

    pip install -e ".[dev]"
    python examples/minimal_cycle.py

It builds a real `DataSnapshot`, asks the real `Guardian` whether a decision is allowed,
and explains the verdict. No broker, no credentials, no model, no network - which is the
point: the safety layer is the one part you can and should read without standing up the
rest of the system.

The two cases below are the ones that matter. The first is a buy inside every limit. The
second asks to sell a position the agent does not own, and the Guardian refuses it - not
because a limit was hit, but because the agent cannot establish that the shares are its
own. That refusal is the property that protects an account that also holds someone else's
money.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autopoiesis.guardian import Guardian
from autopoiesis.models import (
    AccountSnapshot,
    DataSnapshot,
    PositionSnapshot,
    TradeDecision,
)


def snapshot(*, symbol: str, price: float, qty: float) -> DataSnapshot:
    return DataSnapshot(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        market_open=True,
        last_price=price,
        source="example",
        account=AccountSnapshot(
            equity=100_000.0,
            cash=100_000.0,
            buying_power=100_000.0,
            portfolio_value=100_000.0,
            daily_loss=0.0,
            day_start_equity_known=True,
        ),
        positions=[PositionSnapshot(symbol=symbol, quantity=qty, market_value=qty * price)],
        open_orders=[],
    )


def main() -> int:
    guardian = Guardian(
        allowlist=("SPY",),
        max_position_value=5_000.0,
        max_daily_loss=500.0,
        max_trades_per_day=10,
        max_total_exposure=20_000.0,
    )

    print("1. a buy that is inside every limit")
    buy = TradeDecision(
        symbol="SPY", action="BUY", quantity=1, confidence=0.8,
        rationale="example: a one-share buy well inside every limit",
    )
    verdict = guardian.review(buy, snapshot(symbol="SPY", price=600.0, qty=0.0))
    print(f"   {buy.action} {buy.quantity} @ 600.00 -> approved={verdict.approved}")
    print(f"   reason: {verdict.reason}\n")

    print("2. a sell of shares the agent never bought")
    sell = TradeDecision(
        symbol="SPY", action="SELL", quantity=1, confidence=0.8,
        rationale="example: attempt to sell shares the agent does not own",
    )
    # The account holds 10. The agent owns 0, and that is what the last argument says.
    verdict = guardian.review(
        sell, snapshot(symbol="SPY", price=600.0, qty=10.0), agent_position_quantity=0.0
    )
    print(f"   {sell.action} {sell.quantity} @ 600.00 -> approved={verdict.approved}")
    print(f"   reason: {verdict.reason}")
    print("   The account holds 10 shares; the agent owns none of them, so the 10")
    print("   are not the agent's to sell.\n")

    print("3. the same sell, where the agent does own the shares")
    verdict = guardian.review(
        sell, snapshot(symbol="SPY", price=600.0, qty=10.0), agent_position_quantity=10.0
    )
    print(f"   {sell.action} {sell.quantity} @ 600.00 -> approved={verdict.approved}")
    print(f"   reason: {verdict.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
