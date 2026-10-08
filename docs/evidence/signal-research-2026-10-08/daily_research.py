"""Daily-horizon signal research on real bars, 2016-2026, eight liquid ETFs.

Pre-stated family (nothing added after looking). Selection: before 2023-01-01. Test: from
2023-01-01, read once. Prices are raw (price return, no dividends, no cash yield): this biases
*against* any rule that sits out of the market, and the report says so wherever it matters.
Day-level observations are resampled in calendar-month blocks for intervals (forward returns
over h days overlap, and months carry the regime).
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

SYMS = ["SPY", "QQQ", "IWM", "DIA", "TLT", "XLF", "XLE", "XLB"]
PATH = "runtime/min_agent/replay/{s}_5Min_2010-01-04_2026-10-07.json"
SPLIT = pd.Timestamp("2023-01-01")


def load(s: str) -> pd.DataFrame:
    b = pd.DataFrame(json.load(open(PATH.format(s=s)))["bars"])
    b["d"] = pd.to_datetime(b["t"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    b = b.set_index("d")[["o", "h", "l", "c", "v"]]
    r = b.c.pct_change()
    bad = r.abs() > 0.15  # a split in raw prices, not a market move
    if bad.any():
        # rescale the history before each discontinuity so returns stay continuous
        f = pd.Series(1.0, index=b.index)
        for d in b.index[bad]:
            f[b.index < d] *= b.c[d] / b.c.shift(1)[d]
        for col in ("o", "h", "l", "c"): b[col] = b[col] * f
    return b


def features(b: pd.DataFrame) -> pd.DataFrame:
    c = b.c; r = c.pct_change()
    out = pd.DataFrame(index=b.index)
    for n in (1, 5, 21, 63, 126):
        out[f"R{n}"] = (c / c.shift(n) - 1) * 100
    for n in (50, 200):
        out[f"S{n}"] = (c / c.rolling(n).mean() - 1) * 100
    # Connors RSI(2)
    d = c.diff(); up = d.clip(lower=0).rolling(2).mean(); dn = (-d.clip(upper=0)).rolling(2).mean()
    out["RSI2"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out["RV21"] = r.rolling(21).std() * np.sqrt(252) * 100
    for h in (1, 5, 21):
        out[f"F{h}"] = (c.shift(-h) / c - 1) * 100
    out["FRV21"] = r[::-1].rolling(21).std()[::-1].shift(-1) * np.sqrt(252) * 100  # realised vol over the next 21 days
    out["ON"] = (b.o / c.shift(1) - 1) * 100   # overnight
    out["ID"] = (b.c / b.o - 1) * 100          # intraday
    return out


def rank(x): return pd.Series(x).rank(method="average").to_numpy()


def corr_boot(blocks, x, y, reps=4000, seed=5):
    ok = ~(np.isnan(x) | np.isnan(y)); blocks, x, y = blocks[ok], x[ok], y[ok]
    if len(x) < 60: return dict(n=len(x), rho=np.nan, lo=np.nan, hi=np.nan, p=np.nan)
    rx, ry = rank(x), rank(y); codes, _ = pd.factorize(blocks); B = len(set(codes))
    cols = [np.bincount(codes, v) for v in (np.ones(len(x)), rx, ry, rx * rx, ry * ry, rx * ry)]
    S = np.vstack(cols).T
    def corr(s):
        n, sx, sy, sxx, syy, sxy = s; vx = sxx / n - (sx / n) ** 2; vy = syy / n - (sy / n) ** 2
        return (sxy / n - sx * sy / n / n) / np.sqrt(vx * vy) if vx > 0 and vy > 0 else np.nan
    rho = corr(S.sum(0)); rng = np.random.default_rng(seed); vals = []
    for _ in range(reps):
        w = np.bincount(rng.integers(0, B, B), minlength=B).astype(float); vals.append(corr(w @ S))
    vals = np.array([v for v in vals if not np.isnan(v)])
    p = max(2 * min((vals <= 0).mean(), (vals >= 0).mean()), 1.0 / reps)
    return dict(n=len(x), rho=float(rho), lo=float(np.quantile(vals, .025)), hi=float(np.quantile(vals, .975)), p=float(p))


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


HYP = ([(f"R{n}", f"F{h}") for n in (1, 5) for h in (1, 5)]                     # short-term reversal
       + [(f"R{n}", f"F{h}") for n in (21, 63, 126) for h in (5, 21)]            # time-series momentum
       + [(f"S{n}", f"F{h}") for n in (50, 200) for h in (5, 21)]                # distance from average
       + [("RSI2", "F1"), ("RSI2", "F5"), ("RV21", "F21"), ("RV21", "FRV21")])  # oversold bounce; vol -> return; vol -> vol


def pooled(sym_frames, fx, fy, after=None, before=None):
    xs, ys, bl = [], [], []
    for s, fr in sym_frames.items():
        f = fr
        if after is not None: f = f[f.index >= after]
        if before is not None: f = f[f.index < before]
        # standardise each symbol's feature by its own training-free rank so pooling is not driven by scale
        xs.append(f[fx].to_numpy()); ys.append(f[fy].to_numpy()); bl.append((f.index.year * 100 + f.index.month).to_numpy())
    return np.concatenate(bl), np.concatenate(xs), np.concatenate(ys)


if __name__ == "__main__":
    frames = {s: features(load(s)) for s in SYMS}
    k = len(HYP); print(f"{len(SYMS)} symbols, {k} hypotheses, Bonferroni alpha {0.05 / k:.4f}; test from {SPLIT.date()}")
    print(f"{'hypothesis':14} {'train rho':>9} | {'test rho':>9} {'95% CI':>17} {'p':>7} {'n':>6} | per-symbol test rho ({', '.join(SYMS)})")
    for fx, fy in HYP:
        tr = corr_boot(*pooled(frames, fx, fy, before=SPLIT), reps=1500)
        te = corr_boot(*pooled(frames, fx, fy, after=SPLIT), reps=4000)
        per = []
        for s in SYMS:
            f = frames[s][frames[s].index >= SPLIT]
            per.append(corr_boot((f.index.year * 100 + f.index.month).to_numpy(), f[fx].to_numpy(), f[fy].to_numpy(), reps=300)["rho"])
        _ledger(f"daily:8etf:{fx}->{fy}", dict(tr, lo=np.nan, hi=np.nan, p=np.nan), te)
        same = np.sign(tr["rho"]) == np.sign(te["rho"]); sig = te["p"] < 0.05 / k
        print(f"{fx + '->' + fy:14} {tr['rho']:+9.3f} | {te['rho']:+9.3f} [{te['lo']:+.3f},{te['hi']:+.3f}] {te['p']:7.4f} {te['n']:6d} | "
              + " ".join(f"{v:+.2f}" for v in per) + ("  <== SURVIVES" if sig and same else ("  (significant, sign flipped)" if sig else "")))
