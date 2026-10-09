"""Daily bars for the research universe, one file. ADJUSTMENT=all (default: splits and dividends), raw, or split."""
import os, sys, time
import pandas as pd
import alpaca_trade_api as tradeapi
api = tradeapi.REST(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], os.environ["ALPACA_BASE_URL"])
syms = sys.argv[2:]
frames = {}
for s in syms:
    for attempt in range(3):
        try:
            df = api.get_bars(s, "1Day", "2016-01-01", "2026-10-07", adjustment=os.environ.get("ADJUSTMENT", "all")).df
            if len(df): frames[s] = df[["open", "high", "low", "close", "volume"]]
            break
        except Exception as e:
            time.sleep(2)
    print(s, len(frames.get(s, [])), flush=True)
panel = pd.concat(frames, axis=1)
panel.index = panel.index.tz_convert("America/New_York").tz_localize(None).normalize()
panel.to_pickle(sys.argv[1])
print("saved", panel.shape, panel.index[0].date(), panel.index[-1].date())
