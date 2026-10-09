"""Post-publication drift for in-session headlines, with a 5-minute reaction latency.

react  : open of the 5-minute bar holding the publication time -> open of the entry bar (NOT tradable;
         the positive control that the story moved the price and the alignment is right)
t30,t60: entry-bar open -> close 30 / 60 minutes later, less SPY over the same bars (TRADABLE)
Intervals resample calendar weeks."""
import datetime as dt
import json, sys
import numpy as np
import pandas as pd

LAT = dt.timedelta(minutes=5)

def parse(ts): return dt.datetime.fromisoformat(ts)

def outcome(ev):
    pub = parse(ev["t"]); bars = [(parse(a), o, c) for a, o, c in ev["bars"]]; spy = {parse(a): (o, c) for a, o, c in ev["spy"]}
    hold = next((b for b in bars if b[0] <= pub < b[0] + dt.timedelta(minutes=5)), None)
    entry = next((b for b in bars if b[0] >= pub + LAT), None)
    if hold is None or entry is None or entry[0] not in spy or hold[0] not in spy: return None
    def px(start, minutes):  # close of the bar that ENDS `minutes` after `start`
        t = start + dt.timedelta(minutes=minutes - 5)
        b = next((b for b in bars if b[0] == t), None)
        return (b[2], spy.get(t, (None, None))[1]) if b and t in spy else (None, None)
    c30, s30 = px(entry[0], 30); c60, s60 = px(entry[0], 60)
    if None in (c30, s30, c60, s60): return None
    o_e, so_e = entry[1], spy[entry[0]][0]; o_h, so_h = hold[1], spy[hold[0]][0]
    return dict(day=entry[0].date(), react=((o_e / o_h - 1) - (so_e / so_h - 1)) * 100, t30=((c30 / o_e - 1) - (s30 / so_e - 1)) * 100, t60=((c60 / o_e - 1) - (s60 / so_e - 1)) * 100)

def spearman_boot(df, col, reps=3000, seed=4):
    ok = df.dropna(subset=["signal", col]); x = ok.signal.rank().to_numpy(); z = ok[col].rank().to_numpy()
    wk = pd.to_datetime(ok.day).dt.isocalendar(); blocks, _ = pd.factorize(wk.year * 100 + wk.week); B = blocks.max() + 1
    S = np.vstack([np.bincount(blocks), np.bincount(blocks, x), np.bincount(blocks, z), np.bincount(blocks, x * x), np.bincount(blocks, z * z), np.bincount(blocks, x * z)]).T.astype(float)
    def corr(s):
        n, sx, sy, sxx, syy, sxy = s; vx = sxx / n - (sx / n) ** 2; vy = syy / n - (sy / n) ** 2
        return (sxy / n - sx * sy / n / n) / np.sqrt(vx * vy) if vx > 0 and vy > 0 else np.nan
    rho = corr(S.sum(0)); rng = np.random.default_rng(seed)
    v = np.array([corr(np.bincount(rng.integers(0, B, B), minlength=B).astype(float) @ S) for _ in range(reps)]); v = v[~np.isnan(v)]
    return len(ok), rho, np.quantile(v, .025), np.quantile(v, .975), max(2 * min((v <= 0).mean(), (v >= 0).mean()), 1 / reps)

if __name__ == "__main__" and not (len(sys.argv) > 1 and sys.argv[1] == "--ledger"):
    events = json.load(open(sys.argv[1])); outs = {e["id"]: outcome(e) for e in events}
    for path in sys.argv[2:]:
        sc = {s["id"]: s for s in json.load(open(path))}
        rows = [dict(id=i, signal=sc[i]["pos"] - sc[i]["neg"], **o) for i, o in outs.items() if o and i in sc]
        df = pd.DataFrame(rows); name = path.split("/")[-1].replace("news_", "").replace(".json", "")
        print(f"\n== {name}: {len(df)} in-session events, {df.day.min()}..{df.day.max()}")
        for col, label in (("react", "reaction before entry (not tradable)"), ("t30", "TRADABLE +30 min"), ("t60", "TRADABLE +60 min")):
            n, rho, lo, hi, p = spearman_boot(df, col)
            hi3 = df[df.signal >= df.signal.quantile(2 / 3)][col]; lo3 = df[df.signal <= df.signal.quantile(1 / 3)][col]
            print(f"   {col:5} {label:38} rho {rho:+.4f} [{lo:+.4f},{hi:+.4f}] p={p:.4f} | top third {hi3.mean():+.3f}%  bottom third {lo3.mean():+.3f}%  spread {hi3.mean() - lo3.mean():+.3f}%")


def record_to_ledger(events_path, score_paths, ledger):
    """The tradable in-session hypotheses (+30 and +60 minutes) into the signal ledger."""
    from pathlib import Path
    from autopoiesis.research import signal_test as st
    events = json.load(open(events_path)); outs = {e["id"]: outcome(e) for e in events}
    for path in score_paths:
        sc = {s["id"]: s for s in json.load(open(path))}
        df = pd.DataFrame([dict(id=i, signal=sc[i]["pos"] - sc[i]["neg"], **o) for i, o in outs.items() if o and i in sc])
        name = path.split("/")[-1].replace("news_", "").replace(".json", "")
        cut = st.time_split(list(df.day), 0.6)
        for col in ("t30", "t60"):
            res = {}
            for label, sub in (("sel", df[df.day < cut]), ("test", df[df.day >= cut])):
                ok = sub.dropna(subset=["signal", col]); wk = pd.to_datetime(ok.day).dt.isocalendar()
                res[label] = st.block_rank_correlation(list(wk.year * 100 + wk.week), ok.signal.tolist(), ok[col].tolist(), reps=2000)
            st.judge(f"news-intraday:{name}:{col}", res["sel"], res["test"], ledger=Path(ledger))


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--ledger":
    record_to_ledger(sys.argv[2], sys.argv[4:], sys.argv[3])
