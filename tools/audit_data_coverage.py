"""Audit how far back Alpaca market data goes for a set of symbols.

Step 2.3 of docs/EXECUTION_PLAN_2026-11-05.md. Writes a JSON report with the
earliest available daily bar and the count of bars for each symbol, for IEX
daily bars and (where available) 5-minute bars.

Usage:
    python3 tools/audit_data_coverage.py [SYMBOL ...]
    python3 tools/audit_data_coverage.py --symbols SPY,QQQ --out runtime/autopoiesis/data_coverage.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.config import AgentConfig


def _load_env() -> None:
    cfg_path = os.environ.get("AUTOPOIESIS_ENV_FILE")
    if cfg_path and Path(cfg_path).exists():
        for line in Path(cfg_path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


def _earliest_bar(client, symbol: str, timeframe: str) -> dict:
    """Find the earliest available bar by probing backwards in chunks."""
    # Start from a generous early date and walk forward until data appears.
    earliest = None
    count = 0
    # Walk forward year by year to find first non-empty window.
    for year in range(2016, date.today().year + 1):
        try:
            start = date(year, 1, 1).isoformat()
            end = date(year, 12, 31).isoformat()
            raw = client.get(
                f"/stocks/{symbol}/bars",
                {"timeframe": timeframe, "start": start, "end": end, "limit": 10000, "feed": "iex"},
            )
            bars = (raw or {}).get("bars") if isinstance(raw, dict) else None
            if isinstance(bars, list) and bars:
                if earliest is None:
                    earliest = bars[0].get("t")
                count += len(bars)
        except Exception as exc:
            return {"error": str(exc)}
    return {"earliest": earliest, "count": count}


def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", help="symbols to audit")
    parser.add_argument("--symbols", dest="sym_list", default=None, help="comma-separated")
    parser.add_argument("--out", default=None, help="output JSON path")
    args = parser.parse_args()

    symbols = list(args.symbols)
    if args.sym_list:
        symbols.extend(s.strip() for s in args.sym_list.split(",") if s.strip())
    if not symbols:
        symbols = ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLF", "XLE"]

    config = AgentConfig.from_env()
    from autopoiesis.broker_client import BrokerClient

    client = BrokerClient(config)

    report = {"symbols": {}}
    for sym in symbols:
        su = sym.upper()
        report["symbols"][su] = {
            "daily": _earliest_bar(client, su, "1Day"),
            "5min": _earliest_bar(client, su, "5Min"),
        }
        print(f"{su}: daily earliest={report['symbols'][su]['daily'].get('earliest')}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(f"wrote {out_path}")
    else:
        print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
