# 2026-11-07 review: evidence for the fixes

| File | What it shows | Source of the data |
|---|---|---|
| `screen_false_pass_mc.py/.json` | **SYNTHETIC.** How often the offline screen passes a strategy with no skill: 2.2% under the current bar (clustered se), 49.6% under the pre-W2 bar (margin >= 0 on 10 days); with +0.10 skill 68.6% vs 99.6%. 2,000 runs per row, seed 20261107 | simulated decisions; the production `offline_validation.validate` is called unchanged |
