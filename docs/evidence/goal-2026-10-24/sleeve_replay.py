"""The core sleeve through the real loop, the real Guardian and the real executor path, on real bars.

For each trading day of SPY's 5-minute tape (2024-01-02..2026-10-08, cached Alpaca bars), the loop is
asked once, at the first bar from 10:00 New York, with a simulated $100,000 account. The sleeve
sees only daily closes strictly before that day (point in time) through the production EWMA
forecast; orders go through the real Guardian and fill at the next bar's open in the replay book
(`ReplayExecutor`: no broker client, status REPLAYED, not trading evidence). Everything else the
loop would ask is answered HOLD. The screen exception is the daemon's own `_screen_passed`.

Compared with buying and holding the same pool at the first fill. This is a plumbing and behaviour
check on a simulated account; the SPY performance claim is in vol_target_eval.json.

Usage: PYTHONPATH=src python sleeve_replay.py <SPY_5min_bars.json> <YAHOO.pkl> <out.json>
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from datetime import timedelta
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


class HoldEverythingElse:
    def decide_snapshot(self, snapshot):
        return TradeDecision(symbol=snapshot.symbol, action="HOLD", quantity=0, confidence=0.0,
                             rationale="replay: only the sleeve is under test")


def main() -> None:
    bars = load_bars(sys.argv[1])
    daily = pd.read_pickle(sys.argv[2])[("SPY", "adjclose")].dropna()
    daily.index = pd.to_datetime(daily.index).tz_localize(None).normalize()

    def risk_block_for(day):
        def block(_symbol):
            closes = daily[daily.index < pd.Timestamp(day)].tolist()
            series = ewma_series(closes)
            return {"forecast_vol_pct": series[-1], "status": "ACTIVE"} if series else None
        return block

    first_bar = {}
    for index, bar in enumerate(bars):
        local = bar.timestamp.astimezone(NY)
        if local.hour >= 10 and local.date() not in first_bar:
            first_bar[local.date()] = index

    book = ReplayBook(starting_equity=100_000.0)
    gateway = ReplayDataGateway(bars=bars, book=book)
    guardian = Guardian(allowlist=frozenset({"SPY"}), max_position_value=25_000.0, max_daily_loss=500.0,
                        max_trades_per_day=100, max_total_exposure=90_000.0)
    clock = {"now": bars[0].timestamp}
    journal = JsonlJournal(Path(tempfile.mkdtemp()) / "replay-journal.jsonl")
    loop = TradingLoop(data_gateway=gateway, decision_engine=HoldEverythingElse(), guardian=guardian,
                       executor=ReplayExecutor(gateway=gateway, book=book), journal=journal, mode="paper",
                       now=lambda: clock["now"], manage_account=True)  # production: config.whole_account
    loop.accumulation_allowed = AgentDaemon._screen_passed.__get__(
        type("D", (), {"loop": loop, "_screen_verdicts": {}})())

    outcomes: Counter[str] = Counter()
    equity, first_fill_price, first_fill_day, trades = [], None, None, []
    for day, index in sorted(first_bar.items()):
        gateway.cursor = index
        clock["now"] = bars[index].timestamp
        book.day_start_equity = book.equity(bars[index].close)
        loop.sleeve = CoreSleeve(symbol="SPY", pool=20_000.0, target_vol_pct=10.0, risk_block=risk_block_for(day))
        before = len(book.filled)
        record = loop.run_once("SPY")
        if record is None:
            outcomes["no_record"] += 1
            continue
        outcomes[f"{record.decision.strategy_id or 'engine'}:{record.decision.action}:{record.execution.status}"] += 1
        if len(book.filled) > before:
            side, qty, price = book.filled[-1]
            trades.append((str(day), side, qty, round(price, 2)))
            if first_fill_price is None:
                first_fill_price, first_fill_day = price, day
        equity.append((day, book.equity(bars[index].close), book.quantity * bars[index].close))

    days = [(d, e, x) for d, e, x in equity if first_fill_day is not None and d >= first_fill_day]
    pool, held_pnl = 20_000.0, []
    for d, e, x in days:
        idx = first_bar[d]
        held_pnl.append(pool * bars[idx].close / first_fill_price - pool)
    values = [e for _, e, _ in days]
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v); worst = min(worst, v / peak - 1)
    hold_values = [pool + h for h in held_pnl]
    hpeak, hworst = hold_values[0], 0.0
    for v in hold_values:
        hpeak = max(hpeak, v); hworst = min(hworst, v / hpeak - 1)
    out = {
        "bars": len(bars), "days_replayed": len(equity), "first_fill_day": str(first_fill_day),
        "outcomes": dict(outcomes), "trades": len(trades), "first_trades": trades[:5], "last_trades": trades[-3:],
        "sleeve_pnl": round(values[-1] - 100_000.0, 2), "hold_pool_pnl": round(held_pnl[-1], 2),
        "sleeve_max_drawdown_pct_of_account": round(worst * 100, 3),
        "average_exposure_usd": round(sum(x for _, _, x in days) / len(days), 0),
        "max_exposure_usd": round(max(x for _, _, x in days), 0),
        "guardian_refusals": sum(v for k, v in outcomes.items() if k.endswith(":SKIPPED") and "core-sleeve" in k),
    }
    json.dump(out, open(sys.argv[3], "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
