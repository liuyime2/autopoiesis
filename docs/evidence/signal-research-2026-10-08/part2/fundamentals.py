"""Fundamentals from the SEC, point in time, on the large-cap sample.

Pre-stated (6): (1) earnings-announcement return -> the next 20 and 60 trading days' abnormal return
(post-earnings drift); (2) return on assets (+), asset growth (-), earnings yield (+), book-to-market
(+) -> the next month's beta-adjusted return, as a monthly cross-sectional rank correlation.
A value is used from the day its filing was public and not before. Market value is the unadjusted price
times the shares outstanding in the latest filing. Quality is ROA because gross profit is reported by
only 55 of 119 companies; that was decided before looking.
SURVIVORSHIP: today's large caps. Selection: the earlier 60% of dates; test: the later 40%.
Usage: fundamentals.py SEC.pkl PANEL_ADJ.pkl PANEL_RAW.pkl"""
import os, pickle, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import xs_research as xs
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def ledger(name, tr, te):
    path = os.environ.get("SIGNAL_LEDGER")
    if not path: return
    from pathlib import Path
    from min_agent.research import signal_test as st
    mk = lambda d: st.CorrelationResult(int(d["n"]), 0, d["rho"], d.get("lo", np.nan), d.get("hi", np.nan), d.get("p", np.nan))
    st.judge(name, mk(tr), mk(te), ledger=Path(path))


def month_boot(s, reps=3000, seed=2):
    s = s.dropna(); b, _ = pd.factorize(s.index.year * 12 + s.index.month); B = b.max() + 1
    sums = np.bincount(b, s.to_numpy()); cnt = np.bincount(b).astype(float); rng = np.random.default_rng(seed)
    v = np.array([(w @ sums) / (w @ cnt) for w in (np.bincount(rng.integers(0, B, B), minlength=B).astype(float) for _ in range(reps))])
    return dict(n=len(s), rho=s.mean(), lo=np.quantile(v, .025), hi=np.quantile(v, .975), p=max(2 * min((v <= 0).mean(), (v >= 0).mean()), 1 / reps))


def latest(rows, asof, fy_only=False):
    """The most recently FILED value as of `asof`. rows: (end, start, val, filed, form, fp)."""
    best = None
    for end, start, val, filed, form, fp in rows:
        if filed > asof: continue
        if fy_only and fp != "FY": continue
        if best is None or (end, filed) > (best[0], best[3]): best = (end, start, val, filed)
    return best


def prev_year(rows, asof, end):
    cand = [(e, v) for e, s, v, f, form, fp in rows if f <= asof and fp == "FY" and e < end and (pd.Timestamp(end) - pd.Timestamp(e)).days > 300]
    return max(cand)[1] if cand else None


