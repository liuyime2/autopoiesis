"""Run the whole self-evolution loop over real bars, in an isolated account.

`TradingLoop` is only the trading half. The evolution half - reflect, score every decision
against what the market actually did next, screen the strategy on those scores, and let the
lifecycle rules rule on the verdict - runs in `AgentDaemon._maintenance`. Driving only the
loop and then calling `StrategyLifecycleManager.review()` by hand proves the rules work; it
does not prove the loop closes. This drives the daemon, so every stage has to fire on its own
for anything to come out the other end.

Run it:

    python tools/replay_self_evolution.py

It is wired into `make verify` as `self-evolution-closes`, because a claim that the
self-evolution loop closes should be re-runnable rather than asserted from a session. It needs
no network and no credentials: the bars come from the cache written by
`tools/fetch_replay_bars.py`, and the account is simulated.

**What it can and cannot show.** It shows that selection pressure reacts to scored evidence:
a strategy trades, its decisions are scored against real future bars, the screen judges them,
and the rules retire it with the numbers in the reason. It shows nothing about whether any
strategy has an edge. Replay fills are `REPLAYED`, never `SUBMITTED`, and a replayed
retirement is not a real one - the first real `PASS_SCREENED` has to come from live paper
cycles over multiple trading days.

Two findings this harness produced, both of which had been invisible before it existed:

- The Guardian refused all 847 cycles as `data snapshot is stale` until `TradingLoop` grew an
  injectable clock. Relaxing staleness instead would have produced a harness that trades and
  proves nothing.
- `ReplayDataGateway.current` raised `IndexError` when advanced past the last bar, because the
  daemon's sleep hook advances between cycles and runs maintenance before checking whether it
  is finished. `snapshot` clamped; `current` did not.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

#: Derived from the fetcher rather than written out, because a hardcoded filename is a
#: dangling reference the moment someone changes the interval or the window - and the first
#: version of this pointed at `SPY_5min_...` while the tool wrote `SPY_5Min_...`.
import fetch_replay_bars as _fetcher
from min_agent.config import AgentConfig
from min_agent.daemon import AgentDaemon
from min_agent.guardian import Guardian
from min_agent.journal import JsonlJournal
from min_agent.loop import TradingLoop
from min_agent.models import StrategySpec, TradeDecision
from min_agent.reflection_memory import ReflectionMemory
from min_agent.replay import (
    ReplayBook,
    ReplayDataGateway,
    ReplayExecutor,
    load_bars,
)
from min_agent.strategy_engine import StrategyLibrary, StrategyLifecycleManager

BARS = str(
    _fetcher.cache_path(
        _fetcher.DEFAULT_SYMBOL, _fetcher.DEFAULT_START, _fetcher.DEFAULT_END
    )
)
STRATEGY_ID = "replay-momentum-001"


class MomentumEngine:
    """A decision engine whose answers depend on the real bars.

    Not the LLM, and not a stub: a replayed system that always HOLDs would score every
    decision as a good hold and never exercise the branch that retires a strategy, which is
    the branch this exists to reach. The real engine costs ~118s per call, so 847 cycles of it
    is not a thing anyone runs on purpose.

    The real Guardian is used unchanged. A replay with its own softer risk layer would be
    testing a system nobody ships.
    """

    def __init__(self, gateway: ReplayDataGateway, strategy_id: str):
        self.gateway = gateway
        self.strategy_id = strategy_id

    def decide(self, context: dict) -> TradeDecision:
        index = self.gateway.cursor
        closes = [self.gateway.bars[i].close for i in range(max(0, index - 3), index + 1)]
        context = {**context, "recent_closes": closes}
        if len(closes) >= 3:
            if closes[-1] > closes[-3]:
                action, quantity, why = "BUY", 1, "three-bar momentum up"
            elif closes[-1] < closes[-3]:
                action, quantity, why = "SELL", 1, "three-bar momentum down"
            else:
                action, quantity, why = "HOLD", 0, "flat"
        else:
            action, quantity, why = "HOLD", 0, "not enough history"
        return TradeDecision(
            symbol="SPY", action=action, quantity=quantity, confidence=0.6,
            rationale=why, strategy_id=self.strategy_id,
        )


class AlwaysOpen:
    """A scheduler that never says the market is closed."""

    def should_trade_now(self) -> bool:
        return True

    def sleep_seconds(self, **_kwargs) -> int:
        return 0


def run(bars_path: str = BARS) -> dict:
    bars = load_bars(bars_path)
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="replay-self-evolution-"))
    base = AgentConfig.from_env()
    config = AgentConfig(
        mode="paper", symbols=("SPY",), allowlist=base.allowlist,
        alpaca_api_key="", alpaca_secret_key="", alpaca_base_url=base.alpaca_base_url,
        ollama_base_url=base.ollama_base_url, model=base.model, gpu_devices=base.gpu_devices,
        max_position_value=base.max_position_value, max_daily_loss=base.max_daily_loss,
        max_total_exposure=base.max_total_exposure,
        journal_path=workdir / "journal.jsonl", strategy_dir=workdir / "strategies",
        heartbeat_path=workdir / "heartbeat.json", pidfile_path=workdir / "daemon.pid",
    )

    journal = JsonlJournal(config.journal_path)
    library = StrategyLibrary(config.strategy_dir)
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars, book=book)
    library.save(
        StrategySpec(
            strategy_id=STRATEGY_ID, name="replay momentum", kind="FIXED_SIZE",
            symbols=("SPY",),
            parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
            max_position_value=config.max_position_value, enabled=True,
            lifecycle="PROBATION", created_at=bars[0].timestamp,
            rationale="three-bar momentum, replayed over real bars",
        )
    )

    loop = TradingLoop(
        data_gateway=gateway,
        decision_engine=MomentumEngine(gateway, STRATEGY_ID),
        guardian=Guardian(
            allowlist=config.allowlist,
            max_position_value=config.max_position_value,
            max_daily_loss=config.max_daily_loss,
        ),
        executor=ReplayExecutor(gateway=gateway, book=book),
        journal=journal, mode=config.mode,
        now=lambda: gateway.current.timestamp,
    )

    daemon = AgentDaemon(
        config=config, loop=loop, journal=journal, strategy_library=library,
        reflection_memory=ReflectionMemory(workdir / "reflection.json"),
        lifecycle_manager=StrategyLifecycleManager(),
        scheduler=AlwaysOpen(), sleep=lambda _seconds: gateway.advance(),
        now=lambda: gateway.current.timestamp,
    )
    daemon.run(max_cycles=len(bars))

    events = journal.read_events()
    kinds: dict[str, int] = {}
    for event in events:
        kinds[event.event_type] = kinds.get(event.event_type, 0) + 1
    rulings = [
        {"old": e.payload.get("old_lifecycle"), "new": e.payload.get("new_lifecycle"),
         "reason": str(e.payload.get("reason"))}
        for e in events
        if e.event_type == "STRATEGY_LIFECYCLE_UPDATED"
        and e.payload.get("phase") == "applied"
    ]
    final = library.try_load(STRATEGY_ID)
    screens = [
        e.payload for e in events
        if e.event_type == "OFFLINE_VALIDATION_COMPLETED" and e.strategy_id == STRATEGY_ID
    ]
    return {
        "final_screen": screens[-1] if screens else None,
        "cycles": daemon.cycle_count,
        "errors": daemon.error_count,
        "events": kinds,
        "rulings": rulings,
        "final_lifecycle": final.lifecycle if final else None,
        "final_reason": final.lifecycle_reason if final else None,
        "filled_orders": book.quantity,
    }


def main() -> int:
    if not pathlib.Path(BARS).exists():
        print(f"missing {BARS}; run tools/fetch_replay_bars.py first")
        return 2
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
