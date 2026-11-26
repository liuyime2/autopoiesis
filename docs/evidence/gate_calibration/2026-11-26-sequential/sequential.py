"""READ-ONLY. The live screen re-run after every day, on SYNTHETIC strategies over journalled outcomes.

    PYTHONPATH=src python docs/evidence/gate_calibration/2026-11-26-sequential/sequential.py RUNTIME_DIR OUT.json

The rules are spec.md beside this file, written before the first run. Each prefix is screened once by
`offline_validation.validate`; every candidate threshold is then applied to the fields it returns,
and the current thresholds are asserted to reproduce its verdict.
"""
import collections
import glob
import hashlib
import json
import math
import multiprocessing
import random
import sys
import types
from pathlib import Path

from autopoiesis import counterfactual as cf
from autopoiesis import offline_validation as ov
from autopoiesis.learning_evidence import row_eligible

CUT = "2026-11-26"
RUNS = 2000
Z = (1.645, 2.0, 2.33, 2.58)
K = (2.0, 2.33, 2.58, 3.0)
POLICIES = (("null_cycle", 0.0), ("null_day", 0.0), ("null_hold", 0.0), ("skill", 0.5), ("skill", 1.0))
COST, BAND = cf.DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT, cf.DEFAULT_DEAD_BAND_PCT


def load(runtime: Path):
    files = sorted(glob.glob(str(runtime / "journal-archive/r*.jsonl")))
    files += [str(runtime / f"journal.jsonl{s}") for s in (".3", ".2", ".1", "")]
    events = []
    for name in files:
        with open(name) as fh:
            for line in fh:
                if "COUNTERFACTUAL_EVALUATED" not in line:
                    continue
                d = json.loads(line)
                stamp = str(d.get("timestamp") or "")
                if d.get("event_type") == "COUNTERFACTUAL_EVALUATED" and stamp < CUT:
                    events.append((stamp, types.SimpleNamespace(payload=d["payload"])))
    events = [e for _, e in sorted(events, key=lambda x: x[0])]
    latest = {}
    for event in events:
        for row in event.payload["rows"]:
            if row_eligible(row) and row.get("net_return_pct") is not None:
                latest[row["cycle_id"]] = row
    pools = collections.defaultdict(lambda: collections.defaultdict(list))
    for cycle in sorted(latest):
        row = latest[cycle]
        pools[row["symbol"]][str(row["decided_at"])[:10]].append(row["net_return_pct"] + COST)
    day_up = {s: ov.day_direction(events, symbol=s) for s in pools}
    kept = {s: {d: g for d, g in days.items() if len(g) >= 3 and d in day_up[s]} for s, days in sorted(pools.items())}
    return {s: d for s, d in kept.items() if len(d) >= 20}, day_up


def verdicts(r, z, k):
    """(passes, rejects) at thresholds z and k, from one screened prefix."""
    if r.day_margin is None or r.day_margin_se == 0.0:
        return False, False
    margin, se, clustered = r.day_margin, r.day_margin_se, r.day_margin_se_clustered
    if margin < -k * max(se, clustered):
        return False, True
    return r.passable and margin - z * clustered > 1e-6, False


def one(job):
    policy, q, seed = job
    rng = random.Random(seed)
    symbol = list(POOLS)[seed % len(POOLS)]
    days = rng.sample(sorted(POOLS[symbol]), len(POOLS[symbol]))
    records, held = [], {}
    ever_pass = {z: False for z in Z}
    ever_reject = {k: False for k in K}
    for day in days:
        for move in rng.sample(POOLS[symbol][day], 3):
            if policy == "null_day":
                action = held.setdefault(day, rng.choice(("BUY", "SELL")))
            elif policy == "null_hold":
                action = rng.choice(("BUY", "HOLD", "SELL"))
            else:
                right = [a for a in ("BUY", "SELL") if cf._grade(a, move, COST, BAND)[0] == ov.GOOD_TRADE]
                action = right[0] if policy == "skill" and rng.random() < q and right else rng.choice(("BUY", "SELL"))
            records.append(ov.DecisionRecord(cycle_id=str(len(records)), action=action,
                                             verdict=cf._grade(action, move, COST, BAND)[0],
                                             net_return_pct=move - COST, day=day, paired=True))
        result = ov.validate(records, strategy_id="x", day_up=DAY_UP[symbol])
        trades = result.good_trades + result.shadow_good_trades + result.false_trades
        seen = {d.day for d in records if d.verdict in ov.INFORMATIVE}
        result.passable = len(seen) >= ov.MIN_SCREEN_DAYS and trades >= ov.MIN_TRADE_EVIDENCE
        passes, rejects = verdicts(result, Z[0], K[0])
        # The reconstruction must agree with the screen itself at the current thresholds.
        real = (result.verdict == ov.PASS_SCREENED, result.verdict == ov.REJECT_POOR_DECISIONS)
        if (passes, rejects) != real:
            # Only a case within the fields' rounding of a threshold may differ.
            spread = max(result.day_margin_se, result.day_margin_se_clustered)
            gaps = (abs(result.day_margin + K[0] * spread),
                    abs(result.day_margin - Z[0] * result.day_margin_se_clustered))
            assert min(gaps) < 1e-5, (job, result.reason)
        # At the current thresholds the screen's own verdict is used.
        ever_pass[Z[0]] |= real[0]
        ever_reject[K[0]] |= real[1]
        for z in Z[1:]:
            ever_pass[z] |= verdicts(result, z, K[0])[0]
        for k in K[1:]:
            ever_reject[k] |= verdicts(result, Z[0], k)[1]
    return policy, q, [ever_pass[z] for z in Z], [ever_reject[k] for k in K]


def wilson_upper(k, n, z=1.96):
    p = k / n
    return (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


if __name__ == "__main__":
    POOLS, DAY_UP = load(Path(sys.argv[1]))
    jobs = [(p, q, seed) for p, q in POLICIES for seed in range(RUNS)]
    totals = collections.defaultdict(lambda: [[0] * len(Z), [0] * len(K)])
    with multiprocessing.get_context("fork").Pool(16) as pool:
        for policy, q, passes, rejects in pool.imap(one, jobs, chunksize=8):
            t = totals[policy if not q else f"{policy}_q{q}"]
            t[0] = [a + b for a, b in zip(t[0], passes)]
            t[1] = [a + b for a, b in zip(t[1], rejects)]
    cells = {name: {"ever_pass": {str(z): n / RUNS for z, n in zip(Z, t[0])},
                    "ever_pass_upper": {str(z): wilson_upper(n, RUNS) for z, n in zip(Z, t[0])},
                    "ever_reject": {str(k): n / RUNS for k, n in zip(K, t[1])},
                    "ever_reject_upper": {str(k): wilson_upper(n, RUNS) for k, n in zip(K, t[1])}}
             for name, t in totals.items()}
    nulls = [c for name, c in cells.items() if name.startswith("null")]
    chosen = {
        "pass_z": next((z for z in Z if all(c["ever_pass_upper"][str(z)] <= 0.05 for c in nulls)), None),
        "reject_k": next((k for k in K if all(c["ever_reject_upper"][str(k)] <= 0.05 for c in nulls)), None),
    }
    out = {"label": "SYNTHETIC strategies on real journalled outcomes", "cut": CUT, "runs": RUNS,
           "days_per_symbol": {s: len(d) for s, d in POOLS.items()},
           "data_sha256": hashlib.sha256(json.dumps(POOLS, sort_keys=True).encode()).hexdigest(),
           "chosen": chosen, "cells": cells}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, indent=1, sort_keys=True))
