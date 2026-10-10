"""Stage the fundamentals the broker does not carry, from free sources, into a cache.

**What this is for.** The live decision context had three named holes, each left `None` with the
reason rather than filled by something invented: no sector or industry, no next earnings date, and no
earnings surprise. The last one needed a consensus estimate, and neither this broker nor the SEC
publishes one - so `check_instruments.py` reported `eps_surprise_pct: None` forever.

Yahoo Finance carries all three. Measured on this account, for INTC: sector `Technology`,
industry `Semiconductors`, market cap $566bn, 62.6% institutional ownership; four forward estimate
windows with analyst counts; and an earnings history with `EPS Estimate`, `Reported EPS` and
`Surprise(%)` - 2026-07-23 estimate 0.22, reported 0.42, surprise +94.59% - plus the next report
date, 2026-10-29, which is still in the future and therefore *knowable*.

**Why a cache and not a runtime dependency.** `yfinance` pulls in `pandas`, `curl_cffi`, and a
`websockets` version that conflicts with `alpaca-trade-api`'s `websockets<11` constraint. The
production path has three runtime dependencies and that is a real asset - it is why a fresh clone
installs in seconds and why `run-fresh-clone.sh` passes with no network. So this fetcher runs on the
research side, writes one file, and the live system reads the file. The same discipline the replay
bars and the research panels already follow.

**And it is not a signal.** Nothing here predicts direction. Sector and industry let the agent say
which comparison a return is meaningless without; the earnings date lets it know when it will be
holding through an event; the surprise is history. What each of those *does* to a decision is a
hypothesis for the signal gate, not a fact this module asserts.

Usage:
    python stage_fundamentals.py OUT.json SYMBOL...
"""

from __future__ import annotations

import json
import statistics
import sys
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

#: What is kept per symbol. Deliberately small: a cache that grows a hundred fields per symbol
#: becomes its own maintenance problem, and every field here has a stated use.
KEYS = (
    "shortName", "sector", "industry", "marketCap", "sharesOutstanding", "floatShares",
    "heldPercentInstitutions", "trailingPE", "forwardPE", "dividendYield", "beta",
    "earningsQuarterlyGrowth", "revenueGrowth", "returnOnEquity", "debtToEquity",
)


