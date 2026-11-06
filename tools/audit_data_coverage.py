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

from autopoiesis.broker_client import make_client
from autopoiesis.config import AgentConfig


def _load_env() -> None:
    cfg_path = os.environ.get("AUTOPOIESIS_ENV_FILE")
    if not cfg_path:
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        cfg_path = os.path.join(xdg, "autopoiesis", "env")
    if cfg_path and Path(cfg_path).exists():
        for line in Path(cfg_path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


def _timeframe(timeframe: str):
    from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit

    units = {"Min": TimeFrameUnit.Minute, "Hour": TimeFrameUnit.Hour, "Day": TimeFrameUnit.Day}
    digits = ""
    for char in timeframe:
        if not char.isdigit():
            break
        digits += char
    if not digits or timeframe[len(digits):] not in units:
        raise ValueError(f"unsupported timeframe {timeframe}")
    return TimeFrame(int(digits), units[timeframe[len(digits):]])


def _earliest_bar(client, symbol: str, timeframe: str) -> dict:
    tf = _timeframe(timeframe)
    earliest = None
    count = 0
    import time as _time

    for year in range(2016, date.today().year + 1):
        try:
            start = date(year, 1, 1).isoformat()
            end = date(year, 12, 31).isoformat()
            bars = client.get_bars(symbol, tf, start, end, feed="iex")
            frame = getattr(bars, "df", None)
            if frame is not None and not frame.empty:
                if earliest is None:
                    earliest = str(frame.index[0])
                count += len(frame)
            _time.sleep(0.5)
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
    client = make_client(config)

    report = {"symbols": {}}
    for sym in symbols:
        su = sym.upper()
        report["symbols"][su] = {
            "daily": _earliest_bar(client, su, "1Day"),
            "5min": _earliest_bar(client, su, "5Min"),
        }
        import time as _time
        _time.sleep(1)
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
