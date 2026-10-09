"""Fetch real historical bars into the replay cache.

Run it:

    python tools/fetch_replay_bars.py [SYMBOL] [START] [END] [INTERVAL]

The bars Alpaca's historical endpoint returns are the whole reason the replay is worth
anything. A replay over invented prices can only show that code runs; a replay over the real
tape can show whether a change would have traded. So this writes what the broker actually has,
records that provenance in the file, and never falls back to a generator - if the fetch fails,
it fails.

Written to `runtime/autopoiesis/replay/`, which is not committed. On a fresh clone run this once,
with credentials, and the replay and the `self-evolution-closes` gate become runnable. It needs
network and credentials; nothing else in the gate does.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

DEFAULT_SYMBOL = "SPY"
DEFAULT_START = "2026-09-28T13:30:00Z"
DEFAULT_END = "2026-10-02T20:00:00Z"
DEFAULT_INTERVAL = "5Min"
OUT_DIR = pathlib.Path("runtime/autopoiesis/replay")


#: Where the rolling fetch writes. Deliberately not derived from the window requested.
#:
#: Naming the file after the request - the first version did `day(start)_day(end)` - makes a
#: rolling fetch mint a new file every day while the contents stay identical, because the
#: broker's most recent completed session moves far more slowly than the calendar. Measured
#: 2026-10-05, seven files existed after two days of scheduling and **every one ended on the
#: same 2026-10-02 session**; the search merged six of them to reach the 24,162 bars it already
#: had. A year of that is ~365 near-duplicate files the driver re-reads and re-deduplicates on
#: every run. Naming after the data instead of the request makes the rolling fetch overwrite one
#: file, which is what "the fetch extends the cache" was supposed to mean.
#:
#: Dated paths still exist for one-off windows: `replay_self_evolution.py` names a window
#: deliberately and the gate depends on it, so this is an addition rather than a replacement.
ROLLING_CACHE = OUT_DIR / "rolling.json"


def cache_path(symbol: str, start: str, end: str) -> pathlib.Path:
    """A dated cache path, for a window fetched on purpose rather than on a schedule."""
    def day(stamp: str) -> str:
        return stamp[:10]
    return OUT_DIR / f"{symbol.upper()}_{DEFAULT_INTERVAL}_{day(start)}_{day(end)}.json"


def rolling_path(symbol: str, interval: str = DEFAULT_INTERVAL) -> pathlib.Path:
    """The single file the scheduled fetch overwrites. See `ROLLING_CACHE` for why."""
    return OUT_DIR / f"{symbol.upper()}_{interval}_rolling.json"


def client_from_env():
    """The broker client, with credentials loaded the one documented way."""
    from pathlib import Path

    import alpaca_trade_api as tradeapi

    env_file = Path(
        os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ) / "autopoiesis" / "env"
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


def cached_last_bar(symbol: str, interval: str = DEFAULT_INTERVAL) -> datetime | None:
    """The newest timestamp anywhere in the cache, across every file.

    Read through each file's own `bars` rather than trusting filenames, because the filenames
    are exactly what stopped being trustworthy. `None` when the cache holds no usable bars,
    which callers treat as "fetch".
    """
    from autopoiesis.replay import load_bars

    newest: datetime | None = None
    for path in sorted(OUT_DIR.glob("*.json")):
        try:
            bars = load_bars(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for bar in bars:
            if newest is None or bar.timestamp > newest:
                newest = bar.timestamp
    return newest


def _interval_minutes(interval: str) -> int:
    digits = ""
    for char in interval:
        if not char.isdigit():
            break
        digits += char
    if not digits:
        return 5
    return int(digits) if interval.endswith("Min") else int(digits) * 60


def fetch_would_add_nothing(client, cached: datetime | None, interval: str) -> bool | None:
    """Whether a fetch cannot return a bar newer than the one already cached.

    The broker's clock exposes `timestamp` (which is *now*) and `is_open`; it has no field
    naming the last completed session. Comparing the cache against `timestamp` is therefore
    always False once any time has passed, so the skip could never fire. The cutoff is derived
    from the clock instead:

    - **market open**: the newest bar a fetch can return is the last completed one, about one
      interval old. So the cache is current when it already holds a bar at or after
      `now - interval`.
    - **market closed**: no bar can appear before `next_open`, so the newest possible bar is the
      last one of the session that just closed. `next_open - 1 day` is **not** that: it is the
      open of the day before, which lands mid-session and is later than the previous close. The
      cutoff is therefore `next_open - 1 day - 1 day`, i.e. the trading day before `next_open`,
      which is conservative in the safe direction — it can only make the skip *less* likely, and
      the fetch is what actually decides. Measured: with `next_open` Tue 2026-10-06 09:30 EDT and
      the cache ending Fri 2026-10-02 19:55 EDT, `next_open - 1 day` is Mon 09:30 (after the
      cache) and the skip would wrongly fire, while `next_open - 2 days` is Fri 09:30 (before it)
      and the fetch correctly runs.

    `None` means "cannot tell" - an unreadable clock, or a cache with no bars - and the caller
    fetches. Measured 2026-10-05 at 12:29 EDT with the cache ending Friday 2026-10-02 23:55Z:
    the market was open, so `now - 5min` was the cutoff, the cache was behind it, and the fetch
    correctly ran and did return the newer bars.
    """
    try:
        clock = client.get_clock()
    except Exception:
        return None
    now = getattr(clock, "timestamp", None)
    if not isinstance(now, datetime) or cached is None:
        return None

    minutes = _interval_minutes(interval)
    if getattr(clock, "is_open", False):
        cutoff = now - timedelta(minutes=minutes)
    else:
        next_open = getattr(clock, "next_open", None)
        if not isinstance(next_open, datetime):
            return None
        cutoff = next_open - timedelta(days=2)
    return cached >= cutoff


def main(argv: list[str]) -> int:
    symbol = argv[1] if len(argv) > 1 else DEFAULT_SYMBOL
    start = argv[2] if len(argv) > 2 else DEFAULT_START
    end = argv[3] if len(argv) > 3 else DEFAULT_END
    interval = argv[4] if len(argv) > 4 else DEFAULT_INTERVAL
    # The scheduled fetch passes ROLLING; a hand-run window keeps its dated path.
    rolling = len(argv) > 5 and argv[5] == "ROLLING"

    from autopoiesis.replay import fetch_bars, save_bars

    client = client_from_env()

    if rolling:
        cached = cached_last_bar(symbol, interval)
        pointless = fetch_would_add_nothing(client, cached, interval)
        if pointless:
            # Nothing a fetch could return is newer than what is on disk. Skipping is what
            # stops a daily near-duplicate and a log line that reads like progress.
            print(f"cache already current: newest bar {cached.isoformat()}; not fetching")
            return 0

    bars = fetch_bars(symbol, start, end, client=client, interval=interval)
    if not bars:
        # Nothing is written. A refused fetch must not be able to make the cache look fuller
        # than it is; the dated file for a window that failed was written on 2026-10-05 and
        # held the previous session's data.
        print(f"no bars returned for {symbol} between {start} and {end}")
        return 1

    path = rolling_path(symbol, interval) if rolling else cache_path(symbol, start, end)
    save_bars(path, symbol, bars)
    closes = [bar.close for bar in bars]
    print(f"cached {len(bars)} real {interval} {symbol} bars -> {path}")
    print(f"  {bars[0].timestamp} .. {bars[-1].timestamp}")
    print(f"  close {closes[0]:.2f} .. {closes[-1]:.2f}   high {max(b.high for b in bars):.2f}"
          f"   low {min(b.low for b in bars):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
