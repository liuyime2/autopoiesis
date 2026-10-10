# The admission gate asks whether a candidate is good, never whether it can be used

Date: 2026-10-04
Status: **DONE - admission is refused when the queue is full; whether the backlog drains is a live measurement**
Baseline commit for rollback: `e02a8ff`
Scope: `src/autopoiesis/strategy_admission.py` and its tests, plus the daemon's wiring. No new
module, no queue manager, no scheduler.

---

## 0. The arithmetic

Everything the loop can do with a candidate costs cycles, and one cycle serves one strategy.
Over the whole record:

```
market-open cycles:            716
  of which probation received: 92%  (measured, not assumed - see below)
candidates serviceable:         716 x 0.92 / 13 cycles each  =  51
candidates admitted:                                            68
net queue growth:                                               +17
```

Per market day, on the days admission actually ran:

| day | dow | cycles | mkt-open | admitted | serviceable | net |
|---|---|---|---|---|---|---|
| 2026-09-28 | Mon | 69 | 69 | 12 | 4.9 | −7.1 |
| 2026-09-30 | Wed | 61 | 59 | 15 | 4.2 | −10.8 |
| 2026-10-01 | Thu | 66 | 65 | 8 | 4.6 | −3.4 |
| 2026-10-02 | Fri | 65 | 65 | 10 | 4.6 | −5.4 |
| 2026-10-03 | Sat | 0 | 0 | 4 | 0.0 | −4.0 |
| 2026-10-04 | Sun | 0 | 0 | 3 | 0.0 | −3.0 |

Capacity is about 4.6 candidates a market day. Admission has been running at 8–15 on the days
it runs, and **7 candidates were admitted across a weekend, when capacity is zero by
definition.**

Why the deficit matters rather than merely existing: `StrategySelector.select` returns from
the probation queue whenever any candidate is actionable, and only considers the incumbent
otherwise. Reconstructing the lifecycle of the selected strategy *at each cycle* - not its
lifecycle now, which is a different question - probation took **134 of the 146 cycles (92%)**
since the last promotion. So a candidate that cannot be served does not merely fail to help:
it is a claim on the cycles the one ACTIVE strategy needs, and the ACTIVE strategy has been
selected 0 times in 146 cycles.

## 1. Why the existing gate cannot see this

Every refusal `StrategyAdmission` makes is candidate-intrinsic: duplicate id, reference price
far from the market, curriculum progression, symbol outside the allowlist, behaviourally
identical to an existing strategy, duplicate TREND_FOLLOW parameters. Each answers "is this
candidate sound?" None answers "can this loop use it?"

That is not an oversight in the individual rules — it is a missing question, and it is the
question the deficit is asking.

## 2. The minimal change

One more refusal, in the same shape as the existing ones, plus the one number it needs.

1. `StrategyAdmission` takes `max_probation_queue` (default `PROBATION_CYCLES`, reusing the
   constant rather than introducing a third budget) and a `probation_backlog()` reading the
   library: how many strategies are in `PROBATION` and still short of the budget.
2. `admit` refuses when the backlog is already at the cap, with a reason that states the
   number and what to do instead.
3. The daemon supplies the backlog each cycle from the cumulative counts it already computes
   for `cumulative_cycles`.

**Why refuse rather than schedule.** A queue that grows faster than it drains is not a
scheduling problem; it is an admission-rate problem, and the objective's own ordering puts
deletion and refusal ahead of adding a scheduler. There is already a refusal path, a reason
string convention, and a journalled `STRATEGY_ADMISSION_REVIEWED` event to record it in — so
this adds a rule, not infrastructure.

**Why `PROBATION_CYCLES` and not a day of cycles.** A cap expressed in candidates is
environment-independent. Thirteen candidates in probation is about three market days of
service at the measured rate, which is enough to keep the queue busy and small enough that it
drains. A cap in cycles-per-day would encode this machine's 5-minute interval into a risk-like
rule, which is the kind of number that silently becomes wrong on another host.

## 3. What this does and does not fix

It stops the queue growing. It does **not** clear the 17 candidates already in probation, so
the ACTIVE strategy still waits for roughly 17 / 4.6 ≈ 3.7 market days. That is the honest
sequence: the deficit stops compounding, then the backlog drains, then the incumbent trades.
Nothing here should be reported as making the loop profitable.

| Prediction | Falsifier |
|---|---|
| Admissions stop while the queue is deep | Further ACCEPTED events with the backlog at the cap |
| The backlog then falls | Backlog still at 17+ a week later |
| The incumbent eventually trades | The ACTIVE strategy still unselected once the queue empties |
| Refusal is visible, not silent | No `STRATEGY_ADMISSION_REVIEWED` event carrying the reason |

## 4. Risks this introduces, stated before implementing

- **The loop could stop learning.** If the cap is too tight the curriculum proposes nothing
  new and the library stagnates. The cap is deliberately loose relative to current service
  (13 candidates against a serviceable 4.6/day), so this should not bind in the near term.
- **The refusal could mask a curriculum bug.** A curriculum that cannot propose anything
  distinct would now be refused for capacity rather than for being a duplicate. The reason
  string names the backlog, so the two are distinguishable in the journal.

## 5. Tests

- A candidate is refused when the probation backlog is at the cap, and the reason states the
  backlog.
- The same candidate is admitted when the backlog is one below the cap, so the rule cannot
  pass by refusing everything.
- A backlog of zero admits normally — the default must not refuse a fresh library.
- Strategies in `PAUSED`, `RETIRED` or `BASELINE`, and `PROBATION` strategies that have
  already served their budget, do not count toward the backlog. Without that last one the cap
  would be computed from the wrong population and would never fall.

## 6. Verification

- `ruff`, `mypy`, the admission and curriculum tests, then the full `make verify`.
- The behavioural claim — the backlog falls and the incumbent trades — is a live measurement
  and is listed above as a prediction, not asserted here.

## 7. Result

The refusal exists, it binds on live data (backlog 17 against a cap of 13), and the reason
string states the backlog so a capacity refusal is distinguishable in the journal from a
duplicate or a progression failure. Four tests: refused at the cap, admitted one below it, a
candidate that has served its budget does not hold the queue, and a fresh library refuses
nothing.

**The measurement corrected itself once.** The first pass concluded that 86% of cycles went to
PAUSED strategies, which would have meant the selector was trading strategies the lifecycle
had already rejected. That came from reading each strategy's lifecycle *now* rather than at
selection time. Reconstructed per cycle, probation took 134 of 146 — and `eligible` does
exclude PAUSED and RETIRED, so there was never a boundary defect. Three separate measurement
errors in this session came from the same three shortcuts: reading one journal file, reading
current state as historical, and reading a windowed number as a cumulative one.

**Not closed:** the 17 candidates already in probation. Admission refusing stops the queue
growing; it does not drain it.
