"""Does forecasting volatility have decision value? Exposure rules on total-return-adjusted SPY & co.

Signals at the close of day t set the exposure held over day t+1. Parameters are chosen on
2016-2022; 2023 to 2026-10 is the test, read once. Out of the market earns the T-bill proxy (BIL).
A turn of exposure costs 0.025% per unit per side (0.05% round trip, the system's own assumption).
"""
import sys
import numpy as np
import pandas as pd

SPLIT = pd.Timestamp("2023-01-01")
COST = 0.00025


def stats(r: pd.Series, rf: pd.Series):
    ex = r - rf.reindex(r.index).fillna(0)
    yrs = len(r) / 252
    nav = (1 + r).cumprod()
    dd = (nav / nav.cummax() - 1).min()
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    sharpe = ex.mean() / ex.std() * np.sqrt(252) if ex.std() > 0 else np.nan
    return dict(cagr=cagr * 100, vol=r.std() * np.sqrt(252) * 100, sharpe=sharpe, maxdd=dd * 100, calmar=(cagr / abs(dd)) if dd < 0 else np.nan)


def run(close: pd.Series, rf: pd.Series, rule: str, **kw) -> pd.Series:
    r = close.pct_change()
    if rule == "bh":
        w = pd.Series(1.0, index=close.index)
    elif rule == "trend":
        w = (close > close.rolling(kw.get("n", 200)).mean()).astype(float)
    elif rule == "vt":
        rv = r.rolling(kw.get("lb", 21)).std() * np.sqrt(252)
        w = (kw["target"] / rv).clip(upper=kw.get("cap", 1.0))
    elif rule == "vt_trend":
        rv = r.rolling(21).std() * np.sqrt(252)
        w = (kw["target"] / rv).clip(upper=kw.get("cap", 1.0)) * (close > close.rolling(200).mean())
    w = w.shift(1).fillna(0.0)                   # decided at t-1's close
    cost = w.diff().abs().fillna(0) * COST
    return (w * r + (1 - w) * rf.reindex(close.index).fillna(0) - cost).dropna()


def boot_diff(a: pd.Series, b: pd.Series, rf, stat, reps=3000, seed=2):
    idx = a.index.intersection(b.index); a, b = a[idx], b[idx]
    blocks, _ = pd.factorize(idx.year * 100 + idx.month); B = blocks.max() + 1
    groups = [np.where(blocks == k)[0] for k in range(B)]; rng = np.random.default_rng(seed); vals = []
    for _ in range(reps):
        pick = np.concatenate([groups[k] for k in rng.integers(0, B, B)])
        va, vb = stats(a.iloc[pick].reset_index(drop=True), rf.iloc[:0]), stats(b.iloc[pick].reset_index(drop=True), rf.iloc[:0])
        vals.append(va[stat] - vb[stat])
    vals = np.array(vals); return np.nanquantile(vals, .05), np.nanquantile(vals, .95)


if __name__ == "__main__":
    adj = pd.read_pickle(sys.argv[1]).xs("close", axis=1, level=1)
    cash = pd.read_pickle(sys.argv[2]).xs("close", axis=1, level=1)["BIL"].pct_change()
    rows = []
    for sym in ("SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLB", "TLT"):
        c = adj[sym].dropna()
        # parameters chosen on the selection period only
        best = max((stats(run(c[c.index < SPLIT], cash, "vt", target=t), cash)["sharpe"], t) for t in (0.08, 0.10, 0.12, 0.15))
        t_star = best[1]
        variants = {"buy&hold": ("bh", {}), "trend SMA200": ("trend", {}), f"vol-target {t_star:.0%}": ("vt", {"target": t_star}),
                    f"vol-target {t_star:.0%} + trend": ("vt_trend", {"target": t_star})}
        for period, sel in (("select 2016-22", lambda s: s[s.index < SPLIT]), ("TEST 2023-26", lambda s: s[s.index >= SPLIT])):
            base = sel(run(c, cash, "bh"))
            for name, (rule, kw) in variants.items():
                r = sel(run(c, cash, rule, **kw)); s = stats(r, cash)
                rows.append(dict(sym=sym, period=period, rule=name, **s))
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200); pd.set_option("display.float_format", lambda v: f"{v:7.2f}")
    for period in ("select 2016-22", "TEST 2023-26"):
        print(f"\n== {period}: mean across the 8 instruments (each rule's own stats, then averaged) ==")
        g = df[df.period == period].groupby(df.rule.str.replace(r"\d+%", "X%", regex=True))[["cagr", "vol", "sharpe", "maxdd", "calmar"]].mean()
        print(g.round(2))
    print("\n== TEST period, per instrument: Sharpe | max drawdown (%) ==")
    t = df[df.period == "TEST 2023-26"].copy(); t["rule"] = t.rule.str.replace(r"\d+%", "X%", regex=True)
    print(t.pivot(index="sym", columns="rule", values="sharpe").round(2).to_string())
    print(t.pivot(index="sym", columns="rule", values="maxdd").round(1).to_string())
