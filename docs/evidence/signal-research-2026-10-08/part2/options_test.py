"""Does the options market know something the system's own volatility forecast does not?

Pre-stated (4), SPY, 30-day options from options_iv.py:
 (1) the part of implied volatility the EWMA forecast does not explain -> the next 21 days' realised volatility;
 (2) the volatility risk premium (implied minus the EWMA forecast) -> SPY's next 21 days' return;
 (3) skew (95% put implied volatility minus the at-the-money one) -> SPY's next 5 days' return;
 (4) the same skew -> SPY's next 21 days' return.
Selection: the earlier 60% of days; test: the later 40%. Intervals resample calendar months.
Usage: options_test.py OPTIONS.csv PANEL_ADJ.pkl"""
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import signal_research as sr
from min_agent import risk_judgment as rj

o = pd.read_csv(sys.argv[1], parse_dates=["date"]).set_index("date")
c = pd.read_pickle(sys.argv[2]).xs("close", axis=1, level=1)["SPY"].dropna()
r = np.log(c).diff()
ew = pd.Series(rj.ewma_series(list(c)), index=c.index[20:])          # annualised %, after each day's return
frv = r[::-1].rolling(21).std()[::-1].shift(-1) * np.sqrt(252) * 100
f5 = (c.shift(-5) / c - 1) * 100; f21 = (c.shift(-21) / c - 1) * 100
o["iv"] = o[["iv_call", "iv_put"]].mean(axis=1) * 100
o["skew"] = (o["iv_put95"] * 100 - o["iv"])
o = o.join(pd.DataFrame({"ewma": ew, "frv": frv, "f5": f5, "f21": f21}), how="left").dropna(subset=["iv", "ewma", "frv"])
cut = o.index[int(len(o) * 0.6)]
tr, te = o[o.index < cut], o[o.index >= cut]
print(f"{len(o)} days with an implied volatility ({o.index[0].date()}..{o.index[-1].date()}); test from {cut.date()} ({len(te)} days)")
print(f"mean implied {o.iv.mean():.1f}%, mean realised next-21d {o.frv.mean():.1f}%, so implied exceeds realised by {(o.iv - o.frv).mean():+.1f} points on average (the variance risk premium)")
for nm, d in (("selection", tr), ("test", te)):
    print(f"  {nm:10} rank corr with next-21d realised vol: IV {d.iv.corr(d.frv, method='spearman'):+.3f}   EWMA {d.ewma.corr(d.frv, method='spearman'):+.3f}   n={len(d)}")
b, a = np.polyfit(np.log(tr.ewma), np.log(tr.iv), 1)                       # fitted on the selection period only
for d in (tr, te): d["iv_extra"] = np.log(d.iv) - (a + b * np.log(d.ewma))
o["vrp"] = o.iv - o.ewma
tr, te = o[o.index < cut].copy(), o[o.index >= cut].copy()
for d in (tr, te): d["iv_extra"] = np.log(d.iv) - (a + b * np.log(d.ewma))
tests = [("IV beyond EWMA -> next-21d realised vol", "iv_extra", "frv"), ("variance risk premium -> next-21d return", "vrp", "f21"),
         ("skew -> next-5d return", "skew", "f5"), ("skew -> next-21d return", "skew", "f21")]
print(f"\n{'hypothesis':42} {'rho train':>9} | {'rho test':>9} {'95% CI':>17} {'p':>7} {'n':>4}")
for name, x, y in tests:
    res = []
    for d in (tr, te):
        d = d.dropna(subset=[x, y]); blocks = (d.index.year * 12 + d.index.month).to_numpy()
        res.append(sr.test(blocks, d[x].to_numpy(), d[y].to_numpy(), reps=3000))
    path = os.environ.get("SIGNAL_LEDGER")
    if path:
        from pathlib import Path
        from min_agent.research import signal_test as st
        mk = lambda d_: st.CorrelationResult(int(d_["n"]), int(d_.get("D", 0)), d_["rho"], d_["lo"], d_["hi"], d_["p"])
        st.judge("options:" + name, mk(res[0]), mk(res[1]), ledger=Path(path))
    print(f"{name:42} {res[0]['rho']:+9.3f} | {res[1]['rho']:+9.3f} [{res[1]['lo']:+.3f},{res[1]['hi']:+.3f}] {res[1]['p']:7.4f} {res[1]['n']:4d}")
