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
