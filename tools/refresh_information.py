#!/usr/bin/env python3
"""Refresh the per-symbol information cache the trading loop reads.

Writes `<cache-dir>/<SYMBOL>.json` (default `runtime/autopoiesis/information/`). Sources:
  - Yahoo (yfinance): earnings date, consensus EPS, valuation, growth, margins, analyst target
  - Alpaca *data* API: cash dividends / ex-dates, and ~30-day at-the-money implied volatility
    from the options snapshots (`data.alpaca.markets/v1beta1`, feed=indicative)

News is not cached here: the trading loop already reads it live through `data_gateway`.

Every source records its own status under `sources`: `ok`, `empty` (the call worked and
returned nothing) or `error` (with the exception type and message). A failing source never
stops the others and is never silent: before 2026-11-07 every Alpaca call here failed
(wrong host, wrong client method) behind a bare `except: pass`, and the cache held only P/E and
P/B for a week.

The trading loop never calls the network; it reads these files through
`autopoiesis.information.InformationProvider`.

    python3 tools/refresh_information.py SPY XLB TLT
    python3 tools/refresh_information.py --from-account   # holdings, configured, served, attention
    python3 tools/refresh_information.py --cache-dir /tmp/scratch SPY   # leaves runtime untouched
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

DEFAULT_CACHE_DIR = ROOT / "runtime" / "autopoiesis" / "information"
DATA_HOST = "https://data.alpaca.markets"
#: Expiry window around 30 days and the strike band around spot used to pick the at-the-money call.
IV_TARGET_DAYS = 30
IV_WINDOW_DAYS = (23, 37)
IV_STRIKE_BAND = 0.03

#: Exceptions a source may raise on a bad network, a changed schema or an absent package. Anything
#: else is a bug in this tool and should crash it rather than be recorded as a source failure.
SOURCE_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, ImportError)

HttpGet = Callable[[str, dict], dict]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(status: str, detail: str = "") -> dict:
    return {"status": status, "detail": detail, "fetched_at": _now()}


# ----------------------------------------------------------------------------------- sources
def fetch_yahoo(symbol: str) -> tuple[dict, dict]:
    """(fields, status) from yfinance. Fields carry their own `<key>_as_of`."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        calendar = ticker.calendar
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Yahoo has no company summary for funds: measured 2026-11-07, SPY, TLT and XLE all
            # answer 404. That is "nothing to say", not a failing source.
            return {}, _status("empty", "yahoo has no company data for this symbol (HTTP 404; typical of an ETF)")
        return {}, _status("error", f"HTTPError: HTTP {exc.code}")
    except SOURCE_ERRORS as exc:
        return {}, _status("error", f"{type(exc).__name__}: {exc}")

    out: dict = {}
    now = _now()
    if isinstance(calendar, dict):
        earnings = calendar.get("Earnings Date")
        if isinstance(earnings, list) and earnings:
            earnings = earnings[0]
        if earnings is not None:
            out["earnings_date"] = str(earnings)[:10]
            out["earnings_date_as_of"] = now
        eps = calendar.get("EPS Estimate")
        if isinstance(eps, (int, float)):
            out["earnings_consensus_eps"] = float(eps)
            out["earnings_consensus_eps_as_of"] = now

    def group(name: str, mapping: tuple[tuple[str, str], ...], digits: int) -> None:
        values = {k: round(float(info[src]), digits) for k, src in mapping if isinstance(info.get(src), (int, float))}
        if values:
            out[name] = values
            out[f"{name}_as_of"] = now

    group("valuation", (("pe", "trailingPE"), ("forward_pe", "forwardPE"), ("peg", "pegRatio"), ("price_to_book", "priceToBook")), 2)
    group("growth", (("revenue_growth", "revenueGrowth"), ("earnings_growth", "earningsQuarterlyGrowth")), 4)
    group("margins", (("gross", "grossMargins"), ("operating", "operatingMargins"), ("net", "profitMargins")), 4)
    if isinstance(info.get("targetMeanPrice"), (int, float)):
        out["analyst_target"] = round(float(info["targetMeanPrice"]), 2)
        out["analyst_target_as_of"] = now
    if info.get("recommendationKey"):
        out["analyst_recommendation"] = str(info["recommendationKey"])
        out["analyst_recommendation_as_of"] = now
    if not out:
        return {}, _status("empty", "yfinance returned no usable fields (an ETF has no earnings or margins)")
    return out, _status("ok", ", ".join(sorted(k for k in out if not k.endswith("_as_of"))))


