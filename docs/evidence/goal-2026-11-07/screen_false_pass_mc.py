"""SYNTHETIC Monte Carlo: how often does the offline screen pass a strategy with no skill?

Every number this prints comes from simulated decisions, not the journal. It measures the
screen's own error rate (plan item 1.6 / W2), which no real record can: on real data the skill
is unknown. The production function `offline_validation.validate` is called unchanged.

Model (labelled assumptions):
- a strategy is scored on N_DAYS trading days, with 1..15 decisions per day (clustered, as
  the live loop produces them);
- each day has a base up-share p ~ U(0.2, 0.8), which is what `day_direction` gives the screen;
- this strategy's own outcomes that day share one draw r ~ Beta(p*K, (1-p)*K), K = 4: decisions
  inside one day are correlated, which is the pseudo-replication the clustered bar is for;
- a decision is BUY or SELL with equal odds; skill d raises the chance it is right by d.

The "old bar" is the pre-W2 rule (margin >= 0 on >= 10 days, >= 5 trades); the "current bar"
is what `validate` returns now. Usage: python screen_false_pass_mc.py [runs] [seed]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis import offline_validation as ov  # noqa: E402

N_DAYS = 20
K = 4.0


def one_strategy(rng: random.Random, skill: float):
    day_up, records = {}, []
    for d in range(N_DAYS):
        day = f"2026-01-{d + 1:02d}"
        p = rng.uniform(0.2, 0.8)
        day_up[day] = p
        r = rng.betavariate(p * K, (1 - p) * K)
        for i in range(rng.randint(1, 15)):
            action = rng.choice(("BUY", "SELL"))
            p_right = (r if action == "BUY" else 1 - r) + skill
            right = rng.random() < min(1.0, max(0.0, p_right))
            records.append(ov.DecisionRecord(
                cycle_id=f"{day}-{i}", action=action,
                verdict=ov.GOOD_TRADE if right else ov.FALSE_TRADE,
                net_return_pct=0.1 if right else -0.1, day=day,
            ))
    return records, day_up


def run(runs: int, seed: int) -> dict:
    rng = random.Random(seed)
    out = {}
    for skill in (0.0, 0.05, 0.10, 0.15):
        current = old = 0
        for _ in range(runs):
            records, day_up = one_strategy(rng, skill)
            res = ov.validate(records, strategy_id="mc", day_up=day_up)
            current += res.verdict == ov.PASS_SCREENED
            days = len({r.day for r in records})
            old += (
                res.day_margin is not None and res.day_margin >= 0 and days >= ov.MIN_SCREEN_DAYS
                and (res.day_margin_se or 0) > 0 and res.good_trades >= ov.MIN_TRADE_EVIDENCE
            )
        out[f"skill={skill:.2f}"] = {"runs": runs, "pass_current_bar": current / runs, "pass_old_bar": old / runs}
    return out


if __name__ == "__main__":
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20261107
    result = {"synthetic": True, "seed": seed, "n_days": N_DAYS, "beta_concentration": K, "results": run(runs, seed)}
    print(json.dumps(result, indent=2))
