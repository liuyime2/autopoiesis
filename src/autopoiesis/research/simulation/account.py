"""Simulated account with cash, positions, and cost model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimulatedPosition:
    symbol: str
    quantity: int = 0
    avg_price: float = 0.0


@dataclass
class SimulatedAccount:
    cash: float = 100000.0
    positions: dict[str, SimulatedPosition] = field(default_factory=dict)
    commission_pct: float = 0.005
    slippage_pct: float = 0.001

    def position(self, symbol: str) -> SimulatedPosition:
        if symbol not in self.positions:
            self.positions[symbol] = SimulatedPosition(symbol=symbol)
        return self.positions[symbol]

    def apply_fill(
        self, symbol: str, action: str, quantity: int, price: float
    ) -> float:
        """Apply a fill; return the cash delta (negative for buys)."""
        pos = self.position(symbol)
        cost = price * (1 + self.slippage_pct) if action in {"BUY", "COVER"} else price * (1 - self.slippage_pct)
        notional = cost * quantity
        commission = notional * self.commission_pct
        if action in {"BUY", "COVER"}:
            self.cash -= notional + commission
            pos.quantity += quantity
            pos.avg_price = price
        else:
            self.cash += notional - commission
            pos.quantity -= quantity
        return -(notional + commission) if action in {"BUY", "COVER"} else (notional - commission)

    def mark_to_market(self, prices: dict[str, float]) -> float:
        total = self.cash
        for sym, pos in self.positions.items():
            if sym in prices and pos.quantity:
                total += pos.quantity * prices[sym]
        return total
