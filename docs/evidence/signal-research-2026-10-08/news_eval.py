"""Event study: does the model's reading of a headline predict the market-adjusted return that follows?

Entry is the first regular-session OPEN after the publication time (never a price the reader could
not have traded). R1 = open->close of the entry day; R3 = entry open -> close two days later; both
less SPY over the same window. Intervals resample calendar weeks (stories cluster by day)."""
import json, sys
import numpy as np
import pandas as pd

def load_panel(path):
    p = pd.read_pickle(path)
    return p.xs("open", axis=1, level=1), p.xs("close", axis=1, level=1)

def events(scores, openp, closep):
    days = openp.index
    rows = []
    for s in scores:
        ts = pd.Timestamp(s["t"]); ts = ts.tz_convert("America/New_York") if ts.tzinfo else ts.tz_localize("UTC").tz_convert("America/New_York")
        day = pd.Timestamp(ts.date())
        # published before the open of a trading day -> that day; otherwise the next trading day
        pos = days.searchsorted(day)
        if pos >= len(days): continue
        if days[pos] == day and ts.time() >= pd.Timestamp("09:30").time(): pos += 1
        if days[pos] != day and days[pos] < day: continue
        if pos + 2 >= len(days) or s["sym"] not in openp.columns: continue
        o = openp[s["sym"]].iloc[pos]; c1 = closep[s["sym"]].iloc[pos]; c3 = closep[s["sym"]].iloc[pos + 2]
        so = openp["SPY"].iloc[pos]; sc1 = closep["SPY"].iloc[pos]; sc3 = closep["SPY"].iloc[pos + 2]
        if not np.isfinite([o, c1, c3, so, sc1, sc3]).all(): continue
        after_hours = ts.time() < pd.Timestamp("09:30").time() or ts.time() >= pd.Timestamp("16:00").time()
        pc = closep[s["sym"]].iloc[pos - 1] if pos >= 1 else np.nan; spc = closep["SPY"].iloc[pos - 1] if pos >= 1 else np.nan
        gap = ((o / pc - 1) - (so / spc - 1)) * 100 if np.isfinite([pc, spc]).all() and after_hours else np.nan
        rows.append(dict(id=s["id"], sym=s["sym"], day=days[pos], signal=s["pos"] - s["neg"], pos=s["pos"], neg=s["neg"], gap=gap,
                         r1=((c1 / o - 1) - (sc1 / so - 1)) * 100, r3=((c3 / o - 1) - (sc3 / so - 1)) * 100))
    return pd.DataFrame(rows)

def spearman_boot(df, y, reps=3000, seed=4):
    ok = df.dropna(subset=["signal", y]); x = ok.signal.rank().to_numpy(); z = ok[y].rank().to_numpy()
    blocks, _ = pd.factorize(ok.day.dt.isocalendar().year * 100 + ok.day.dt.isocalendar().week); B = blocks.max() + 1
    S = np.vstack([np.bincount(blocks), np.bincount(blocks, x), np.bincount(blocks, z), np.bincount(blocks, x * x), np.bincount(blocks, z * z), np.bincount(blocks, x * z)]).T.astype(float)
    def corr(s):
        n, sx, sy, sxx, syy, sxy = s; vx = sxx / n - (sx / n) ** 2; vy = syy / n - (sy / n) ** 2
        return (sxy / n - sx * sy / n / n) / np.sqrt(vx * vy)
    rho = corr(S.sum(0)); rng = np.random.default_rng(seed); v = np.array([corr(np.bincount(rng.integers(0, B, B), minlength=B).astype(float) @ S) for _ in range(reps)])
    return len(ok), rho, np.quantile(v, .025), np.quantile(v, .975), max(2 * min((v <= 0).mean(), (v >= 0).mean()), 1 / reps)

if __name__ == "__main__" and not (len(sys.argv) > 1 and sys.argv[1] == "--ledger"):
    openp, closep = load_panel(sys.argv[1])
    for path in sys.argv[2:]:
        df = events(json.load(open(path)), openp, closep)
        name = path.split("/")[-1].replace("news_", "").replace(".json", "")
        print(f"\n== {name}: {len(df)} events, {df.sym.nunique()} stocks, {df.day.min().date()}..{df.day.max().date()}; mean signal {df.signal.mean():+.3f}")
        for y in ("gap", "r1", "r3"):
            n, rho, lo, hi, p = spearman_boot(df, y)
            hi_t = df[df.signal >= df.signal.quantile(2 / 3)][y]; lo_t = df[df.signal <= df.signal.quantile(1 / 3)][y]
            label = {"gap": "reaction (prior close -> next open, after-hours news only; NOT tradable)", "r1": "tradable day-1 (entry at open)", "r3": "tradable 3-day"}[y]
            print(f"   {y} {label}: n={n} rho {rho:+.4f} [{lo:+.4f},{hi:+.4f}] p={p:.4f} | top-third {hi_t.mean():+.3f}%  bottom-third {lo_t.mean():+.3f}%  spread {hi_t.mean() - lo_t.mean():+.3f}%  (long-short gross, before ~0.10-0.20% costs)")


def record_to_ledger(panel_path, score_paths, ledger):
    """Every tradable news hypothesis (model x window) into the signal ledger: earlier 60% of event
    days is the selection period, the later 40% the test, as for every other hypothesis."""
    from pathlib import Path
    from min_agent.research import signal_test as st
    openp, closep = load_panel(panel_path)
    for path in score_paths:
        df = events(json.load(open(path)), openp, closep)
        name = path.split("/")[-1].replace("news_", "").replace(".json", "")
        cut = st.time_split(list(df.day), 0.6)
        for y in ("r1", "r3"):
            res = {}
            for label, sub in (("sel", df[df.day < cut]), ("test", df[df.day >= cut])):
                ok = sub.dropna(subset=["signal", y])
                weeks = ok.day.dt.isocalendar()
                res[label] = st.block_rank_correlation(list(weeks.year * 100 + weeks.week), ok.signal.tolist(), ok[y].tolist(), reps=2000)
            st.judge(f"news:{name}:{y}", res["sel"], res["test"], ledger=Path(ledger))


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--ledger":
    record_to_ledger(sys.argv[2], sys.argv[4:], sys.argv[3])
