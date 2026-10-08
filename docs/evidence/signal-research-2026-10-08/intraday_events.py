"""In-session headlines (2024+) and the 5-minute bars that follow them, for a post-publication drift test.
Usage: intraday_events.py news.json out_events.json N"""
import json, os, random, sys, time
import datetime as dt
from zoneinfo import ZoneInfo
import alpaca_trade_api as t
NY = ZoneInfo("America/New_York"); UTC = dt.timezone.utc
api = t.REST(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], os.environ["ALPACA_BASE_URL"])
import bisect
spy_all = json.load(open("runtime/min_agent/replay/SPY_5Min_2024-01-02_2026-10-08.json"))["bars"]
spy_ts = [dt.datetime.fromisoformat(b["t"]) for b in spy_all]
items = []
for x in json.load(open(sys.argv[1])):
    ts = dt.datetime.fromisoformat(x["t"]).astimezone(NY)
    if ts.date() >= dt.date(2024, 1, 3) and ts.date() <= dt.date(2026, 9, 24) and ts.weekday() < 5 and dt.time(9, 40) <= ts.time() <= dt.time(14, 30):
        items.append((x, ts))
random.Random(5).shuffle(items); items = items[: int(sys.argv[3])]
out = []; t0 = time.time()
for k, (x, ts) in enumerate(items):
    try:
        df = api.get_bars(x["sym"], "5Min", (ts - dt.timedelta(minutes=10)).astimezone(UTC).isoformat(), (ts + dt.timedelta(minutes=125)).astimezone(UTC).isoformat(), adjustment="all").df
        lo = bisect.bisect_left(spy_ts, (ts - dt.timedelta(minutes=10)).astimezone(UTC)); hi = bisect.bisect_right(spy_ts, (ts + dt.timedelta(minutes=125)).astimezone(UTC))
        sp = spy_all[lo:hi]
    except Exception:
        time.sleep(1); continue
    if len(df) < 15 or len(sp) < 15: continue
    f = lambda d: [(i.tz_convert(NY).isoformat(), float(r.open), float(r.close)) for i, r in d.iterrows()]
    g = lambda d: [(dt.datetime.fromisoformat(b["t"]).astimezone(NY).isoformat(), float(b["o"]), float(b["c"])) for b in d]
    out.append(dict(id=x["id"], sym=x["sym"], t=str(ts), headline=x["headline"], summary=x["summary"], bars=f(df), spy=g(sp)))
    if k % 100 == 0: print(k, len(out), f"{time.time()-t0:.0f}s", flush=True)
json.dump(out, open(sys.argv[2], "w")); print("saved", len(out))
