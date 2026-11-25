import collections, json, math, random, sys
exec(open("live_impact.py").read().split("files = sorted")[0])
from autopoiesis import counterfactual as cf
from autopoiesis.learning_evidence import row_eligible
import glob, types
files = sorted(glob.glob(str(RT / "journal-archive/r*.jsonl"))) + [str(RT / f"journal.jsonl{s}") for s in (".3", ".2", ".1", "")]
events = []
# frozen cutoff so a rerun reads the same rows while the daemon keeps appending
CUT = "2026-11-25"
for f in files:
    with open(f) as fh:
        for line in fh:
            if "COUNTERFACTUAL_EVALUATED" not in line: continue
            d = json.loads(line)
            ts = str(d.get("timestamp") or d.get("created_at") or "")
            if d.get("event_type") == "COUNTERFACTUAL_EVALUATED" and ts < CUT:
                events.append((ts, types.SimpleNamespace(payload=d["payload"])))
events.sort(key=lambda x: x[0]); ev = [e for _, e in events]
latest = {}
for e in ev:
    for r in e.payload["rows"]:
        if row_eligible(r) and r.get("net_return_pct") is not None: latest[r["cycle_id"]] = r
cost, band = cf.DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT, cf.DEFAULT_DEAD_BAND_PCT
pools = collections.defaultdict(lambda: collections.defaultdict(list))
for c in sorted(latest):
    r = latest[c]; pools[r["symbol"]][str(r["decided_at"])[:10]].append(r["net_return_pct"] + cost)
pools = {s: {d: g for d, g in days.items() if len(g) >= 3} for s, days in pools.items()}
pools = {s: d for s, d in sorted(pools.items()) if len(d) >= 20}
day_up = {s: ov.day_direction(ev, symbol=s) for s in pools}
data_sha = hashlib.sha256(json.dumps(pools, sort_keys=True).encode()).hexdigest()
def wilson(k, n, z=1.96):
    p = k / n; den = 1 + z*z/n; c = (p + z*z/(2*n))/den; h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return max(0, c-h), min(1, c+h)
def history(seed):
    rng = random.Random(seed); s = list(pools)[seed % len(pools)]
    days = [d for d in rng.sample(sorted(pools[s]), len(pools[s])) if d in day_up[s]][:20]
    return rng, s, [(d, g) for d in days for g in rng.sample(pools[s][d], 3)]
def correct_action(g, rng):
    ok = [a for a in ("BUY", "SELL") if cf._grade(a, g, cost, band)[0] == ov.GOOD_TRADE]
    return ok[0] if ok else rng.choice(("BUY", "SELL"))
N = 1000; out = {}
for pol, q in [("null_cycle",0),("null_day",0),("null_hold",0),("skill",0.25),("skill",0.5),("skill",1.0)]:
    cnt = collections.defaultdict(collections.Counter)
    for seed in range(N):
        rng, s, hist = history(seed); per_day = {}
        acts = []
        for d, g in hist:
            if pol == "null_day": a = per_day.setdefault(d, rng.choice(("BUY","SELL")))
            elif pol == "null_hold": a = rng.choice(("BUY","HOLD","SELL"))
            elif pol == "skill" and rng.random() < q: a = correct_action(g, rng)
            else: a = rng.choice(("BUY","SELL"))
            acts.append((d, g, a))
        for paired in (False, True):
            for name, mod in (("deployed", ov), ("counted", fix)):
                recs = [mod.DecisionRecord(cycle_id=str(i), action=a, verdict=cf._grade(a, g, cost, band)[0],
                        net_return_pct=g - cost, day=d, paired=paired) for i, (d, g, a) in enumerate(acts)]
                cnt[(name, paired)][mod.validate(recs, strategy_id="x", day_up=day_up[s]).verdict] += 1
    for (name, paired), c in cnt.items():
        label = f"{pol}{'' if not q else f'_q{q}'}|{name}|paired={paired}"
        p, r = c[ov.PASS_SCREENED], c[ov.REJECT_POOR_DECISIONS]
        out[label] = {"runs": N, "pass": p, "reject": r, "pass_ci": wilson(p, N), "reject_ci": wilson(r, N)}
        print(f"{label:44s} PASS {p/N:6.1%} [{wilson(p,N)[0]:.1%},{wilson(p,N)[1]:.1%}]  REJECT {r/N:6.1%}")
print("symbols", {s: len(d) for s, d in pools.items()}, "data_sha", data_sha[:16])
json.dump({"label": "SYNTHETIC strategies on real journalled outcomes", "release": "02a3d5e88ff2", "cut": CUT,
           "data_sha256": data_sha, "symbols": {s: len(d) for s, d in pools.items()}, "cells": out},
          open(sys.argv[1], "w"), indent=1, sort_keys=True)