def fetch(symbol: str) -> dict:
    """One symbol's fundamentals, or the reason it could not be read.

    Never raises. A symbol Yahoo does not cover records `error` and nothing else, so a gap is
    visible rather than silently absent from the cache.
    """
    import yfinance as yf

    out: dict = {"symbol": symbol.upper(), "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    for key in KEYS:
        value = info.get(key)
        if value is not None:
            out[key] = value

    # Forward consensus: the estimate itself, and how many analysts are behind it. The count
    # matters - an average over two analysts is not the same object as one over forty, and the
    # record must say which it was.
    estimates = {}
    try:
        frame = ticker.earnings_estimate
        if frame is not None and len(frame):
            for period in frame.index:
                row = frame.loc[period]
                estimates[str(period)] = {
                    "avg": _number(row.get("avg")),
                    "low": _number(row.get("low")),
                    "high": _number(row.get("high")),
                    "analysts": _number(row.get("numberOfAnalysts")),
                }
    except Exception as exc:
        out["estimate_error"] = f"{type(exc).__name__}: {exc}"
    if estimates:
        out["estimates"] = estimates

    # Earnings history: what was expected, what happened, and the surprise the two imply. This is
    # the field the broker and the SEC both lack.
    events = []
    try:
        frame = ticker.earnings_dates
        if frame is not None and len(frame):
            for when, row in frame.iterrows():
                estimate = _number(row.get("EPS Estimate"))
                reported = _number(row.get("Reported EPS"))
                events.append(
                    {
                        "date": str(when)[:10],
                        "estimate": estimate,
                        "reported": reported,
                        "surprise_pct": _number(row.get("Surprise(%)")),
                        "upcoming": reported is None,
                    }
                )
    except Exception as exc:
        out["history_error"] = f"{type(exc).__name__}: {exc}"
    events.sort(key=lambda e: e["date"], reverse=True)
    out["earnings_events"] = events[:12]

    # The next report date. Read off the events rather than from a field that may not exist: an
    # event with no `reported` and a date at or after today is the one that has not happened.
    today = datetime.now(timezone.utc).date().isoformat()
    upcoming = [e for e in out["earnings_events"] if e["upcoming"] and e["date"] >= today]
    upcoming.sort(key=lambda e: e["date"])
    out["next_earnings_date"] = upcoming[0]["date"] if upcoming else None

    # --- whether the earnings history is worth anything at all -----------------------------
    #
    # Measured 2026-10-09, and it is a trap rather than a detail: `yf.Ticker(s).earnings_dates`
    # for INTC returned 2026-07-23 and a future 2026-10-29 in one call, and twelve rows ending
    # 2025-04-24 in the next. It is a bounded window whose contents vary between calls, so a
    # symbol whose newest event is eighteen months old looks exactly like one that reports rarely.
    # `next_earnings_date` is None in that case, which is honest - but a silent None is
    # indistinguishable from "reports on no schedule", so the staleness is recorded beside it.
    if out["earnings_events"]:
        newest = max(e["date"] for e in out["earnings_events"])
        age_days = (datetime.now(timezone.utc).date() - date.fromisoformat(newest)).days
        out["earnings_history_age_days"] = age_days
        out["earnings_history_stale"] = age_days > 120
    else:
        out["earnings_history_age_days"] = None
        out["earnings_history_stale"] = True

    # The cadence, from the intervals between the last few reports. Stated as a cadence and not as
    # a date, because that is what the data supports: a free source that reliably publishes *future*
    # earnings dates was not found, and inventing one from the cadence while calling it a date is
    # the kind of figure that gets retracted. A consumer can say "roughly every N days", which is
    # both true and actionable for holding through an event.
    past = sorted(e["date"] for e in out["earnings_events"] if not e["upcoming"])
    intervals = [
        (date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(past, past[1:], strict=False)
    ]
    intervals = [i for i in intervals if 0 < i < 400]
    out["earnings_cadence_days"] = round(statistics.median(intervals)) if len(intervals) >= 2 else None
    # The most recent surprise, which is history and is labelled as such.
    past = [e for e in out["earnings_events"] if e["reported"] is not None]
    past.sort(key=lambda e: e["date"], reverse=True)
    if past and past[0]["surprise_pct"] is not None:
        out["last_earnings_surprise_pct"] = past[0]["surprise_pct"]
        out["last_earnings_date"] = past[0]["date"]
    return out


def _number(value):
    try:
        if value is None or value != value:  # NaN
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def main(argv: list[str]) -> int:
    """`argv` is `sys.argv`, so `argv[0]` is the program name and `argv[1]` the output path.

    Passing `sys.argv[1:]` here and then indexing `argv[1]` would be off by one, and the symptom is
    not a crash: the output lands in a file named after the first symbol. The first version of this
    did exactly that, and it wrote its cache to `./INTC` while reporting "saved 6 symbol(s)".
    """
    if len(argv) < 4:
        print(__doc__)
        return 2
    out_path = Path(argv[1])
    symbols = argv[2:]
    cache: dict = {}
    if out_path.exists():
        try:
            cache = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    for index, symbol in enumerate(symbols, start=1):
        row = fetch(symbol)
        cache[symbol.upper()] = row
        flag = "err" if row.get("error") else (
            "ok" if row.get("sector") else "no-sector")
        print(f"{index}/{len(symbols)} {symbol:6} {flag:9} "
              f"next_earnings={row.get('next_earnings_date')} "
              f"last_surprise={row.get('last_earnings_surprise_pct')}", flush=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"saved {len(cache)} symbol(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
