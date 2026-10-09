"""One-minute bars, fetched a month at a time (the broker rejects a whole-year 1-minute ask).

The original fetch_1min.py asked for the entire range in one request and the broker retried
forever: 21 months of 1-minute bars is ~500k rows per symbol and the endpoint caps a page. A
month is ~22k rows and answers in about two seconds, so the range is walked month by month and
concatenated.
Usage: fetch_1min_paged.py OUT.pkl START END SYMBOL...
"""
import os
import sys
import time

import pandas as pd
import alpaca_trade_api as tradeapi

api = tradeapi.REST(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], os.environ["ALPACA_BASE_URL"])


def months(start: str, end: str):
    a = pd.Timestamp(start)
    while a < pd.Timestamp(end):
        b = min(a + pd.offsets.MonthBegin(1), pd.Timestamp(end))
        yield a, b
        a = b


frames = {}
for s in sys.argv[4:]:
    parts = []
    for a, b in months(sys.argv[2], sys.argv[3]):
        for attempt in range(4):
            try:
                df = api.get_bars(s, "1Min", a.date().isoformat(), b.date().isoformat(), adjustment="raw").df
                if len(df):
                    parts.append(df)
                break
            except Exception:
                time.sleep(2 + attempt * 3)
        else:
            print(s, a.date(), "FAILED", flush=True)
    if parts:
        d = pd.concat(parts)
        d = d[~d.index.duplicated(keep="first")]
        d.index = d.index.tz_convert("America/New_York")
        frames[s] = d.between_time("09:30", "15:59")[["open", "high", "low", "close", "volume"]]
    print(s, len(frames.get(s, [])), flush=True)
panel = pd.concat(frames, axis=1)
panel.to_pickle(sys.argv[1])
print("saved", panel.shape, panel.index[0], panel.index[-1])
