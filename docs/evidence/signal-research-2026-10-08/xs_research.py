"""Cross-sectional signal research on ~120 large-cap stocks, total-return-adjusted daily bars.

Each day the stocks are ranked on a feature known at that day's close; the daily rank
correlation (IC) with the stocks' later returns is averaged. Pre-stated family of 12 (nothing
added after looking). Selection: before 2023-01-01. Test: from 2023-01-01, read once. Intervals
resample calendar months (forward returns over h days overlap).

SURVIVORSHIP: the universe is today's large caps, which were large because they did well. That
flatters anything that picks past winners (momentum) and works against reversal; the report
states it where it matters. It cannot be removed with the data available here.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

SPLIT = pd.Timestamp("2023-01-01")


def panel(path: str, exclude=("SPY", "QQQ", "IWM", "DIA", "TLT", "XLF", "XLE", "XLB")):
    p = pd.read_pickle(path)
    close = p.xs("close", axis=1, level=1).drop(columns=[c for c in exclude if c in p.columns.get_level_values(0)], errors="ignore")
    return close.sort_index()


def build(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    r1 = close.pct_change()
    f = {}
    f["ret_1d"] = r1 * 100
    f["ret_5d"] = (close / close.shift(5) - 1) * 100
    f["ret_21d"] = (close / close.shift(21) - 1) * 100
    f["mom_12_1"] = (close.shift(21) / close.shift(252) - 1) * 100       # 12-month return skipping the last month
    f["mom_6_1"] = (close.shift(21) / close.shift(126) - 1) * 100
    f["vol_21"] = r1.rolling(21).std() * np.sqrt(252) * 100
    f["vol_63"] = r1.rolling(63).std() * np.sqrt(252) * 100
    f["dist_52w_high"] = (close / close.rolling(252).max() - 1) * 100
    d = close.diff(); up = d.clip(lower=0).rolling(2).mean(); dn = (-d.clip(upper=0)).rolling(2).mean()
    f["rsi2"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    for h in (1, 5, 21):
        f[f"F{h}"] = (close.shift(-h) / close - 1) * 100
    return f


def _ledger(name, train, test, blocks=0, control=None):
    """Write the hypothesis to the signal ledger when SIGNAL_LEDGER is set (see min_agent.research.signal_test)."""
    import os
    path = os.environ.get("SIGNAL_LEDGER")
    if not path:
        return
    from pathlib import Path
    from min_agent.research import signal_test as st
    make = lambda d: st.CorrelationResult(int(d["n"]), int(d.get("D", blocks)), d["rho"], d["lo"], d["hi"], d["p"])
    st.judge(name, make(train), make(test), control=make(control) if control else None, ledger=Path(path))


HYP = [("ret_1d", "F1"), ("ret_1d", "F5"), ("ret_5d", "F5"), ("ret_5d", "F21"), ("ret_21d", "F21"),
       ("mom_12_1", "F21"), ("mom_6_1", "F21"), ("vol_63", "F21"), ("vol_21", "F21"),
       ("dist_52w_high", "F21"), ("rsi2", "F1"), ("rsi2", "F5")]


def daily_ic(x: pd.DataFrame, y: pd.DataFrame) -> pd.Series:
    ok = x.notna() & y.notna()
    x = x.where(ok); y = y.where(ok)
    rx = x.rank(axis=1); ry = y.rank(axis=1)
    n = ok.sum(axis=1)
    cov = (rx * ry).mean(axis=1) - rx.mean(axis=1) * ry.mean(axis=1)
    ic = cov / (rx.std(axis=1, ddof=0) * ry.std(axis=1, ddof=0))
    return ic[n >= 40]


def month_boot(s: pd.Series, reps=4000, seed=9):
    s = s.dropna(); blocks, _ = pd.factorize(s.index.year * 100 + s.index.month)
    B = blocks.max() + 1; sums = np.bincount(blocks, s.to_numpy()); cnt = np.bincount(blocks).astype(float)
    rng = np.random.default_rng(seed); vals = np.empty(reps)
    for i in range(reps):
        w = np.bincount(rng.integers(0, B, B), minlength=B).astype(float); vals[i] = (w @ sums) / (w @ cnt)
    p = max(2 * min((vals <= 0).mean(), (vals >= 0).mean()), 1.0 / reps)
    return s.mean(), np.quantile(vals, .025), np.quantile(vals, .975), p, len(s)


def decile_spread(x: pd.DataFrame, y: pd.DataFrame, h: int, cost_bp: float, sign: int = 1):
    """Top-minus-bottom decile, rebalanced every h days; net of a round trip each rebalance."""
    idx = x.index[::h]; rows = []
    for t in idx:
        xv = x.loc[t]; yv = y.loc[t]; ok = xv.notna() & yv.notna()
        if ok.sum() < 40: continue
        r = xv[ok].rank(); n = len(r); k = max(1, n // 10)
        lo = r.nsmallest(k).index; hi = r.nlargest(k).index
        rows.append((t, sign * (yv[hi].mean() - yv[lo].mean())))
    s = pd.Series(dict(rows))
    return s, s - 2 * cost_bp / 100.0  # long and short legs each pay the round trip


if __name__ == "__main__":
    close = panel(sys.argv[1]); f = build(close)
    # the control: the later return in excess of beta times the market's, with beta known at the time
    spy = pd.read_pickle(sys.argv[1]).xs("close", axis=1, level=1)["SPY"].reindex(close.index)
    beta = close.pct_change().rolling(126).cov(spy.pct_change()).div(spy.pct_change().rolling(126).var(), axis=0)
    for h in (1, 5, 21):
        f[f"F{h}_x"] = f[f"F{h}"].sub(beta.mul((spy.shift(-h) / spy - 1) * 100, axis=0))
    k = len(HYP); print(f"{close.shape[1]} stocks, {close.index[0].date()}..{close.index[-1].date()}, {k} hypotheses, Bonferroni alpha {0.05 / k:.4f}; test from {SPLIT.date()}")
    print(f"{'hypothesis':20} {'IC train':>9} | {'IC test':>8} {'95% CI':>17} {'p':>7} {'days':>5}  verdict")
    survivors = []
    for fx, fy in HYP:
        ic = daily_ic(f[fx], f[fy])
        tr = ic[ic.index < SPLIT]; te = ic[ic.index >= SPLIT]
        m, lo, hi, p, n = month_boot(te); mt = tr.mean()
        te_x = daily_ic(f[fx], f[fy + "_x"]); te_x = te_x[te_x.index >= SPLIT]
        mx, lox, hix, px, nx = month_boot(te_x)
        _ledger(f"xs:120stocks:{fx}->{fy}", dict(n=len(tr), rho=mt, lo=np.nan, hi=np.nan, p=np.nan), dict(n=n, rho=m, lo=lo, hi=hi, p=p),
                control=dict(n=nx, rho=mx, lo=lox, hi=hix, p=px))
        sig = p < 0.05 / k; same = np.sign(m) == np.sign(mt)
        verdict = "SURVIVES" if sig and same else ("significant, sign flipped" if sig else "-")
        if sig and same: survivors.append((fx, fy, np.sign(m)))
        print(f"{fx + '->' + fy:20} {mt:+9.4f} | {m:+8.4f} [{lo:+.4f},{hi:+.4f}] {p:7.4f} {n:5d}  {verdict}")
    print("survivors:", survivors)
