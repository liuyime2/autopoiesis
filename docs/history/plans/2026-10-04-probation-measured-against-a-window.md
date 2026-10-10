# Probation is measured against a quantity no candidate can reach

Date: 2026-10-04
Status: **DONE - the unreachable threshold is reachable; whether probation now drains is a live measurement**
Baseline commit for rollback: `b17ea05`
Scope: `StrategyResult`, the daemon's evaluation, and the two rules in
`src/autopoiesis/strategy_engine.py` that mean "service owed to this candidate". No config
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

## 8. A third site, found while measuring the fix

The change above deliberately touched two rules. A third one turned out to be the same
question wearing different clothes:

```python
if strategy.lifecycle == "PROBATION" and result.cycles >= self.min_active_cycles:
```

This is the gate for the whole probation verdict block — degenerate pause, broker-verified
promotion, and decision-quality promotion all sit behind it. It asks "does this candidate
have enough history to judge?", which is a *service* question, so it belongs on
`cumulative_cycles` like the other two. Left on the window it means a candidate that has
served its whole 13-cycle budget but has 4 cycles in the current window receives **no verdict
at all** — not a refusal, an absence — and waits for a 50-cycle coincidence. Measured on the
live library, `trend-follow-buy-010` is exactly that case: 15 cumulative cycles, 10 scored
decisions, `PASS_SCREENED`, and 4 window cycles.

So all three probation-stage service questions now read `cumulative_cycles`, and the
windowed number is left only where the meaning is genuinely recent: failure rate, rejection
rate, score, and the exploration floor. That is the whole distinction, stated once:

| reads `cumulative_cycles` | reads windowed `cycles` |
|---|---|
| `_needs_probation` | `failure_rate`, `rejection_rate` |
| `_is_degenerate_no_exploration` (PROBATION) | `score` via `StrategySelector` |
| probation verdict gate (this one) | `exploration_floor_cycles` |

One test added: a candidate with its budget served and 0 window cycles is judged, and a
candidate still short of its budget is not.

## 9. Fourth site, and this one the daemon actually exercised

The daemon acted on the change above within minutes of the restart, and it acted wrongly.
That is worth more than any test.

```
2026-10-04T11:24:13  trend-follow-buy-010  decided -> PAUSED
                     "no exploration evidence over the evaluation window"
```

`trend-follow-buy-010` is the one candidate that had earned a ruling: 15 cumulative cycles,
10 scored decisions, `PASS_SCREENED`. Over its lifetime it was selected 15 times and made
**one BUY**. And the pause fired because the *windowed* `trade_attempts` was 0 — its single
BUY fell outside the last 50 records.

So the question "did this candidate ever try to trade?" — a lifetime question — was answered
with a five-hour window, and the answer flipped from yes to no purely because of where the
window boundary happened to fall. The strategy that had demonstrated it could act was
paused for not acting. This is the same instrument error as §1, now on the path that decides
promotion, and it is the reason the only candidate with a passing screen never became ACTIVE.

Note what is *not* wrong here: a BUY strategy that holds 14 times in 15 is a weak strategy,
and pausing it is defensible. What is wrong is that the evidence for the claim was five hours
of a fifteen-cycle history.

### The change

`cumulative_trade_attempts` alongside `cumulative_cycles`, from the same journal read, and the
degenerate check uses it. Both quantities answer the same question — how much has this
candidate been given, and what did it do with it — so they are counted together and carried
together. The windowed `trade_attempts` stays for the *promoted*-strategy path, where "has
stopped acting" genuinely is a recent-behaviour question.

### What would prove this wrong

| Prediction | Falsifier |
|---|---|
| A candidate that traded in its lifetime is not paused for never trading | Another pause citing "no exploration evidence" for a strategy with lifetime BUY/SELL decisions |
| The pause still fires on a candidate that never traded | A 13-cycle candidate with 0 lifetime attempts that is not paused |
| The distinction matters at the boundary | Both cases pausing, or neither |

The second row is the one that keeps this from becoming a licence to keep anything: a
candidate that genuinely never traded must still be paused, and it will be, because lifetime
attempts is 0 for it.

## 10. The 18 strategies the superseded rule paused — measured, and deliberately not repaired

The obvious next move after replacing a rule is to re-adjudicate what it decided. Measured
first, it does not pay.

```
the 18 strategies paused with reason "probation produced no exploration evidence"
  screen verdicts:  17 INCONCLUSIVE_INSUFFICIENT_EVIDENCE, 1 REJECT_POOR_DECISIONS
  with a PASS_SCREENED verdict:  0
  that ever attempted a trade:   1   (and that one is the REJECT, on 43 scored decisions)

  lifetime cycles when the old rule fired: 43, 9, 8, 7, 7, 7, 7, 7, 7, 6, 6, 6, 6, 5, 5, 5, 5, 5
  lifetime BUY/SELL: 1 for one of them, 0 for the other seventeen
```

Re-adjudicating under the current rules yields **zero promotions**: seventeen would get no
ruling at all (5-9 cumulative cycles against a budget of 13, so neither degenerate nor
promotable) and one would be retired on a verdict it already has. Meanwhile every one of them
would re-enter a probation queue that is **already over its capacity cap** — 17 candidates
against a cap of 13 — so the repair would deepen the exact deficit it was meant to remedy.

`PAUSED` is terminal in `_review_one`, so the loop will never revisit them on its own. That is
acceptable here for a measured reason rather than a hopeful one: the strategies the superseded
rule excluded are precisely the ones with nothing to offer. Seventeen never traded and cannot
be screened; the eighteenth traded once in 43 cycles and its own screen rejects it.

The audit trail is complete without a repair. Each pause is a journalled
`STRATEGY_LIFECYCLE_UPDATED` with a timestamp and a reason, and the rule that produced it is
in git history. A reader can see both the decision and the rule it came from, which is what
"auditable" has to mean.

**What would change this answer:** one of the eighteen earning a `PASS_SCREENED` verdict, or
the capacity cap being raised. Either would make re-adjudication worth its cost. Neither holds
today.
