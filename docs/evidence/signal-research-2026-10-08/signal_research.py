"""Powered signal research on real 5-minute bars.

A fixed, pre-stated family of hypotheses (nothing is added after looking). Selection period:
the first 60% of trading days. Test period: the last 40%, read once. Every hypothesis is
reported, whether or not it works, and the multiple-testing threshold counts all of them.

Statistics: Spearman-style rank correlation between a point-in-time feature and the forward
return, with a day-block bootstrap (whole trading days resampled, because neighbouring bars
share the same session) for the interval and the p-value.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
BARS_PER_DAY = 78
REPLAY = "runtime/min_agent/replay/{sym}_5Min_2024-01-02_2026-10-08.json"


def load(sym: str) -> pd.DataFrame:
    raw = json.load(open(REPLAY.format(sym=sym)))["bars"]
    df = pd.DataFrame(raw)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(NY)
    df = df[(df.t.dt.time >= dt.time(9, 30)) & (df.t.dt.time < dt.time(16, 0))].copy()
    df["day"] = df.t.dt.date
    df["i"] = df.groupby("day").cumcount()
    full = df.groupby("day")["i"].transform("max") == BARS_PER_DAY - 1
    df = df[full].reset_index(drop=True)  # whole sessions only (drops half-days and gaps)
    return df


def frame(df: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time features and forward returns, never crossing the close."""
    g = df.groupby("day")["c"]
    out = pd.DataFrame({"day": df.day, "i": df.i})
    for n in (1, 3, 6, 12, 24, 48):
        out[f"M{n}"] = (df.c / g.shift(n) - 1.0) * 100  # NaN until n bars into the session
    for n in (12, 48, 78):
        sma = g.transform(lambda s, n=n: s.rolling(n, min_periods=n).mean())
        out[f"S{n}"] = (df.c / sma - 1.0) * 100
    for h in (12, 48):
        out[f"f{h}"] = (g.shift(-h) / df.c - 1.0) * 100  # NaN if it would pass the close
    return out


def day_table(df: pd.DataFrame) -> pd.DataFrame:
    d = df.groupby("day")
    first = d.apply(lambda x: x.c.iloc[5] / x.o.iloc[0] - 1.0) * 100       # open -> 10:00
    last = d.apply(lambda x: x.c.iloc[77] / x.c.iloc[71] - 1.0) * 100      # 15:30 -> close
    openpx = d.o.first(); closepx = d.c.last()
    gap = (openpx / closepx.shift(1) - 1.0) * 100
    day_ret = (closepx / openpx - 1.0) * 100
    return pd.DataFrame({"first30": first, "last30": last, "gap": gap, "day_ret": day_ret}).dropna()


def ranks(x: np.ndarray) -> np.ndarray:
    return pd.Series(x).rank(method="average").to_numpy()


def block_stats(days: np.ndarray, x: np.ndarray, y: np.ndarray):
    """Per-day sufficient statistics of the rank-transformed pair, for a fast block bootstrap."""
    rx, ry = ranks(x), ranks(y)
    codes, uniq = pd.factorize(days)
    n = np.bincount(codes).astype(float)
    sx = np.bincount(codes, rx); sy = np.bincount(codes, ry)
    sxx = np.bincount(codes, rx * rx); syy = np.bincount(codes, ry * ry); sxy = np.bincount(codes, rx * ry)
    return np.vstack([n, sx, sy, sxx, syy, sxy]).T


def corr_from(S: np.ndarray) -> float:
    n, sx, sy, sxx, syy, sxy = S
    if n < 30: return float("nan")
    cov = sxy / n - (sx / n) * (sy / n)
    vx = sxx / n - (sx / n) ** 2; vy = syy / n - (sy / n) ** 2
    return float(cov / np.sqrt(vx * vy)) if vx > 0 and vy > 0 else float("nan")


