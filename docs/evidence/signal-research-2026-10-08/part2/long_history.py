"""The daily hypotheses again, on 28 years of ETFs with bear markets in them.

Pool: SPY, nine sector ETFs, QQQ, IWM, DIA (1998-2026), dividend-adjusted. ETFs carry no
single-stock survivorship. Selection before 2015-01-01 (the dot-com bust and 2008 are in it), test
from 2015-01-01 (2018, 2020 and 2022 are in it). The same 18 hypotheses as daily_research.py.
Usage: long_history.py YAHOO.pkl"""
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import daily_research as dr

SPLIT = pd.Timestamp("2015-01-01")
POOL = ["SPY", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "QQQ", "IWM", "DIA"]


def frames_from(path):
    raw = pd.read_pickle(path); out = {}
    for s in POOL:
        d = raw[s].dropna(subset=["close", "adjclose"])
        k = d.adjclose / d.close
        out[s] = pd.DataFrame({"o": d.open * k, "h": d.close * k, "l": d.close * k, "c": d.adjclose, "v": d.volume}).dropna(subset=["c"])
        out[s] = out[s][out[s].c > 0]
    return out


REVERSAL_NOTE = ("US equity-index timing only: it does not replicate on bonds, gold or foreign equities, vanishes between "
                 "sectors once the market's common move is removed, and the dip rule built on it has no risk-adjusted benefit "
                 "(long_robustness.py, dip_rule.py)")


def ledger(name, tr, te, note=None):
    path = os.environ.get("SIGNAL_LEDGER")
    if not path: return
    from pathlib import Path
    from min_agent.research import signal_test as st
    mk = lambda d: st.CorrelationResult(int(d["n"]), 0, d["rho"], d.get("lo", np.nan), d.get("hi", np.nan), d.get("p", np.nan))
    st.judge(name, mk(tr), mk(te), ledger=Path(path), extra={"downstream": note} if note else None)


if __name__ == "__main__":
    frames = {s: dr.features(f) for s, f in frames_from(sys.argv[1]).items()}
    k = len(dr.HYP)
    print(f"{len(frames)} ETFs, {min(f.index[0] for f in frames.values()).date()}..{max(f.index[-1] for f in frames.values()).date()}, {k} hypotheses; test from {SPLIT.date()}")
    print(f"{'hypothesis':14} {'train rho':>9} | {'test rho':>9} {'95% CI':>17} {'p':>7} {'n':>6} | in 2000-02 / 2007-09 / 2020 / 2022")
    for fx, fy in dr.HYP:
        tr = dr.corr_boot(*dr.pooled(frames, fx, fy, before=SPLIT), reps=1200)
        te = dr.corr_boot(*dr.pooled(frames, fx, fy, after=SPLIT), reps=3000)
        eps = []
        for a, b in (("2000-03-01", "2002-10-31"), ("2007-10-01", "2009-03-31"), ("2020-02-15", "2020-04-30"), ("2022-01-01", "2022-10-31")):
            sub = {s: f[(f.index >= a) & (f.index <= b)] for s, f in frames.items()}
            r = dr.corr_boot(*dr.pooled(sub, fx, fy), reps=200)["rho"]
            eps.append(r)
        ledger(f"long:13etf:{fx}->{fy}", tr, te, note=REVERSAL_NOTE if not fx.startswith("RV") else None)
        same = np.sign(tr["rho"]) == np.sign(te["rho"]); sig = te["p"] < 0.05 / k
        print(f"{fx + '->' + fy:14} {tr['rho']:+9.3f} | {te['rho']:+9.3f} [{te['lo']:+.3f},{te['hi']:+.3f}] {te['p']:7.4f} {te['n']:6d} | "
              + " ".join(f"{v:+.2f}" for v in eps) + ("  <== SURVIVES" if sig and same else ("  (significant, sign flipped)" if sig else "")))
