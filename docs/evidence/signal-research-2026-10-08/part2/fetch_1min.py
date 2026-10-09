"""Regular-session 1-minute bars from Alpaca, one pickle of closes/opens/volume per symbol.
Usage: fetch_1min.py OUT.pkl START END SYMBOL..."""
import os, sys
import pandas as pd
import alpaca_trade_api as tradeapi
api = tradeapi.REST(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], os.environ["ALPACA_BASE_URL"])
frames = {}
for s in sys.argv[4:]:
    df = api.get_bars(s, "1Min", sys.argv[2], sys.argv[3], adjustment="all").df
    df.index = df.index.tz_convert("America/New_York")
    df = df.between_time("09:30", "15:59")[["open", "high", "low", "close", "volume"]]
    frames[s] = df; print(s, len(df), df.index[0], df.index[-1], flush=True)
pd.concat(frames, axis=1).to_pickle(sys.argv[1]); print("saved")