if __name__ == "__main__":
    sec = pickle.load(open(sys.argv[1], "rb"))
    adj = pd.read_pickle(sys.argv[2]).xs("close", axis=1, level=1); raw = pd.read_pickle(sys.argv[3]).xs("close", axis=1, level=1)
    opn = pd.read_pickle(sys.argv[2]).xs("open", axis=1, level=1)
    spy = adj["SPY"]
    # ---- (1) post-earnings drift -------------------------------------------------------------
    rows = []
    idx = adj.index
    for t, d in sec.items():
        if t not in adj.columns: continue
        px = adj[t]
        for acc in d["earnings"]:
            ts = pd.Timestamp(acc).tz_convert(NY); day = pd.Timestamp(ts.date())
            pos = idx.searchsorted(day)
            if pos >= len(idx): continue
            if ts.time() >= pd.Timestamp("16:00").time() or idx[pos] != day:
                pos += 1 if idx[pos] == day else 0   # after the close: the reaction is the next session
            r, h20, h60 = pos, pos + 20, pos + 60
            if r < 1 or h60 >= len(idx): continue
            ear = (px.iloc[r] / px.iloc[r - 1] - 1) - (spy.iloc[r] / spy.iloc[r - 1] - 1)
            d20 = (px.iloc[h20] / px.iloc[r] - 1) - (spy.iloc[h20] / spy.iloc[r] - 1)
            d60 = (px.iloc[h60] / px.iloc[r] - 1) - (spy.iloc[h60] / spy.iloc[r] - 1)
            if np.isfinite([ear, d20, d60]).all(): rows.append(dict(sym=t, day=idx[r], ear=ear * 100, d20=d20 * 100, d60=d60 * 100))
    ev = pd.DataFrame(rows).sort_values("day")
    cut = ev.day.iloc[int(len(ev) * 0.6)]
    print(f"earnings events {len(ev)} ({ev.day.min().date()}..{ev.day.max().date()}); test from {cut.date()}\n")
    print(f"{'hypothesis':26} {'train':>8} | {'test rho':>9} {'95% CI':>17} {'p':>7} {'n':>5}")
    from min_agent.research import signal_test as st
    import signal_research as sr
    for y in ("d20", "d60"):
        res = {}
        for lab, sub in (("tr", ev[ev.day < cut]), ("te", ev[ev.day >= cut])):
            blocks = (sub.day.dt.year * 4 + (sub.day.dt.month - 1) // 3).to_numpy()
            res[lab] = sr.test(blocks, sub.ear.to_numpy(), sub[y].to_numpy(), reps=2500)
        ledger(f"fundamentals:EAR->{y}", res["tr"], res["te"])
        print(f"{'EAR->' + y:26} {res['tr']['rho']:+8.3f} | {res['te']['rho']:+9.3f} [{res['te']['lo']:+.3f},{res['te']['hi']:+.3f}] {res['te']['p']:7.4f} {res['te']['n']:5d}")
    # ---- (2) the cross-section ---------------------------------------------------------------
    months = adj.index[adj.index >= "2017-01-01"].to_series().groupby([adj.index[adj.index >= "2017-01-01"].year, adj.index[adj.index >= "2017-01-01"].month]).last().values
    ret = adj.pct_change(); mk = spy.pct_change()
    beta = ret.rolling(126).cov(mk).div(mk.rolling(126).var(), axis=0)
    feats = {k: {} for k in ("ROA", "asset_growth", "earn_yield", "book_to_market")}
    fwd = {}
    for m in months:
        asof = pd.Timestamp(m).strftime("%Y-%m-%d"); pos = idx.get_loc(pd.Timestamp(m))
        if pos + 21 >= len(idx): continue
        mret = spy.iloc[pos + 21] / spy.iloc[pos] - 1
        fwd[m] = {}
        for t, d in sec.items():
            if t not in adj.columns: continue
            a = latest(d["facts"]["Assets"], asof, fy_only=True); ni = latest(d["facts"]["NetIncomeLoss"], asof, fy_only=True)
            eq = latest(d["facts"]["StockholdersEquity"], asof); sh = latest(d["facts"]["EntityCommonStockSharesOutstanding"], asof)
            p_raw = raw[t].iloc[pos] if t in raw.columns else np.nan
            b = beta[t].iloc[pos]
            f = adj[t].iloc[pos + 21] / adj[t].iloc[pos] - 1
            if np.isfinite(f) and np.isfinite(b): fwd[m][t] = (f - b * mret) * 100
            if a and ni and a[2] > 0: feats["ROA"].setdefault(m, {})[t] = ni[2] / a[2]
            if a:
                pa = prev_year(d["facts"]["Assets"], asof, a[0])
                if pa and pa > 0: feats["asset_growth"].setdefault(m, {})[t] = a[2] / pa - 1
            if sh and np.isfinite(p_raw) and p_raw > 0 and sh[2] > 0:
                mcap = p_raw * sh[2]
                if ni: feats["earn_yield"].setdefault(m, {})[t] = ni[2] / mcap
                if eq and eq[2] > 0: feats["book_to_market"].setdefault(m, {})[t] = eq[2] / mcap
    Y = pd.DataFrame(fwd).T
    print(f"\ncross-section: {len(Y)} monthly dates, up to {Y.notna().sum(axis=1).max()} stocks; test from the later 40% of dates\n")
    print(f"{'hypothesis':26} {'IC train':>8} | {'IC test':>8} {'95% CI':>17} {'p':>7} {'months':>6}")
    cut_m = Y.index[int(len(Y) * 0.6)]
    for name, expected in (("ROA", "+"), ("asset_growth", "-"), ("earn_yield", "+"), ("book_to_market", "+")):
        X = pd.DataFrame(feats[name]).T.reindex(Y.index)
        ic = xs.daily_ic(X, Y.reindex(columns=X.columns)).dropna()
        ic.index = pd.to_datetime(ic.index)
        tr, te = ic[ic.index < cut_m], ic[ic.index >= cut_m]
        a = month_boot(tr, 1500); b = month_boot(te, 3000)
        ledger(f"fundamentals:{name}->F21", a, b)
        print(f"{name + ' (' + expected + ')':26} {a['rho']:+8.4f} | {b['rho']:+8.4f} [{b['lo']:+.4f},{b['hi']:+.4f}] {b['p']:7.4f} {b['n']:6d}")
