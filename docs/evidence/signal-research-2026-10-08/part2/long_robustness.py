"""Does the long-history reversal survive being looked at harder?

For the hypotheses that cleared the bar in long_history.py: (a) wider resampling blocks (the features
look back 63-126 days, so single months understate the dependence), (b) without the 2020 crash,
(c) on assets the hypothesis was not selected on (bonds, gold, foreign equities), (d) with the
market's common move removed, leaving only differences between sectors on the same day.
Usage: long_robustness.py YAHOO.pkl"""
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_research as dr
import long_history as lh

raw = pd.read_pickle(sys.argv[1])
pool = {s: dr.features(f) for s, f in lh.frames_from(sys.argv[1]).items()}
other = {}
for s in ("TLT", "IEF", "LQD", "GLD", "EFA"):
    d = raw[s].dropna(subset=["close", "adjclose"]); k = d.adjclose / d.close
    other[s] = dr.features(pd.DataFrame({"o": d.open * k, "h": d.close * k, "l": d.close * k, "c": d.adjclose, "v": d.volume}))
S = lh.SPLIT


def blocks(idx, months):
    return ((idx.year * 12 + idx.month - 1) // months).to_numpy()


def stat(frames, fx, fy, months=1, drop=None, after=S):
    xs, ys, bl = [], [], []
    for f in frames.values():
        f = f[f.index >= after]
        if drop: f = f[~((f.index >= drop[0]) & (f.index <= drop[1]))]
        xs.append(f[fx].to_numpy()); ys.append(f[fy].to_numpy()); bl.append(blocks(f.index, months))
    r = dr.corr_boot(np.concatenate(bl), np.concatenate(xs), np.concatenate(ys), reps=2000)
    return r


def within_day(frames, fx, fy):
    """Rank each feature and outcome across the sectors each day, so the market's common move drops out."""
    X = pd.concat({s: f[fx] for s, f in frames.items()}, axis=1); Y = pd.concat({s: f[fy] for s, f in frames.items()}, axis=1)
    ok = X.notna() & Y.notna() & (X.notna().sum(axis=1) >= 8).to_numpy()[:, None]
    X, Y = X.where(ok), Y.where(ok)
    rx, ry = X.rank(axis=1), Y.rank(axis=1)
    ic = ((rx - rx.mean(axis=1).to_numpy()[:, None]) * (ry - ry.mean(axis=1).to_numpy()[:, None])).sum(axis=1) / (
        np.sqrt(((rx - rx.mean(axis=1).to_numpy()[:, None]) ** 2).sum(axis=1) * ((ry - ry.mean(axis=1).to_numpy()[:, None]) ** 2).sum(axis=1)))
    ic = ic.dropna(); ic = ic[ic.index >= S]
    b, _ = pd.factorize(ic.index.year * 12 + ic.index.month); B = b.max() + 1
    s = np.bincount(b, ic.to_numpy()); n = np.bincount(b).astype(float); rng = np.random.default_rng(1)
    v = [(w @ s) / (w @ n) for w in (np.bincount(rng.integers(0, B, B), minlength=B).astype(float) for _ in range(2000))]
    return ic.mean(), np.quantile(v, .025), np.quantile(v, .975)


print(f"{'hypothesis':12} | {'1-month blocks':>22} | {'6-month blocks':>22} | {'without 2020':>22} | {'bonds/gold/EFA':>22} | {'sectors only (market removed)':>30}")
for fx, fy in (("R63", "F21"), ("R126", "F21"), ("S50", "F21"), ("R63", "F5"), ("RV21", "F21")):
    a = stat(pool, fx, fy, 1); b = stat(pool, fx, fy, 6); c = stat(pool, fx, fy, 1, drop=(pd.Timestamp("2020-02-01"), pd.Timestamp("2020-07-31")))
    d = stat(other, fx, fy, 6)
    sec = {s: pool[s] for s in lh.POOL if s not in ("SPY", "QQQ", "IWM", "DIA")}
    m, lo, hi = within_day(sec, fx, fy)
    f = lambda r: f"{r['rho']:+.3f} [{r['lo']:+.2f},{r['hi']:+.2f}] p={r['p']:.3f}"
    print(f"{fx + '->' + fy:12} | {f(a):>22} | {f(b):>22} | {f(c):>22} | {f(d):>22} | IC {m:+.4f} [{lo:+.3f},{hi:+.3f}]")
