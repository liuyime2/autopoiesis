"""SIMULATION. Does the core sleeve stay the only thing that trades its symbol under a hostile rival?

Real SPY 5-minute bars (cached Alpaca history, 2024-01-02..2026-10-08), a SIMULATED $100,000 account
(`ReplayBook`), the real `TradingLoop`, the real `Guardian`, a strict journal. Five cycles a day, thirty
minutes apart, from 10:00 New York. The rival decision engine is deliberately bad: it alternates a
one-share BUY and SELL of SPY on every cycle it is asked, the way the 2026-10-26..29 SPY RULE and
TREND_FOLLOW probes did. Nothing here reaches a broker: `ReplayExecutor` holds no client and reports
status REPLAYED.

Run A: rival alone (no sleeve) - what the rival does if nothing stops it.
Run B: sleeve + rival - the rival may only be asked when the sleeve does not own the symbol.

Usage: PYTHONPATH=src python sleeve_exclusive_replay.py <SPY_5min_bars.json> <YAHOO.pkl> <out.json>
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from autopoiesis.daemon import AgentDaemon
from autopoiesis.guardian import Guardian
from autopoiesis.journal import JsonlJournal
from autopoiesis.loop import TradingLoop
from autopoiesis.models import TradeDecision
from autopoiesis.replay import ReplayBook, ReplayDataGateway, ReplayExecutor, load_bars
from autopoiesis.risk_judgment import ewma_series
from autopoiesis.sleeve import SLEEVE_STRATEGY_ID, CoreSleeve

NY = ZoneInfo("America/New_York")
POOL = 20_000.0
OFFSETS = (0, 6, 12, 18, 24)   # 5-minute bars after the first bar from 10:00 NY


class HostileRival:
    """A competing strategy engine that always has something to say, and says the opposite of last time."""

    def __init__(self) -> None:
        self.asked = 0

    def decide_snapshot(self, snapshot):
        self.asked += 1
        action = "BUY" if self.asked % 2 else "SELL"
        return TradeDecision(symbol=snapshot.symbol, action=action, quantity=1, confidence=0.9,
                             rationale="SIMULATION rival engine: alternates BUY and SELL", strategy_id="rival-probe")


def run(bars, daily, first_bar, with_sleeve: bool) -> dict:
    book = ReplayBook(starting_equity=100_000.0)
    gateway = ReplayDataGateway(bars=bars, book=book)
    guardian = Guardian(allowlist=frozenset({"SPY"}), max_position_value=25_000.0, max_daily_loss=500.0,
                        max_trades_per_day=100_000, max_total_exposure=90_000.0)
    clock = {"now": bars[0].timestamp}
    rival = HostileRival()
    loop = TradingLoop(data_gateway=gateway, decision_engine=rival, guardian=guardian,
                       executor=ReplayExecutor(gateway=gateway, book=book),
                       journal=JsonlJournal(Path(tempfile.mkdtemp()) / "j.jsonl", strict=True), mode="paper",
                       now=lambda: clock["now"], manage_account=True)
    loop.accumulation_allowed = lambda strategy_id: True   # the screen is not what this run tests

    def block_for(day):
        def block(_symbol):
            series = ewma_series(daily[daily.index < pd.Timestamp(day)].tolist())
            return {"forecast_vol_pct": series[-1], "status": "ACTIVE"} if series else None
        return block

    by_source: Counter[str] = Counter()
    exposure: list[float] = []
    for day, base in sorted(first_bar.items()):
        book.day_start_equity = book.equity(bars[base].close)
        if with_sleeve:
            loop.sleeve = CoreSleeve(symbol="SPY", pool=POOL, target_vol_pct=10.0, risk_block=block_for(day))
        for offset in OFFSETS:
            index = base + offset
            if index >= len(bars) or bars[index].timestamp.astimezone(NY).date() != day:
                continue
            gateway.cursor, clock["now"] = index, bars[index].timestamp
            before = len(book.filled)
            record = loop.run_once("SPY")
            if record is not None and len(book.filled) > before:
                by_source[record.decision.strategy_id or "engine"] += 1
            exposure.append(abs(book.quantity) * bars[index].close)
    return {
        "cycles_asked_of_the_rival": rival.asked, "filled_trades_by_source": dict(by_source),
        "average_exposure_usd": round(sum(exposure) / len(exposure), 0), "max_exposure_usd": round(max(exposure), 0),
        "final_account_pnl_usd": round(book.equity(bars[-1].close) - 100_000.0, 2),
    }


def main() -> None:
    bars = load_bars(sys.argv[1])
    daily = pd.read_pickle(sys.argv[2])[("SPY", "adjclose")].dropna()
    daily.index = pd.to_datetime(daily.index).tz_localize(None).normalize()
    first_bar = {}
    for index, bar in enumerate(bars):
        local = bar.timestamp.astimezone(NY)
        if local.hour >= 10 and local.date() not in first_bar:
            first_bar[local.date()] = index
    out = {
        "simulation": True, "days": len(first_bar), "cycles_per_day": len(OFFSETS), "pool_usd": POOL,
        "A_rival_alone": run(bars, daily, first_bar, with_sleeve=False),
        "B_sleeve_and_rival": run(bars, daily, first_bar, with_sleeve=True),
    }
    b = out["B_sleeve_and_rival"]
    out["checks"] = {
        "only_the_sleeve_traded": set(b["filled_trades_by_source"]) <= {SLEEVE_STRATEGY_ID},
        "exposure_never_above_pool_plus_one_share": b["max_exposure_usd"] <= POOL + 800,
    }
    Path(sys.argv[3]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
