# 2026-10-31 review: evidence for the fixes

| File | What it shows | Source of the data |
|---|---|---|
| `admission_replay.py/.json` | The 18 RULE proposals made since the ledger-aware build, replayed through the new ledger rule: 18 of 18 refused | real strategy files + real signal ledger |
| `screen_rescore.py/.json` | The 13 strategies that PASS_SCREENED today, re-scored under the ten-day and five-trade requirements: 0 of 13 keep the pass (they rest on 1-8 days) | real journal |
| `sleeve_exclusive_replay.py/.json` | **Simulation.** Real SPY bars, simulated account, real loop and Guardian, a hostile rival engine: rival alone makes 3,475 trades; with the sleeve the rival is never asked and only the sleeve trades (55) | cached real bars; the account and the rival are simulated and labelled |
| `first_open_check.py` | The first-open checklist as one read-only command | real heartbeat, journal, library, deployment record |

Corrections to the 2026-10-31 review made while building these: the TLT strategy passed on 72 right *trades*
and 0 holds (not on holds), from 125 cycles on 8 days; every deployment did have a full-gate record (five were
only in release directories and are now committed).

## The first-open check is not a vacuous pass (negative control, 2026-10-31)

`first_open_check.py` judges order behaviour only on cycles that ran under the current build. That scoping was added
after the check reported the old behaviour on a date from before the pause, so it deserves a control. `--since` moves
the build start. On the same real data (2026-10-30, 196 cycles):

| Build start | Cycles judged | TLT not added to | No refused strategy admitted |
|---|---|---|---|
| recorded (2026-10-31 18:55) | 0 of 196 | PASS (nothing to judge) | PASS |
| `--since 2026-10-01` | 196 of 196 | **FAIL: 11 BUYs by trend-follow-tlt-buy-001** | **FAIL: lists the strategies the new rules refuse** |

On a trading day under the new build the first row's checks judge real cycles; the second row shows they can fail.
