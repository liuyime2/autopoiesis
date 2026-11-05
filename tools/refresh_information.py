#!/usr/bin/env python3
"""Refresh the per-symbol information cache the trading loop reads.

Writes `runtime/autopoiesis/information/<SYMBOL>.json` for each symbol given.
Sources (all measured to work from this host):
  - Yahoo (yfinance): earnings date, consensus EPS, valuation, growth, margins,
    analyst target/recommendation
  - Alpaca: dividends/ex-dates, recent news, options ATM implied volatility

The trading loop never calls the network; it reads these JSON files through
`autopoiesis.information.InformationProvider`. Run this on a timer (see
tools/information-refresh.timer) or by hand:

    python3 tools/refresh_information.py SPY XLB TLT
    python3 tools/refresh_information.py --from-account   # holdings + watchlist

Read-only: it writes only under runtime/autopoiesis/information/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CACHE_DIR = ROOT / "runtime" / "autopoiesis" / "information"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _fetch_yahoo(symbol: str) -> dict:
    import yfinance as yf

    out: dict = {}
    t = yf.Ticker(symbol)

    # Earnings date + consensus
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                out["earnings_date"] = str(ed[0])[:10]
            elif ed is not None:
                out["earnings_date"] = str(ed)[:10]
            eps = cal.get("EPS Estimate")
            if eps is not None:
                out["earnings_consensus_eps"] = float(eps)
    except Exception:
        pass

    # Valuation / growth / margins / analyst
    try:
        info = t.info or {}
    except Exception:
        info = {}
    val = {}
    for k, src in (("pe", "trailingPE"), ("forward_pe", "forwardPE"), ("peg", "pegRatio"), ("price_to_book", "priceToBook")):
        v = info.get(src)
        if isinstance(v, (int, float)):
            val[k] = round(float(v), 2)
    if val:
        out["valuation"] = val
        out["valuation_as_of"] = _now()
    growth = {}
    for k, src in (("revenue_growth", "revenueGrowth"), ("earnings_growth", "earningsQuarterlyGrowth")):
        v = info.get(src)
        if isinstance(v, (int, float)):
            growth[k] = round(float(v), 4)
    if growth:
        out["growth"] = growth
        out["growth_as_of"] = _now()
    margins = {}
    for k, src in (("gross", "grossMargins"), ("operating", "operatingMargins"), ("net", "profitMargins")):
        v = info.get(src)
        if isinstance(v, (int, float)):
            margins[k] = round(float(v), 4)
    if margins:
        out["margins"] = margins
        out["margins_as_of"] = _now()
    target = info.get("targetMeanPrice")
    if isinstance(target, (int, float)):
        out["analyst_target"] = round(float(target), 2)
        out["analyst_target_as_of"] = _now()
    rec = info.get("recommendationKey")
    if rec:
        out["analyst_recommendation"] = str(rec)
        out["analyst_recommendation_as_of"] = _now()
    return out


def _fetch_alpaca(symbol: str, config) -> dict:
    out: dict = {}
    try:
        from autopoiesis.broker_client import make_client
        client = make_client(config)
    except Exception:
        return out

    # Dividends / ex-dates
    try:
        raw = client.get("/corporate-actions", {"symbols": symbol, "types": "dividend", "limit": 5})
        items = (raw or {}).get("corporate_actions", {}).get(symbol, []) if isinstance(raw, dict) else []
        divs = []
        for it in items[:5]:
            divs.append({
                "ex_date": it.get("ex_date") or it.get("exDate"),
                "amount": it.get("cash_amount") or it.get("amount"),
            })
        if divs:
            out["dividends"] = divs
            out["dividends_as_of"] = _now()
    except Exception:
        pass

    # News
    try:
        raw = client.get("/news", {"symbols": symbol, "limit": 5}, api_version="v1beta1")
        items = (raw or {}).get("news") if isinstance(raw, dict) else None
        if isinstance(items, list):
            news = []
            for n in items[:5]:
                news.append({
                    "headline": n.get("headline") or n.get("title"),
                    "summary": (n.get("summary") or "")[:200],
                })
            if news:
                out["news"] = news
                out["news_as_of"] = _now()
    except Exception:
        pass

    # Options ATM implied volatility
    try:
        raw = client.get(f"/options/snapshots/{symbol}", {"limit": 1})
        snaps = (raw or {}).get("snapshots") if isinstance(raw, dict) else None
        if isinstance(snaps, dict) and snaps:
            first = next(iter(snaps.values()))
            greeks = first.get("greeks") if isinstance(first, dict) else None
            iv = greeks.get("implied_volatility") if isinstance(greeks, dict) else None
            if iv is not None:
                out["options_iv"] = round(float(iv), 4)
                out["options_iv_as_of"] = _now()
    except Exception:
        pass

    return out


def refresh_one(symbol: str, config) -> dict:
    data: dict = {"symbol": symbol.upper(), "as_of": _now()}
    data.update(_fetch_yahoo(symbol))
    data.update(_fetch_alpaca(symbol, config))
    # realized vol from the journal's EWMA forecast is not available here; leave
    # it to the trading loop's risk judgment if needed.
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", help="symbols to refresh")
    parser.add_argument("--from-account", action="store_true", help="holdings + configured symbols + sleeve + attention picks")
    args = parser.parse_args()

    from autopoiesis.config import AgentConfig

    config = AgentConfig.from_env()
    symbols = [s.upper() for s in args.symbols]

    if args.from_account:
        # Holdings from the latest journal snapshot + configured symbols.
        try:
            from autopoiesis.journal import JsonlJournal

            recs = JsonlJournal(config.journal_path).read_all()
            if recs:
                for pos in recs[-1].snapshot.positions:
                    symbols.append(pos.symbol.upper())
        except Exception:
            pass
        symbols.extend(s.upper() for s in config.symbols)
        if config.sleeve_symbol:
            symbols.append(config.sleeve_symbol.upper())
        # attention picks are not in the journal; the operator can pass them explicitly.

    symbols = sorted({s for s in symbols if s})

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        data = refresh_one(sym, config)
        path = CACHE_DIR / f"{sym}.json"
        path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"{sym}: {len(data)} fields -> {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
