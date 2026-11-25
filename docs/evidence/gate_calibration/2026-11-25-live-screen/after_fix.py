"""READ-ONLY: deployed screen vs fixed screen. Calibration (SYNTHETIC strategies, real outcomes) and live-journal recompute."""
import collections, glob, hashlib, importlib.util, json, math, random, sys, types
from autopoiesis.models import StrategySpec
from pathlib import Path
from autopoiesis import offline_validation as old          # PYTHONPATH = deployed release
spec_ = importlib.util.spec_from_file_location("ov_new", sys.argv[1]); new = importlib.util.module_from_spec(spec_); sys.modules["ov_new"] = new; spec_.loader.exec_module(new)
base = open("screen_cal.py").read()
exec(base.split("N = 1000")[0].replace('exec(open("live_impact.py").read().split("files = sorted")[0])',
     'RT = Path(__import__("os").environ.get("AUTOPOIESIS_RUNTIME", "runtime/autopoiesis")); ov = old'))
N = 4000; cells = {}; mismatches = 0
for pol, q in [("null_cycle",0),("null_day",0),("null_hold",0),("skill",0.25),("skill",0.5),("skill",1.0)]:
    cnt = collections.defaultdict(collections.Counter)
    for seed in range(N):
        rng, s, hist = history(seed); per = {}; acts = []
        for d, g in hist:
            if pol == "null_day": a = per.setdefault(d, rng.choice(("BUY","SELL")))
            elif pol == "null_hold": a = rng.choice(("BUY","HOLD","SELL"))
            elif pol == "skill" and rng.random() < q: a = correct_action(g, rng)
            else: a = rng.choice(("BUY","SELL"))
            acts.append((d, g, a))
        got = {}
        for name, mod in (("deployed", old), ("fixed", new)):
            for paired in (False, True):
                recs = [mod.DecisionRecord(cycle_id=str(i), action=a, verdict=cf._grade(a, g, cost, band)[0], net_return_pct=g-cost, day=d, paired=paired) for i,(d,g,a) in enumerate(acts)]
                v = mod.validate(recs, strategy_id="x", day_up=day_up[s]).verdict
                cnt[(name, paired)][v] += 1; got[(name, paired)] = v
        mismatches += got[("fixed", False)] != got[("fixed", True)]
    for (name, paired), c in sorted(cnt.items()):
        label = f"{pol}{q or ''}|{name}|paired={paired}"; p, r = c[old.PASS_SCREENED], c[old.REJECT_POOR_DECISIONS]
        cells[label] = {"runs": N, "pass": p, "reject": r, "pass_ci": wilson(p, N), "reject_ci": wilson(r, N)}
        if paired: print(f"{label:38s} PASS {p/N:6.1%} (up {wilson(p,N)[1]:.1%}) REJECT {r/N:6.1%} (up {wilson(r,N)[1]:.1%})")
print("fixed: paired/unpaired verdict mismatches", mismatches, "| data", data_sha[:16])
live = []
specs = [StrategySpec.model_validate_json(p.read_text()) for p in sorted((RT/"strategies").glob("*.json"))]
tab = collections.Counter(); du = {}
for sp in specs:
    sym = sp.symbols[0]
    if sym not in du: du[sym] = old.day_direction(ev, symbol=sym)
    a = old.validate(old.collect_decisions(ev, sp.strategy_id, strategy=sp), strategy_id=sp.strategy_id, day_up=du[sym])
    b = new.validate(new.collect_decisions(ev, sp.strategy_id, strategy=sp), strategy_id=sp.strategy_id, day_up=du[sym])
    if a.decisions: tab[(a.verdict, b.verdict)] += 1
    if a.verdict != b.verdict:
        live.append({"strategy_id": sp.strategy_id, "lifecycle": sp.lifecycle, "reason_cites_screen": "offline validation rejected" in sp.lifecycle_reason,
                     "deployed": a.verdict, "fixed": b.verdict, "scored": a.scored, "margin_deployed": a.day_margin, "margin_fixed": b.day_margin})
for k, v in sorted(tab.items()): print(v, "deployed=%s -> fixed=%s" % k)
for r in live: print(r["strategy_id"], r["lifecycle"], r["reason_cites_screen"], r["deployed"], "->", r["fixed"])
json.dump({"label": "SYNTHETIC strategies on real outcomes; live recompute is read-only", "deployed": "02a3d5e", "cut": CUT, "data_sha256": data_sha,
           "mismatches": mismatches, "cells": cells, "live_changed": live, "live_table": [[*k, v] for k, v in sorted(tab.items())]}, open(sys.argv[2], "w"), indent=1, sort_keys=True)