def fetch_dividends(symbol: str, fetch: Callable[[str], dict[str, float]] | None = None) -> tuple[dict, dict]:
    """Cash dividends of the last year from the Alpaca data API (reuses tools/refresh_dividends)."""
    if fetch is None:
        import refresh_dividends

        fetch = refresh_dividends._fetch
    try:
        rates = fetch(symbol)
    except SOURCE_ERRORS as exc:
        return {}, _status("error", f"{type(exc).__name__}: {exc}")
    if not rates:
        return {}, _status("empty", "no cash dividends in the lookback window")
    rows = [{"ex_date": ex, "amount": amount} for ex, amount in sorted(rates.items())][-5:]
    out: dict = {"dividends": rows, "dividends_as_of": _now()}
    upcoming = [r["ex_date"] for r in rows if r["ex_date"] >= date.today().isoformat()]
    if upcoming:
        out["ex_dividend_date"] = upcoming[0]
        out["ex_dividend_date_as_of"] = out["dividends_as_of"]
    return out, _status("ok", f"{len(rows)} dividend(s), latest ex {rows[-1]['ex_date']}")


def pick_atm_call(snapshots: dict, spot: float, today: date) -> dict | None:
    """The call with expiry nearest 30 days and strike nearest spot, or None if none carries an IV.

    The contract symbol is OCC: ROOT + YYMMDD + C/P + strike*1000 (8 digits).
    """
    best: tuple[float, float, dict] | None = None
    for contract, snap in snapshots.items():
        iv = snap.get("impliedVolatility") if isinstance(snap, dict) else None
        if not isinstance(iv, (int, float)) or iv <= 0 or len(contract) < 16:
            continue
        tail = contract[-15:]
        try:
            expiry = datetime.strptime(tail[:6], "%y%m%d").date()
            strike = int(tail[7:]) / 1000.0
        except ValueError:
            continue
        if tail[6] != "C":
            continue
        dte = (expiry - today).days
        key = (abs(dte - IV_TARGET_DAYS), abs(strike - spot))
        if best is None or key < best[:2]:
            best = (*key, {"contract": contract, "iv": round(float(iv), 4), "dte": dte, "strike": strike, "spot": spot})
    return best[2] if best else None


def fetch_options_iv(symbol: str, http_get: HttpGet, today: date | None = None) -> tuple[dict, dict]:
    """~30-day at-the-money call implied volatility from the indicative options snapshots."""
    today = today or date.today()
    try:
        trade = http_get(f"v2/stocks/{symbol}/trades/latest", {})
        spot = float(trade["trade"]["p"])
        raw = http_get(
            f"v1beta1/options/snapshots/{symbol}",
            {
                "feed": "indicative", "type": "call", "limit": 1000,
                "expiration_date_gte": (today + timedelta(days=IV_WINDOW_DAYS[0])).isoformat(),
                "expiration_date_lte": (today + timedelta(days=IV_WINDOW_DAYS[1])).isoformat(),
                "strike_price_gte": round(spot * (1 - IV_STRIKE_BAND), 2),
                "strike_price_lte": round(spot * (1 + IV_STRIKE_BAND), 2),
            },
        )
    except SOURCE_ERRORS as exc:
        return {}, _status("error", f"{type(exc).__name__}: {exc}")
    snapshots = raw.get("snapshots") if isinstance(raw, dict) else None
    if not isinstance(snapshots, dict) or not snapshots:
        return {}, _status("empty", f"no listed calls within {IV_WINDOW_DAYS[0]}-{IV_WINDOW_DAYS[1]} days of {symbol} +-{IV_STRIKE_BAND:.0%}")
    chosen = pick_atm_call(snapshots, spot, today)
    if chosen is None:
        return {}, _status("empty", f"{len(snapshots)} contract(s) returned, none with an implied volatility")
    now = _now()
    out = {"options_iv": chosen["iv"], "options_iv_as_of": now, "options_iv_contract": chosen}
    truncated = " (page truncated)" if isinstance(raw, dict) and raw.get("next_page_token") else ""
    return out, _status("ok", f"{chosen['contract']} dte={chosen['dte']}{truncated}")


