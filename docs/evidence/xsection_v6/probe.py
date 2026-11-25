"""EXPLORATORY probe behind the v6 choice: development window only (bars before 2025-11-01).

    python docs/evidence/xsection_v6/probe.py [UNIVERSE_DIR]

Real returns, no synthetic data, no holdout bar read. For 7 scores x rebalance every 1 or 5
sessions x top half or top tenth (28 configurations): excess over all-member equal weight,
tracking error, IR, turnover a year and the cost per unit turnover that would erase the excess,
all gross of cost. Run 2026-11-24 to choose v6; kept so the selection it made can be audited.
Writes probe.json and probe.md beside this file.
"""
import sys, json, time
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
import numpy as np
from run_gate_calibration import load_universe_panel
from run_xsection_v5 import DEV, window

cutoff, first, last = DEV
t0 = time.time()
UNIVERSE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runtime" / "autopoiesis" / "replay" / "universe-1Day-adjall"
panel, facts = load_universe_panel(UNIVERSE,
                                   cutoff, top_n=500, grid_step=5, workers=16)
start, end = window(panel["calendar"], first, last)
syms = sorted(panel["returns"]); ix = {s: i for i, s in enumerate(syms)}
R = np.array([panel["returns"][s] for s in syms], dtype=float)          # symbols x T
FIRST = np.array([panel["first"][s] for s in syms])
grid, members = panel["grid"], panel["members"]
logR = np.log1p(R); cum = np.concatenate([np.zeros((len(syms), 1)), np.cumsum(logR, 1)], 1)  # cum[:, t] = sum r[<t]
print("panel", round(time.time() - t0), "s", R.shape, flush=True)

def comp(t, a, b):  # compounded log return over sessions [t-a, t-b)
    return cum[:, t - b] - cum[:, t - a]
def vol(t, n):
    return R[:, t - n:t].std(1)
# goodness scores: higher = hold. need = sessions of history required
SIG = {
    "REV_1D": (2, lambda t: -R[:, t - 1]),
    "REV_5D": (6, lambda t: -comp(t, 5, 0)),
    "REV_1M": (22, lambda t: -comp(t, 21, 0)),  # need = window + 1: the probe is a day stricter than xsection
    "MOM_12_1": (253, lambda t: comp(t, 252, 21)),
    "LOWVOL_3M": (64, lambda t: -vol(t, 63)),
}
def rank01(x, m):
    out = np.full(x.shape, np.nan); v = x[m]; order = v.argsort(kind="stable")
    r = np.empty(len(v)); r[order] = np.arange(len(v)); out[m] = r / max(1, len(v) - 1); return out
def combo(names):
    def f(t, m):
        return np.nanmean([rank01(SIG[n][1](t), m) for n in names], 0)
    return f

def run(score_fn, need, every, frac):
    g = 0; held = np.zeros(len(syms), bool); bench, strat, turns = [], [], []
    for t in range(start, end):
        while g + 1 < len(grid) and grid[g + 1] <= t: g += 1
        mem = np.zeros(len(syms), bool); mem[[ix[s] for s in members[g]]] = True
        if (t - start) % every == 0:
            elig = mem & (FIRST <= t - need)
            sc = score_fn(t, elig)
            k = int(mem.sum() * frac); cand = np.where(elig)[0]
            pick = cand[np.lexsort((cand, -sc[cand]))][:k]   # ties by symbol order
            new = np.zeros(len(syms), bool); new[pick] = True
            turns.append((new ^ held).sum() / (2 * max(1, k)) if held.any() else 1.0); held = new
        bench.append(R[mem, t].mean()); strat.append(R[held, t].mean())
    d = np.array(strat) - np.array(bench)
    ex, te = d.mean() * 252, d.std() * 252 ** .5
    turn_yr = sum(turns[1:]) / ((end - start) / 252)
    return {"excess": ex, "te": te, "ir": ex / te, "turnover_per_yr": turn_yr,
            "breakeven_bps_per_unit_turnover": ex / turn_yr * 1e4 if turn_yr else None}

out = {}
cases = [(n, need, f) for n, (need, f) in SIG.items()] + [
    ("COMBO_REV5_LOWVOL_MOM", 253, None), ("COMBO_REV1_REV5", 6, None)]
for name, need, f in cases:
    fn = (lambda t, m, f=f: f(t)) if f else combo(["REV_5D", "LOWVOL_3M", "MOM_12_1"] if "MOM" in name else ["REV_1D", "REV_5D"])
    for every in (1, 5):
        for frac in (0.5, 0.1):
            r = run(fn, need, every, frac); out[f"{name}|every={every}|top={frac}"] = r
            print(f"{name:24s} every={every} top={frac}: excess {r['excess']:+.2%} TE {r['te']:.2%} IR {r['ir']:+.2f} "
                  f"turn/yr {r['turnover_per_yr']:.0f} breakeven {r['breakeven_bps_per_unit_turnover']:.1f} bps", flush=True)
sessions = [str(panel["calendar"][start]), str(panel["calendar"][end - 1])]
Path(__file__).with_name("probe.json").write_text(json.dumps(
    {"label": "EXPLORATORY dev-window probe, real returns, gross of cost", "sessions": sessions,
     "universe_set_sha256": facts.get("set_sha256"), "results": out}, indent=1, sort_keys=True) + "\n")
md = [f"# v6 probe: 28 exploratory configurations, development window {sessions[0]} .. {sessions[1]}", "",
      "EXPLORATORY, real returns, gross of cost; no holdout bar read. Best of 28 is optimistic by selection.", "",
      "| score | every | top | excess/yr | TE | IR | turnover/yr | break-even bps |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
for key, r in out.items():
    name, every, top = key.split("|")
    md.append(f"| {name} | {every.split('=')[1]} | {top.split('=')[1]} | {r['excess']:+.2%} | {r['te']:.2%} | "
              f"{r['ir']:+.2f} | {r['turnover_per_yr']:.0f} | {r['breakeven_bps_per_unit_turnover']:.1f} |")
Path(__file__).with_name("probe.md").write_text("\n".join(md) + "\n")
print("total", round(time.time() - t0), "s")
