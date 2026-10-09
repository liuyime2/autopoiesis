"""One-minute bars: does another asset's last five minutes lead SPY, and does order-flow imbalance?

Pre-stated (24): QQQ, IWM, DIA, TLT, XLF, XLE five-minute return -> SPY's next 5/15/30 minutes (18);
SPY's signed-volume imbalance and close-location imbalance over five minutes -> next 5/15/30 (6).
Regular session only. Selection: the earlier 60% of days; test: the later 40%, read once. The lead-lag
hypotheses also run with SPY's own last-five-minute move removed from the leader's (the control: a leader
that merely moves with SPY predicts nothing). Intervals resample whole days.
Usage: intraday_1min.py BARS1M.pkl"""
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import signal_research as sr   # day-block rank correlation

LEADERS = ["QQQ", "IWM", "DIA", "TLT", "XLF", "XLE"]
HORIZONS = (5, 15, 30)


def ledger(name, tr, te, control=None):
    path = os.environ.get("SIGNAL_LEDGER")
    if not path: return
    from pathlib import Path
    from autopoiesis.research import signal_test as st
    mk = lambda d: st.CorrelationResult(int(d["n"]), int(d.get("D", 0)), d["rho"], d["lo"], d["hi"], d["p"])
    st.judge(name, mk(tr), mk(te), control=mk(control) if control else None, ledger=Path(path))


def per_day(frame_close: pd.DataFrame):
    return frame_close.groupby(frame_close.index.date)


if __name__ == "__main__":
    raw = pd.read_pickle(sys.argv[1])
    close = pd.DataFrame({s: raw[s]["close"] for s in raw.columns.get_level_values(0).unique()}).dropna()
    opn = raw["SPY"]["open"].reindex(close.index); hi = raw["SPY"]["high"].reindex(close.index); lo = raw["SPY"]["low"].reindex(close.index); vol = raw["SPY"]["volume"].reindex(close.index)
    day = pd.Series(close.index.date, index=close.index)
    g = close.groupby(day.to_numpy())
    feats = {}
    for s in LEADERS:
        feats[f"{s}_r5"] = close[s] / g[s].shift(5) - 1
    spy_r5 = close["SPY"] / g["SPY"].shift(5) - 1
    signed = np.sign(close["SPY"] - opn) * vol
    clv = ((close["SPY"] - lo) - (hi - close["SPY"])) / (hi - lo).replace(0, np.nan) * vol
    gv = vol.groupby(day.to_numpy())
    feats["flow_signed5"] = signed.groupby(day.to_numpy()).transform(lambda x: x.rolling(5, min_periods=5).sum()) / gv.transform(lambda x: x.rolling(5, min_periods=5).sum())
    feats["flow_clv5"] = clv.groupby(day.to_numpy()).transform(lambda x: x.rolling(5, min_periods=5).sum()) / gv.transform(lambda x: x.rolling(5, min_periods=5).sum())
    fwd = {h: (g["SPY"].shift(-h) / close["SPY"] - 1) for h in HORIZONS}
    days = sorted(set(day))
    cut = days[int(len(days) * 0.6)]
    print(f"{len(days)} sessions {days[0]}..{days[-1]}; test from {cut}; 24 hypotheses")
    print(f"{'hypothesis':22} {'rho train':>9} | {'rho test':>9} {'95% CI':>17} {'p':>7} | with SPY's own move removed")
    for name, x in feats.items():
        beta = None
        for h in HORIZONS:
            y = fwd[h]
            tr_mask = (day < cut).to_numpy(); te_mask = ~tr_mask
            def test(mask, xv):
                return sr.test(day[mask].to_numpy(), xv[mask].to_numpy(), y[mask].to_numpy(), reps=1200 if mask is tr_mask else 2500)
            a, b = test(tr_mask, x), test(te_mask, x)
            ctrl = None
            if name.endswith("_r5"):
                ok = (day < cut).to_numpy() & x.notna().to_numpy() & spy_r5.notna().to_numpy()
                beta = np.polyfit(spy_r5[ok], x[ok], 1)[0]            # slope fitted on the selection period only
                resid = x - beta * spy_r5
                ctrl = test(te_mask, resid)
            ledger(f"1min:{name}->SPY+{h}m", a, b, ctrl)
            c = f"{ctrl['rho']:+.3f} [{ctrl['lo']:+.3f},{ctrl['hi']:+.3f}]" if ctrl else "-"
            print(f"{name + '->+' + str(h) + 'm':22} {a['rho']:+9.4f} | {b['rho']:+9.4f} [{b['lo']:+.4f},{b['hi']:+.4f}] {b['p']:7.4f} | {c}")