def http_get_data_api(path: str, params: dict) -> dict:
    """GET on the Alpaca data host with the paper credentials from the environment."""
    import requests

    key, secret = os.environ.get("ALPACA_API_KEY", ""), os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be in the environment")
    response = requests.get(
        f"{DATA_HOST}/{path}", params=params, timeout=30,
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
    )
    response.raise_for_status()
    return response.json()


# ------------------------------------------------------------------------------------- driver
def refresh_one(
    symbol: str, *, http_get: HttpGet | None = None, yahoo=fetch_yahoo, dividends=fetch_dividends,
) -> dict:
    now = _now()
    data: dict = {"symbol": symbol.upper(), "as_of": now, "available_at": now, "sources": {}}
    http_get = http_get or http_get_data_api
    for name, call in (
        ("yahoo", lambda: yahoo(symbol)),
        ("dividends", lambda: dividends(symbol)),
        ("options_iv", lambda: fetch_options_iv(symbol, http_get)),
    ):
        fields, status = call()
        data.update(fields)
        data["sources"][name] = status
    return data


def symbols_from_account(config) -> list[str]:
    """Holdings, configured, sleeve, symbols served by live strategies, recent attention picks."""
    from autopoiesis.journal import JsonlJournal
    from autopoiesis.strategy_engine import StrategyLibrary

    symbols: list[str] = [s.upper() for s in config.symbols]
    if config.sleeve_symbol:
        symbols.append(config.sleeve_symbol.upper())
    journal = JsonlJournal(config.journal_path)
    records = journal.last_n(1)
    if records:
        symbols += [p.symbol.upper() for p in records[-1].snapshot.positions]
    for event in journal.read_events("ATTENTION_SELECTED", limit=3):
        symbols += [str(s).upper() for s in event.payload.get("symbols", [])]
    for spec in StrategyLibrary(config.journal_path.parent / "strategies").list():
        if spec.enabled and spec.lifecycle not in {"PAUSED", "RETIRED"}:
            symbols += [s.upper() for s in spec.symbols]
    return symbols


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="*", help="symbols to refresh")
    parser.add_argument("--from-account", action="store_true", help="holdings + configured + sleeve + served + attention picks")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="where to write (default: the runtime cache)")
    args = parser.parse_args(argv)

    from autopoiesis.atomicio import write_text_atomic
    from autopoiesis.config import AgentConfig

    config = AgentConfig.from_env()
    symbols = [s.upper() for s in args.symbols]
    if args.from_account:
        symbols += symbols_from_account(config)
    symbols = sorted({s for s in symbols if s})

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for symbol in symbols:
        data = refresh_one(symbol)
        write_text_atomic(cache_dir / f"{symbol}.json", json.dumps(data, indent=2, default=str) + "\n")
        states = {name: s["status"] for name, s in data["sources"].items()}
        failed += sum(1 for v in states.values() if v == "error")
        print(f"{symbol}: {states}")
    print(f"{len(symbols)} symbol(s) written to {cache_dir}; {failed} source error(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
