"""Fetch real historical bars into the replay cache.

Run it:

    python tools/fetch_replay_bars.py [SYMBOL] [START] [END] [INTERVAL]

The bars Alpaca's historical endpoint returns are the whole reason the replay is worth
anything. A replay over invented prices can only show that code runs; a replay over the real
tape can show whether a change would have traded. So this writes what the broker actually has,
records that provenance in the file, and never falls back to a generator - if the fetch fails,
it fails.

Written to `runtime/min_agent/replay/`, which is not committed. On a fresh clone run this once,
with credentials, and the replay and the `self-evolution-closes` gate become runnable. It needs
network and credentials; nothing else in the gate does.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

DEFAULT_SYMBOL = "SPY"
DEFAULT_START = "2026-09-28T13:30:00Z"
DEFAULT_END = "2026-10-02T20:00:00Z"
DEFAULT_INTERVAL = "5Min"
OUT_DIR = pathlib.Path("runtime/min_agent/replay")


def cache_path(symbol: str, start: str, end: str) -> pathlib.Path:
    def day(stamp: str) -> str:
        return stamp[:10]
    return OUT_DIR / f"{symbol.upper()}_{DEFAULT_INTERVAL}_{day(start)}_{day(end)}.json"


def client_from_env():
    """The broker client, with credentials loaded the one documented way."""
    from pathlib import Path

    import alpaca_trade_api as tradeapi

    env_file = Path(
        os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ) / "min-agent" / "env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise SystemExit(
            "no Alpaca credentials; they live in "
            "${XDG_CONFIG_HOME:-$HOME/.config}/min-agent/env and not in this repository"
        )
    return tradeapi.REST(key, secret, "https://paper-api.alpaca.markets")


def main(argv: list[str]) -> int:
    symbol = argv[1] if len(argv) > 1 else DEFAULT_SYMBOL
    start = argv[2] if len(argv) > 2 else DEFAULT_START
    end = argv[3] if len(argv) > 3 else DEFAULT_END
    interval = argv[4] if len(argv) > 4 else DEFAULT_INTERVAL

    from min_agent.replay import fetch_bars, save_bars

    client = client_from_env()
    bars = fetch_bars(symbol, start, end, client=client, interval=interval)
    if not bars:
        print(f"no bars returned for {symbol} between {start} and {end}")
        return 1

    path = cache_path(symbol, start, end)
    save_bars(path, symbol, bars)
    closes = [bar.close for bar in bars]
    print(f"cached {len(bars)} real {interval} {symbol} bars -> {path}")
    print(f"  {bars[0].timestamp} .. {bars[-1].timestamp}")
    print(f"  close {closes[0]:.2f} .. {closes[-1]:.2f}   high {max(b.high for b in bars):.2f}"
          f"   low {min(b.low for b in bars):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
