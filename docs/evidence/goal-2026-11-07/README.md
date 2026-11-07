# 2026-11-07 review: evidence for the fixes

| File | What it shows | Source of the data |
|---|---|---|
| `screen_false_pass_mc.py/.json` | **SYNTHETIC.** How often the offline screen passes a strategy with no skill: 2.2% under the current bar (clustered se), 49.6% under the pre-W2 bar (margin >= 0 on 10 days); with +0.10 skill 68.6% vs 99.6%. 2,000 runs per row, seed 20261107 | simulated decisions; the production `offline_validation.validate` is called unchanged |
| recovery drill (G0 0.5), run 2026-11-07 ~23:10 UTC | `journal_snapshot.py drill` on the live runtime: 16 files snapshotted, restored copy byte-identical, every figure the same (6,482 cycles, 33,212 events, 171 closed lots, realized 165.70, unrealized −367.37). `verify` of the newest hourly backup `20261107T230135Z-f07de4cd5a30`: whole; `restore` of it into an isolated scratch directory: figures identical to live | real runtime; scratch directories only, runtime untouched |
