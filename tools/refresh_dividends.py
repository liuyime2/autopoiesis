#!/usr/bin/env python3
"""Refresh the cash-dividend cache the counterfactual adjusts its price series with.

Why this exists. Measured 2026-10-17: the counterfactual scores a decision against the
change in a symbol's **price**, and on an ex-dividend date the price drops by the
distribution. So a HOLD is scored as a loss and a SELL as a gain, and neither is true of
the total return. The broker's own accounting is correct - a dividend is a cash credit and
the equity already reflects it - but every derived measure that reads a price change as a
return is not.

TLT, XLE, XLF and XLB all distribute monthly, and TLT has been cycled every session since
2026-10-08. The three ex-dates the provider knows about (2026-08-03, 09-01, 10-01) all fall
before that, so **nothing has been mis-scored yet** - measured, not assumed. The next one,
around 2026-11-02, is the first that will bite.

Source, tested rather than assumed:

    GET https://data.alpaca.markets/v1/corporate-actions
        ?symbols=TLT&types=cash_dividend&start=&end=

reached with the existing paper key. Counts from 2026-06-01: TLT 5, XLE 2, XLF 2, XLB 2,
SPY 1, AAPL 1, **BIL 0** - BIL is a T-bill ETF and the provider does not carry its
distributions, so this covers the instruments above and does not cover BIL.

Usage:
    python tools/refresh_dividends.py              # refresh in place
    python tools/refresh_dividends.py --check      # report coverage and exit non-zero if stale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runtime" / "autopoiesis" / "replay" / "DIVIDENDS.pkl"

#: How far back to ask. Long enough that the counterfactual's price series - which is built
#: from retained cycles - is always covered, and short enough that the request stays small.
LOOKBACK_DAYS = 400

#: The instruments this project cycles. Kept explicit rather than derived, because the set
#: a dividend matters for is the set that can be held, not the set that is configured today.
SYMBOLS = ("SPY", "BIL", "TLT", "XLB", "XLE", "XLF")

ENDPOINT = "https://data.alpaca.markets/v1/corporate-actions"


def _credentials() -> tuple[str, str]:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be in the environment")
    return key, secret


def _fetch(symbol: str, *, days: int = LOOKBACK_DAYS, timeout: int = 40) -> dict[str, float]:
    """`{ex_date: rate}` for one symbol. Raises on anything unusable."""
    import requests

    key, secret = _credentials()
    end = date.today()
    start = end - timedelta(days=days)
    response = requests.get(
        ENDPOINT,
        params={
            "symbols": symbol,
            # `cash_dividend` is the type. `dividend` is not one - it returns 400, and a 400
            # was briefly read as "this service does not exist" rather than "that parameter
            # is wrong". The valid set is cash_dividend, forward_split, reverse_split,
            # stock_dividend, spin_off, cash_merger and others.
            "types": "cash_dividend",
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=timeout,
    )
    response.raise_for_status()
    rows = (response.json().get("corporate_actions") or {}).get("cash_dividends") or []
    out: dict[str, float] = {}
    for row in rows:
        ex_date = row.get("ex_date")
        rate = row.get("rate")
        if isinstance(ex_date, str) and rate is not None:
            out[ex_date] = float(rate)
    return out


def _load() -> dict:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def refresh(*, symbols=SYMBOLS) -> dict[str, int]:
    """Merge the provider's dividends into the cache. Returns added dates per symbol."""
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
              f"{len(merged)} total", flush=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(existing), encoding="utf-8")
    print(f"saved {CACHE}")
    return added


def check() -> int:
    """Report coverage. Non-zero when the cache is absent or older than the lookback."""
    data = _load()
    if not data:
        print(f"STALE: no dividend cache at {CACHE}")
        return 1
    missing = [s for s in SYMBOLS if not data.get(s)]
    stale = []
    newest = None
    for symbol, rows in data.items():
        for ex_date in rows:
            if newest is None or ex_date > newest:
                newest = ex_date
    if newest is None:
        print("STALE: cache holds no ex-dates")
        return 1
    age = (date.today() - date.fromisoformat(newest)).days
    if age > 45:
        # Monthly payers, so a gap of more than a month and a half means a refresh was
        # missed. Not a hard ceiling like the VIX one - a quiet stretch is legitimate - but
        # worth saying out loud rather than discovering at the next ex-date.
        stale.append(f"newest ex-date {newest} is {age} days old")
    detail = f"{len(data)} symbol(s), newest ex-date {newest}"
    if missing:
        detail += f"; no records for {', '.join(missing)}"
    if stale:
        print(f"STALE: {detail}; {'; '.join(stale)}")
        return 1
    print(f"FRESH: {detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
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