def test(days, x, y, reps=4000, seed=11):
    ok = ~(np.isnan(x) | np.isnan(y))
    days, x, y = days[ok], x[ok], y[ok]
    if len(x) < 50: return dict(n=len(x), rho=float("nan"), lo=float("nan"), hi=float("nan"), p=float("nan"), D=0)
    B = block_stats(days, x, y); D = len(B)
    rho = corr_from(B.sum(0))
    rng = np.random.default_rng(seed); vals = np.empty(reps)
    for r in range(reps):
        w = np.bincount(rng.integers(0, D, D), minlength=D).astype(float)
        vals[r] = corr_from(w @ B)
    vals = vals[~np.isnan(vals)]
    p = 2 * min((vals <= 0).mean(), (vals >= 0).mean())
    return dict(n=len(x), D=D, rho=rho, lo=float(np.quantile(vals, .025)), hi=float(np.quantile(vals, .975)), p=max(p, 1.0 / reps))


def _ledger(name, train, test, blocks=0):
    """Write the hypothesis to the signal ledger when SIGNAL_LEDGER is set (see min_agent.research.signal_test)."""
    import os
    path = os.environ.get("SIGNAL_LEDGER")
    if not path:
        return
    from pathlib import Path
    from min_agent.research import signal_test as st
    make = lambda d: st.CorrelationResult(int(d["n"]), int(d.get("D", blocks)), d["rho"], d["lo"], d["hi"], d["p"])
    st.judge(name, make(train), make(test), ledger=Path(path))


HYPOTHESES = (
    [(f"M{n}", f"f{h}") for n in (1, 3, 6, 12, 24, 48) for h in (12, 48)]          # momentum / reversal
    + [(f"S{n}", f"f{h}") for n in (12, 48, 78) for h in (12, 48)]                  # distance from the average
    + [("first30", "last30"), ("gap", "day_ret"), ("gap", "first30")]                # opening range, gap
)


def run(sym: str, split_day=None, verbose=True):
    df = load(sym); fr = frame(df); dt_ = day_table(df)
    days_all = sorted(df.day.unique())
    cut = days_all[int(len(days_all) * 0.6)] if split_day is None else split_day
    rows = []
    for fx, fy in HYPOTHESES:
        if fx in dt_.columns:  # daily hypotheses
            tr = dt_[dt_.index < cut]; te = dt_[dt_.index >= cut]
            a = test(np.array(tr.index), tr[fx].to_numpy(), tr[fy].to_numpy(), reps=1500)
            b = test(np.array(te.index), te[fx].to_numpy(), te[fy].to_numpy(), reps=4000)
        else:
            tr = fr[fr.day < cut]; te = fr[fr.day >= cut]
            a = test(tr.day.to_numpy(), tr[fx].to_numpy(), tr[fy].to_numpy(), reps=1500)
            b = test(te.day.to_numpy(), te[fx].to_numpy(), te[fy].to_numpy(), reps=4000)
        rows.append(dict(sym=sym, x=fx, y=fy, train=a, test=b))
        if b["rho"] == b["rho"]:  # a combination that cannot exist inside one session has nothing to record
            _ledger(f"intraday:{sym}:{fx}->{fy}", a, b)
    return rows, cut, len(days_all)


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    rows, cut, nd = run(sym)
    k = len(HYPOTHESES)
    print(f"{sym}: {nd} full sessions, test period starts {cut}, {k} hypotheses, Bonferroni alpha = {0.05 / k:.4f}")
    print(f"{'hypothesis':18} {'rho train':>9} {'rho test':>9} {'95% CI test':>16} {'p test':>8} {'D':>4}  verdict")
    for r in rows:
        a, b = r["train"], r["test"]
        sig = b["p"] < 0.05 / k
        same = np.sign(a["rho"]) == np.sign(b["rho"])
        print(f"{r['x'] + '->' + r['y']:18} {a['rho']:+9.3f} {b['rho']:+9.3f} [{b['lo']:+.3f},{b['hi']:+.3f}] {b['p']:8.4f} {b['D']:4d}  "
              f"{'SURVIVES' if sig and same else ('test-significant, sign flipped' if sig else '-')}")
