"""Fetch a survivorship-free daily universe of listed US equities into the replay cache.

    python tools/fetch_universe_bars.py --end YYYY-MM-DD [--start 2020-07-27] [--cache DIR] [--exclude-etp]

Every active **and inactive** listed equity the broker knows, so names that were delisted
during the window are in it until their data ends. A panel of today's constituents only would
credit any strategy with never having held a company that failed. Research only: nothing in the
trading path reads this directory.

Written to `runtime/autopoiesis/replay/universe-1Day-adjall/` (not committed): one
`<SYMBOL>.json` per symbol in the cache's `{"bars": [...]}` format, and `manifest.json` with the
asset snapshot, the parameters, per-symbol fingerprints, a digest of the whole set, and what was
dropped and why. Needs network and credentials. A failed batch fails the run; there is no
fallback.

`--exclude-etp` (off by default, for families registered after v6) leaves out active listings
that the Nasdaq Trader symbol directory flags `ETF=Y`, which covers ETFs and ETNs. The broker's
asset record has no such flag. The two directory files are saved verbatim beside the manifest,
and their sha256 enters `set_sha256`. Inactive and acquired names are not classified, because a
current file says who holds a ticker today, not who held it before. Their count is recorded.
Coverage was measured in docs/evidence/universe-etp-identifier-2026-11-24.md. Without the flag,
the set, the manifest fields and the digest are what they were for v5 and v6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

LISTED_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX"})
#: The bars endpoint rejects the CUSIP-like ids some inactive assets carry (e.g. `0029900E0`).
TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
DEFAULT_START = "2020-07-27"  # first IEX daily bar the broker serves
DEFAULT_CACHE = ROOT / "runtime" / "autopoiesis" / "replay" / "universe-1Day-adjall"
BATCH = 100
#: Nasdaq Trader's daily symbol directory: every Nasdaq-listed (`nasdaqlisted`) and other
#: exchange-listed (`otherlisted`: NYSE, NYSE American, Arca, Cboe) security, with an `ETF` column.
ETP_SOURCES = {
    "nasdaqlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "otherlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
ETP_DROP = "active: exchange-traded product (nasdaqtrader ETF=Y)"


def etp_symbols(*directory_texts: str) -> set[str]:
    """Symbols flagged `ETF=Y` in Nasdaq Trader directory files, test issues left out.

    Columns are read by header name, because the two files order them differently. The symbol
    is `Symbol` (nasdaqlisted) or `ACT Symbol` (otherlisted). Both use the broker's `BRK.B`
    notation. A file without the expected header fails loudly rather than classifying nothing.
    """
    out: set[str] = set()
    for text in directory_texts:
        lines = text.splitlines()
        header = lines[0].split("|") if lines else []
        key = "Symbol" if "Symbol" in header else "ACT Symbol"
        if key not in header or "ETF" not in header or "Test Issue" not in header:
            raise ValueError(f"not a Nasdaq Trader symbol directory: {header[:8]}")
        sym, etf, test = header.index(key), header.index("ETF"), header.index("Test Issue")
        for line in lines[1:]:
            if line.startswith("File Creation Time"):
                continue
            row = line.split("|")
            if len(row) == len(header) and row[etf] == "Y" and row[test] != "Y":
                out.add(row[sym].strip())
    return out


def fetch_etp_sources(cache: pathlib.Path) -> tuple[set[str], list[dict]]:
    """Download the directory files into `cache`; return the ETP symbols and each file's record."""
    import urllib.request

    texts, records = [], []
    for name, url in ETP_SOURCES.items():
        request = urllib.request.Request(url, headers={"User-Agent": "autopoiesis-research"})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        path = cache / name
        path.write_bytes(raw)
        path.chmod(0o600)
        text = raw.decode("utf-8", errors="replace")
        trailer = next((l for l in reversed(text.splitlines()) if l.startswith("File Creation Time")), "")
        records.append({"file": name, "url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                        "file_creation_time": trailer.split(":", 1)[1].split("|")[0].strip() if trailer else None})
        texts.append(text)
    return etp_symbols(*texts), records


def select_assets(
    active: Iterable[Mapping], inactive: Iterable[Mapping], etp: set[str] | None = None
) -> tuple[list[dict], Counter]:
    """The listed equities to fetch, and how many were left out for each reason.

    A ticker on both lists has been reused by a later company; its bars under that symbol would
    splice two histories, so only the active listing is kept. With `etp`, an active listing whose
    symbol is in it is left out as an exchange-traded product. Inactive listings are not checked
    against it.
    """
    dropped: Counter = Counter()
    kept: dict[str, dict] = {}
    active_symbols = {str(a.get("symbol")) for a in active}
    for status, assets in (("active", active), ("inactive", inactive)):
        for asset in assets:
            symbol = str(asset.get("symbol", ""))
            if asset.get("class") != "us_equity":
                dropped[f"{status}: not us_equity"] += 1
            elif asset.get("exchange") not in LISTED_EXCHANGES:
                dropped[f"{status}: not a listed exchange"] += 1
            elif not TICKER.match(symbol):
                dropped[f"{status}: ticker not well formed"] += 1
            elif status == "inactive" and symbol in active_symbols:
                dropped["inactive: ticker reused by an active listing"] += 1
            elif etp is not None and status == "active" and symbol in etp:
                dropped[ETP_DROP] += 1
            elif symbol in kept:
                dropped[f"{status}: duplicate listing"] += 1
            else:
                kept[symbol] = {"symbol": symbol, "name": str(asset.get("name", "")),
                                "exchange": str(asset.get("exchange")), "status": status}
    return [kept[s] for s in sorted(kept)], dropped


def acquired_symbols(client, start: str, end: str) -> dict[str, str]:
    """Merger acquirees in the window: symbol -> effective date. Companies that ended.

    Measured 2026-11-21: the broker's asset listing omits recent exits such as ATVI, TWTR,
    PXD and SPLK although their bars exist, so a universe built from it alone had no
    later-delisted member after 2022-10. Cash and stock mergers name the acquiree. Name
    changes are deliberately not read: an old ticker there is the same company under a new
    one, and counting it as an exit would invent a delisting.
    """
    out: dict[str, str] = {}
    first, last = int(start[:4]), int(end[:4])
    for year in range(first, last + 1):
        token = None
        while True:
            query = {"types": "cash_merger,stock_merger", "start": max(start, f"{year}-01-01"),
                     "end": min(end, f"{year}-12-31"), "limit": 1000}
            if token:
                query["page_token"] = token
            page = client.data_get("/corporate-actions", query, api_version="v1beta1")
            actions = page.get("corporate_actions", {})
            for row in actions.get("cash_mergers", []) + actions.get("stock_mergers", []):
                symbol = str(row.get("acquiree_symbol") or "")
                if TICKER.match(symbol):
                    out.setdefault(symbol, str(row.get("effective_date") or ""))
            token = page.get("next_page_token")
            if not token:
                break
    return out


def add_acquirees(
    assets: list[dict], acquirees: Mapping[str, str], active_symbols: set[str], dropped: Counter
) -> tuple[list[dict], int, int]:
    """`assets` plus the acquirees not already in it; returns (assets, added, already present)."""
    present = {a["symbol"] for a in assets}
    added = already = 0
    out = list(assets)
    for symbol, effective in sorted(acquirees.items()):
        if symbol in present:
            already += 1
        elif symbol in active_symbols:
            dropped["acquired: ticker reused by an active listing"] += 1
        else:
            out.append({"symbol": symbol, "name": "", "exchange": "", "status": "acquired",
                        "effective_date": effective})
            added += 1
    return sorted(out, key=lambda a: a["symbol"]), added, already


def _bars_from_frame(frame) -> dict[str, list]:
    from autopoiesis.replay import ReplayBar

    out: dict[str, list] = {}
    if frame is None or len(frame) == 0:
        return out
    for stamp, row in frame.iterrows():
        out.setdefault(str(row["symbol"]), []).append(ReplayBar(
            timestamp=stamp.to_pydatetime().astimezone(timezone.utc),
            open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
            close=float(row["close"]), volume=float(row["volume"]),
        ))
    return out


def get_bars_with_backoff(client, batch, start, end, *, sleep=None, tries=5, wait_s=60.0):
    """`get_bars` for one batch, waiting out the broker's rate limit.

    Measured 2026-11-21: the first full run died after 72 s with `too many requests`. A batch
    of 100 symbols over six years is many pages, and the data API allows 200 requests a
    minute. Only the rate-limit error is retried; anything else, or a rate limit that outlasts
    `tries`, fails the run as before.
    """
    import time

    sleep = sleep or time.sleep
    for attempt in range(tries):
        try:
            return client.get_bars(batch, "1Day", start, end, adjustment="all", feed="iex").df
        except Exception as exc:  # the client raises its own APIError class
            if "too many requests" not in str(exc).lower() or attempt == tries - 1:
                raise
            print(f"  rate limited; waiting {wait_s:.0f}s (attempt {attempt + 1}/{tries})")
            sleep(wait_s)
    raise RuntimeError("unreachable")


def set_digest(entries: Mapping[str, Mapping], etp_sources: Iterable[Mapping] | None = None) -> str:
    """One digest over every symbol's fingerprint, so two fetches can be compared.

    With `etp_sources`, the classification files' sha256 are hashed in too, so a filtered set
    never shares a digest with an unfiltered one. Without it, the digest is unchanged from v5/v6.
    """
    fingerprints = {s: e["fingerprint"] for s, e in sorted(entries.items())}
    if etp_sources is None:
        canonical = json.dumps(fingerprints, sort_keys=True)
    else:
        canonical = json.dumps({"etp_sources": sorted(r["sha256"] for r in etp_sources),
                                "symbols": fingerprints}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", required=True, help="last session to include, YYYY-MM-DD")
    parser.add_argument("--cache", type=pathlib.Path, default=None,
                        help=f"default {DEFAULT_CACHE.name}, or {DEFAULT_CACHE.name}-noetp with --exclude-etp")
    parser.add_argument("--exclude-etp", action="store_true",
                        help="leave out active listings Nasdaq Trader flags ETF=Y (future families only)")
    parser.add_argument("--no-corporate-actions", action="store_true",
                        help="skip merger acquirees (reproduces the 2026-11-21 set)")
    args = parser.parse_args(argv)
    if args.cache is None:
        args.cache = DEFAULT_CACHE.with_name(DEFAULT_CACHE.name + "-noetp") if args.exclude_etp else DEFAULT_CACHE

    from autopoiesis.atomicio import narrow_private, write_json_atomic
    from autopoiesis.replay import save_bars
    from autopoiesis.research.manifest import dataset_fingerprint
    from fetch_replay_bars import client_from_env

    args.cache.mkdir(parents=True, exist_ok=True)
    narrow_private(args.cache, mode=0o700)
    etp, etp_sources = (fetch_etp_sources(args.cache) if args.exclude_etp else (None, None))
    if etp is not None:
        print(f"{len(etp)} exchange-traded products in the Nasdaq Trader directory")

    client = client_from_env()
    active = [a._raw for a in client.list_assets(status="active")]
    inactive = [a._raw for a in client.list_assets(status="inactive")]
    assets, dropped = select_assets(active, inactive, etp)
    acquirees: dict[str, str] = {}
    added = already = 0
    if not args.no_corporate_actions:
        acquirees = acquired_symbols(client, args.start, args.end)
        assets, added, already = add_acquirees(
            assets, acquirees, {str(a.get("symbol")) for a in active}, dropped)
        print(f"{len(acquirees)} merger acquirees: {added} added, {already} already listed")
    symbols = [a["symbol"] for a in assets]
    print(f"{len(symbols)} listed equities to fetch ({sum(1 for a in assets if a['status'] == 'inactive')} inactive)")

    entries: dict[str, dict] = {}
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        frame = get_bars_with_backoff(client, batch, args.start, args.end)
        for symbol, bars in _bars_from_frame(frame).items():
            path = args.cache / f"{symbol}.json"
            save_bars(path, symbol, bars)
            path.chmod(0o600)
            entries[symbol] = {"bars": len(bars), "first": bars[0].timestamp.date().isoformat(),
                               "last": bars[-1].timestamp.date().isoformat(),
                               "fingerprint": dataset_fingerprint(symbol, bars)}
        print(f"  {min(i + BATCH, len(symbols))}/{len(symbols)} requested, {len(entries)} with bars")

    no_bars = [s for s in symbols if s not in entries]
    manifest = {
        "provenance": "alpaca historical bars (feed iex, adjustment all, 1Day); not synthetic",
        "start": args.start, "end": args.end,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "assets": assets, "dropped": dict(sorted(dropped.items())),
        "requested": len(symbols), "with_bars": len(entries), "without_bars": len(no_bars),
        "inactive_with_bars": sum(1 for a in assets if a["status"] == "inactive" and a["symbol"] in entries),
        "corporate_actions": (None if args.no_corporate_actions else {
            "acquirees_found": len(acquirees), "added": added, "already_listed": already,
            "added_with_bars": sum(1 for a in assets if a["status"] == "acquired" and a["symbol"] in entries)}),
        "symbols": entries, "set_sha256": set_digest(entries, etp_sources),
    }
    if etp_sources is not None:  # absent, not null, without the flag: the v5/v6 manifest is unchanged
        manifest["etp_filter"] = {
            "sources": etp_sources, "excluded": dropped[ETP_DROP],
            "unclassified_non_active": sum(1 for a in assets if a["status"] != "active"),
            "unclassified_non_active_with_bars": sum(1 for a in assets if a["status"] != "active" and a["symbol"] in entries)}
    manifest_path = args.cache / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    manifest_path.chmod(0o600)
    print(f"{len(entries)} symbols with bars, {manifest['inactive_with_bars']} of them inactive; "
          f"set sha256 {manifest['set_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
