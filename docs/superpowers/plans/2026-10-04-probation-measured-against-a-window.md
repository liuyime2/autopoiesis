# Probation is measured against a quantity no candidate can reach

Date: 2026-10-04
Status: **DONE - the unreachable threshold is reachable; whether probation now drains is a live measurement**
Baseline commit for rollback: `b17ea05`
Scope: `StrategyResult`, the daemon's evaluation, and the two rules in
`src/min_agent/strategy_engine.py` that mean "service owed to this candidate". No config
change, no new module, no persisted counter.

---

## 0. Why this is the same defect as `e9317a6`, one level down

`e9317a6` found that the probation budget (`StrategySelector.min_probation_cycles = 13`) and
the probation-stage verdict (`StrategyLifecycleManager.min_active_cycles = 5`) were two
numbers for one concept, and made them one constant. That was correct and it is not enough,
because **both numbers were being compared against the wrong quantity.**

`result.cycles` comes from `ReflectionMemory.strategy_results`, which the daemon builds from
`journal.last_n(config.reflection_window)`. The window is **50 cycle records**, which spans
about five hours and is shared by every strategy that traded in it.

```
reflection_window = 50                    (config.py:65)
last_n(50) spans 2026-10-02T11:03 -> 15:58, i.e. ~5 hours
  11 strategies appear in it
  cycles per strategy: 7 6 6 5 5 5 4 4 3 3 2 0 0
  max any one strategy can hold: 7
```

So the quantity a probation budget is compared against **cannot exceed 7**, and both the
budget and the verdict are 13.

## 1. What that costs, measured

```
PROBATION candidates on disk: 18
  still owing probation in the current window: 18   (every one of them)
  cycles they still owe:                            223
ACTIVE strategies: 1  (tiny-fixed-size-001)
  cycles it was selected in the 146 since promotion: 0
```

`StrategySelector.select` returns from the probation queue whenever any candidate is
actionable at the current price, and only considers the incumbent otherwise. With 18
candidates that never leave probation, **probation has absolute priority forever** and the one
promoted strategy never trades. Promotion has happened once on the current format and the
incumbent has not run since.

The same unreachable threshold disables the pause. `_is_degenerate_no_exploration` compares
the same `result.cycles` against `probation_cycles`:

```
degenerate pauses that would fire on the current window: 0
  before e9317a6 (threshold 5): 0    after e9317a6 (threshold 13): 0
```

Neither fires today, so `e9317a6` caused no live regression — but it moved a threshold from a
reachable value (5, against a maximum of 7) to an unreachable one (13). That is the same
error it set out to fix, one level down: **the disagreement was real, and both sides of it
were wrong.**

## 2. Root cause: one concept, two different quantities

"Cycle" means two things in this code and the difference is load-bearing:

| meaning | used for | source | right value |
|---|---|---|---|
| cycles in the last 50 | error and rejection *rates*, scoring, the exploration floor | reflection window | 0-7 today |
| cycles **served to this candidate** | has it had its fair share of probation | the journal, cumulatively | 0-44 today |

Probation used the first for the second. Measured against lifetime service from the full
record, 42 strategies have ever been selected and **9 have served 13 or more cycles** — those
nine have earned the right to leave probation, and none of them can.

## 3. The minimal change

One field, one source, two call sites.

1. `StrategyResult` carries `cumulative_cycles: int = 0` — cycles this strategy has been
   selected across the whole journal, as opposed to `cycles` in the current window.
2. The daemon's evaluation populates it from `journal.read_all()`. **The daemon already calls
   `read_all()` eight times per cycle** (daemon.py lines 481, 549, 560, 833, 839, 856, 999),
   so this adds no new read of anything.
3. Exactly two rules switch to it, because exactly two rules mean "service owed":
   - `StrategySelector._needs_probation` — "has it had its 13 cycles?"
   - the `PROBATION` branch of `_is_degenerate_no_exploration` — "has it had its budget and
     still never tried to trade?"

Everything else keeps the windowed number, deliberately: failure and rejection *rates* are
recent-reliability concepts, the exploration floor is defined in windows, and `score` is a
window judgement. Changing those would be changing their meaning, not fixing a bug.

The `result.cycles <= 0` guard at the top of `_review_one` stays windowed as well — it exists
to avoid ruling on a strategy with nothing in the current window, which is a freshness
question, not a service question.

Not done, deliberately: the window stays at 50. Raising it to make 13 reachable per strategy
would need ~234 cycles, roughly a day and a half of wall time, and would make every score a
day and a half stale — that trades one wrong number for a worse one.

## 4. What this should produce, and how it can be wrong

If the instrument is the bug, then giving probation a reachable exit should let the queue
drain. Against today's library, nine strategies already hold 13+ cumulative cycles, so the
queue should shorten immediately rather than after 223 cycles.

| Prediction | Falsifier |
|---|---|
| The queue drains | Still 18 candidates owing probation after a full window |
| The incumbent trades | The ACTIVE strategy still unselected after its probation queue empties |
| Promotion becomes reachable | Still no promotion once candidates clear their budget |
| The pause still works | A 0-trade candidate with 13 cumulative cycles is not paused |

The second row is the one that matters most and it is the one that cannot be settled by a
test: an ACTIVE strategy that trades is a claim about the live record.

## 5. Tests

- `_needs_probation` is False for a result with `cumulative_cycles=13, cycles=4` — the case
  that is impossible today.
- `_is_degenerate_no_exploration` still pauses a `PROBATION` candidate with 13 cumulative
  cycles and no trade attempt, and does not pause one at 12.
- The failure and rejection **rates** still use the window: a result with 1 error in 4 window
  cycles is still paused for error rate even when cumulative cycles are large. Without this,
  the change would silently convert a rate into a lifetime ratio.
- A `StrategyResult` built the old way, with no `cumulative_cycles`, behaves exactly as
  before — so the 892 existing tests are evidence the default is inert.

## 6. Verification

- `ruff`, `mypy`, the lifecycle and governance tests, then the full `make verify`.
- The behavioural claim — probation drains and the incumbent trades — is recorded as a
  prediction above, not asserted here.

## 7. Result

`cumulative_cycles` threads from `journal.read_all()` through the evaluator and the
reflection into `StrategyResult`, and the two rules that mean "service owed" read it. Four
tests added: a served candidate leaves probation on an empty window, an unserved one stays
whatever the window says, the rates stay windowed, and a result built without the new field
behaves exactly as before — which is what makes the other 892 tests evidence rather than
coincidence.

Measured against the live library, the queue moves from 18 owing to 17, and
`trend-follow-buy-010` (15 lifetime cycles) is released immediately. The instrument is fixed;
the queue is still deep, because 16 of the 18 candidates have never been selected at all.
That is a serving-capacity problem and it is now the binding constraint, not a measurement
one: 9 actionable candidates x 13 cycles against ~36 cycles a day is roughly three days
before the incumbent can trade.

**What would show this worked:** the count of candidates owing probation falling below 17,
and then the ACTIVE strategy being selected. Neither is observable in a test, and neither is
asserted here.
