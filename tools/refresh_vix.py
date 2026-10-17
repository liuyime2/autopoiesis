#!/usr/bin/env python3
"""Refresh the VIX cache from Yahoo's public chart endpoint.

Why this exists. `risk_judgment.vix_floor` refuses a reading more than
`VIX_STALE_AFTER_DAYS` old, correctly - and on 2026-10-15 the cache's last point was
2026-10-09, so the floor had been disengaged since 2026-10-13. Nothing refreshed it on a
schedule: the research path that populated it is run by hand. P6 measured the floor as a
12.4% forecast-error improvement, and it was contributing nothing for two days.

The doctor now warns when the reading is stale. This is the other half - the refresh that
keeps it from going stale in the first place.

**Merges rather than replaces.** The cache carries 9,263 points back to 1990-01-02 and the
chart endpoint returns a bounded window; writing what the endpoint returned would throw
away the history every run, and the history is what the six-year quality window reads.

Read-only with respect to the account: one HTTP GET to a public endpoint, no broker, no
credentials, no order.

Usage:
    python tools/refresh_vix.py                 # refresh in place
    python tools/refresh_vix.py --check         # report freshness and exit non-zero if stale
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runtime" / "autopoiesis" / "replay" / "VIX.pkl"

#: The symbols the cache carries. `^VIX3M` is the 3-month index, used by the research path
#: as a term-structure check; refreshing it costs one more request.
SYMBOLS = ("^VIX", "^VIX3M")

ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (autopoiesis; vix refresh)"}


def _fetch(symbol: str, *, timeout: int = 40) -> dict[str, float]:
    """Daily closes for `symbol`, keyed by ISO date. Raises on anything unusable."""
    import requests

    response = requests.get(
        ENDPOINT.format(symbol=symbol.replace("^", "%5E")),
        params={"period1": 0, "period2": int(time.time()), "interval": "1d"},
        headers=HEADERS, timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out: dict[str, float] = {}
    for stamp, close in zip(stamps, closes, strict=False):
        if close is None:
            continue  # a holiday row arrives with nulls; it is not a reading
        day = time.strftime("%Y-%m-%d", time.gmtime(stamp))
        out[day] = float(close)
    return out


def _load() -> dict:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def refresh(*, symbols=SYMBOLS) -> dict[str, int]:
    """Merge the endpoint's series into the cache. Returns added points per symbol."""
    existing = _load()
    added: dict[str, int] = {}
    for symbol in symbols:
        fresh = _fetch(symbol)
        merged = dict(existing.get(symbol) or {})
        before = len(merged)
        merged.update(fresh)
        existing[symbol] = dict(sorted(merged.items()))
        added[symbol] = len(merged) - before
        print(f"  {symbol}: {len(fresh)} fetched, {added[symbol]} new, "
              f"{len(merged)} total, last {max(merged) if merged else 'none'}", flush=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(existing), encoding="utf-8")
    print(f"saved {CACHE}")
    return added


def check() -> int:
    """Report freshness against the same ceiling the floor uses. Non-zero when stale."""
    from autopoiesis.risk_judgment import VIX_STALE_AFTER_DAYS

    if not CACHE.exists():
        print(f"STALE: no cache at {CACHE}")
        return 1
    history = (_load().get("^VIX") or {})
    if not history:
        print("STALE: cache holds no ^VIX points")
        return 1
    last = max(history)
    age = (date.today() - date.fromisoformat(last)).days
    if age > VIX_STALE_AFTER_DAYS:
        print(f"STALE: last point {last}, {age} days old, ceiling {VIX_STALE_AFTER_DAYS}")
        return 1
    print(f"FRESH: last point {last}, {age} day(s) old, ceiling {VIX_STALE_AFTER_DAYS}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report freshness and exit non-zero when stale")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    try:
        refresh()
    except Exception as exc:  # a refresh that fails must not corrupt the cache
        print(f"REFRESH FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
