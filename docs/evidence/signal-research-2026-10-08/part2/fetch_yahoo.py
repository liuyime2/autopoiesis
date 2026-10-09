"""Long daily history (dividend-adjusted) from Yahoo's public chart endpoint, one pickle.
Usage: fetch_yahoo.py OUT.pkl SYMBOL..."""
import sys, time
import pandas as pd
import requests
H = {"User-Agent": "Mozilla/5.0 (research script; autopoiesis)"}
frames = {}
for s in sys.argv[2:]:
    for attempt in range(3):
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}", params={"period1": 0, "period2": int(time.time()), "interval": "1d", "events": "div,splits"}, headers=H, timeout=40)
            j = r.json()["chart"]["result"][0]
            ts = pd.to_datetime(j["timestamp"], unit="s", utc=True).tz_convert("America/New_York").tz_localize(None).normalize()
            q = j["indicators"]["quote"][0]; adj = j["indicators"]["adjclose"][0]["adjclose"]
            # high and low were missing, which made two of the 六脉神剑 indicators (KDJ and LWR,
            # both stochastic-style and both needing the bar's range) unbuildable on this panel.
            # The chart endpoint returns them; the first version simply did not ask.
            frames[s] = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"],
                                      "close": q["close"], "adjclose": adj, "volume": q["volume"]}, index=ts)
            print(s, len(ts), ts[0].date(), flush=True); break
        except Exception as e:
            time.sleep(2)
    else:
        print(s, "FAILED", flush=True)
pd.concat(frames, axis=1).to_pickle(sys.argv[1]); print("saved", len(frames))
