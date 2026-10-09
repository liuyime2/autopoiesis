"""SPY implied volatility and skew from Alpaca's option history (expired contracts included), by Black-Scholes.

For every second trading day: the expiry closest to 30 days out, the call and put nearest the money, and the put
nearest 95% of spot. Each option's day VWAP (not the last trade, which can be hours old) priced against SPY's
volume-weighted price that day. r = 4%, dividend yield 1.3% (the error these cause in an implied volatility of
15-20% is a few tenths of a point, the same for every day). Keeps only options that traded at least 20 contracts.
Usage: options_iv.py OUT.csv  (needs the SPY 5-minute cache and the broker credentials)"""
import json, math, os, sys, time
import datetime as dt
import requests
import pandas as pd

H = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
R, Q = 0.04, 0.013


def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, sig, call):
    d1 = (math.log(S / K) + (R - Q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T)); d2 = d1 - sig * math.sqrt(T)
    if call: return S * math.exp(-Q * T) * ncdf(d1) - K * math.exp(-R * T) * ncdf(d2)
    return K * math.exp(-R * T) * ncdf(-d2) - S * math.exp(-Q * T) * ncdf(-d1)


def iv(price, S, K, T, call):
    lo, hi = 0.02, 2.5
    if price < max(0.0, (S * math.exp(-Q * T) - K * math.exp(-R * T)) if call else (K * math.exp(-R * T) - S * math.exp(-Q * T))) - 1e-9: return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, call) > price: hi = mid
        else: lo = mid
    return (lo + hi) / 2


def get(url, params):
    for _ in range(3):
        r = requests.get(url, headers=H, params=params, timeout=40)
        if r.status_code == 200: return r.json()
        time.sleep(1.5)
    return {}


bars = json.load(open("runtime/min_agent/replay/SPY_5Min_2024-01-02_2026-10-08.json"))["bars"]
df = pd.DataFrame(bars); df["t"] = pd.to_datetime(df.t, utc=True).dt.tz_convert("America/New_York")
df = df[(df.t.dt.time >= dt.time(9, 30)) & (df.t.dt.time < dt.time(16, 0))]
df["pv"] = df.c * df.v
daily = df.groupby(df.t.dt.date).agg(pv=("pv", "sum"), v=("v", "sum"), close=("c", "last"))
daily["spot"] = daily.pv / daily.v
days = [d for d in daily.index if dt.date(2024, 3, 1) <= d <= dt.date(2026, 8, 31)][::2]
today = dt.date.today(); rows = []
for k, d in enumerate(days):
    S = float(daily.loc[d, "spot"]); lo_e, hi_e = d + dt.timedelta(days=24), d + dt.timedelta(days=38)
    status = "inactive" if hi_e < today else "active"
    cs = get("https://paper-api.alpaca.markets/v2/options/contracts", {"underlying_symbols": "SPY", "status": status, "expiration_date_gte": lo_e.isoformat(),
             "expiration_date_lte": hi_e.isoformat(), "strike_price_gte": round(S * 0.93), "strike_price_lte": round(S * 1.02), "limit": 1000}).get("option_contracts", [])
    if not cs: continue
    exps = sorted({c["expiration_date"] for c in cs}, key=lambda e: abs((dt.date.fromisoformat(e) - d).days - 30)); E = exps[0]
    cs = [c for c in cs if c["expiration_date"] == E]
    def nearest(kind, target):
        c = [x for x in cs if x["type"] == kind]
        return min(c, key=lambda x: abs(float(x["strike_price"]) - target)) if c else None
    want = {"call": nearest("call", S), "put": nearest("put", S), "put95": nearest("put", S * 0.95)}
    syms = [w["symbol"] for w in want.values() if w]
    bj = get("https://data.alpaca.markets/v1beta1/options/bars", {"symbols": ",".join(syms), "timeframe": "1Day", "start": d.isoformat(), "end": (d + dt.timedelta(days=1)).isoformat()}).get("bars", {})
    T = (dt.date.fromisoformat(E) - d).days / 365.0; out = {"date": d, "spot": S, "expiry": E, "T": T}
    for name, w in want.items():
        b = (bj.get(w["symbol"]) or []) if w else []
        b = [x for x in b if x["t"][:10] == d.isoformat()]
        if b and b[0]["v"] >= 20:
            out[f"iv_{name}"] = iv(b[0]["vw"], S, float(w["strike_price"]), T, name == "call")
            out[f"vol_{name}"] = b[0]["v"]
    rows.append(out)
    if k % 25 == 0: print(k, len(days), len(rows), flush=True)
pd.DataFrame(rows).to_csv(sys.argv[1], index=False); print("saved", len(rows))
