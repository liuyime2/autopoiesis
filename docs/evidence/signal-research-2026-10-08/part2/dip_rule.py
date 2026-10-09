"""Is the long-history reversal worth trading? Exposure follows the market's distance from its 50-day average.

Rule: hold `high` of the position when the index is below its 50-day average (a dip), `low` when it
is above. The fair baseline is not buy-and-hold but a CONSTANT exposure equal to the rule's own
average, because holding less is lower risk by itself. Signals at the close set the next day's
exposure; costs 0.025% per unit of exposure traded per side; cash earns nothing (no T-bill series
before 2002), which understates a rule that sits in cash. Selection 1993-2014, test 2015-2026.
Usage: dip_rule.py YAHOO.pkl"""
import sys
import numpy as np
import pandas as pd

COST = 0.00025
SPLIT = pd.Timestamp("2015-01-01")
raw = pd.read_pickle(sys.argv[1])


def series(sym):
    d = raw[sym].dropna(subset=["adjclose"]); return d.adjclose[d.adjclose > 0]


def sharpe(r): return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan


def run(c, high, low, n=50):
    r = c.pct_change()
    below = (c < c.rolling(n).mean())
    w = pd.Series(np.where(below, high, low), index=c.index).shift(1)
    valid = w.notna() & r.notna()
    cost = w.diff().abs().fillna(0) * COST
    return (w * r - cost)[valid], w[valid], r[valid]


def boot(diff, reps=3000, seed=3):
    b, _ = pd.factorize(diff.index.year * 12 + diff.index.month); B = b.max() + 1
    groups = [np.where(b == k)[0] for k in range(B)]; rng = np.random.default_rng(seed); v = []
    for _ in range(reps):
        pick = np.concatenate([groups[k] for k in rng.integers(0, B, B)]); v.append(diff.iloc[pick].mean() * 252)
    return np.quantile(v, .05), np.quantile(v, .95)


print(f"{'asset':5} {'period':10} {'avg exp':>8} {'rule CAGR':>10} {'const CAGR':>10} {'rule Sharpe':>12} {'const Sharpe':>13} {'rule maxDD':>11} {'const maxDD':>12} {'annual edge over const [90%]':>34}")
for sym in ("SPY", "QQQ", "IWM", "XLF", "XLE"):
    c = series(sym)
    for name, lo_, hi_ in (("1993-2014", None, SPLIT), ("2015-2026", SPLIT, None)):
        cc = c if lo_ is None else c[c.index >= lo_]
        cc = cc[cc.index < hi_] if hi_ is not None else cc
        if len(cc) < 500: continue
        full_r, w, r = run(c, 1.0, 0.5)
        sel = (full_r.index >= (lo_ or c.index[0])) & (full_r.index < (hi_ or pd.Timestamp("2100-01-01")))
        rr, ww, rb = full_r[sel], w[sel], r[sel]
        const = ww.mean() * rb - COST * 0  # constant exposure at the rule's own mean, no rebalancing cost to speak of
        nav = lambda x: (1 + x).cumprod(); dd = lambda x: (nav(x) / nav(x).cummax() - 1).min() * 100
        cagr = lambda x: (nav(x).iloc[-1] ** (252 / len(x)) - 1) * 100
        diff = rr - const; lo, hi = boot(diff)
        print(f"{sym:5} {name:10} {ww.mean():8.2f} {cagr(rr):9.1f}% {cagr(const):9.1f}% {sharpe(rr):12.2f} {sharpe(const):13.2f} {dd(rr):10.1f}% {dd(const):11.1f}% {diff.mean()*252*100:+8.2f}% [{lo*100:+.2f}, {hi*100:+.2f}]")
