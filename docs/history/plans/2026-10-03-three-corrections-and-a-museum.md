# Three corrections, and a museum with six live exhibits

Date: 2026-10-03
Status: **DONE - three claims corrected, one structural fact established, no source change**
Baseline commit for rollback: `bfdbbea`
Scope: no source change. Measurement and corrections.

---

## 1. Retraction: "313 released cycles spread across strategies that trade"

Turn 8 reported that the selector change released 313 of 325 cycles "to the strategies that
do trade". Turn 10 measured where they land and got **310 of 328 cycles (94%) to a single
strategy**, `tiny-fixed-size-001`.

**That figure is not trustworthy, and it is retracted.** The harness drove the real selector
but passed the *original* `results` list every iteration, so a strategy's score never rose
as it accrued cycles. The real argmax sees improving scores and would rotate. The harness
measured an artefact of its own wiring, not the system.

I am not replacing it with another simulation. The claims below come from the recorded
snapshot and the library on disk, with no model in between.

What survives from turn 8's claim is only the half that was measured directly then: the
probation queue stopped spending cycles on strategies that would emit HOLD (30 wasted cycles
→ 0, replayed over the recorded price path). The "313 released" figure is withdrawn.

## 2. Correction: the chain has a missing link, not an untriggered one

Turn 9 ended with: *"window rolls → pause lapses → strategy returns to PROBATION → screen
passes → promotion fires. Every link except the first two is now verified."*

**There is no mechanism for the second-to-third link.** `StrategyLifecycleManager._review_one`
opens with:

```python
if strategy.lifecycle in {"BASELINE", "PAUSED", "RETIRED"}:
    return None
```

Nothing in the code moves a PAUSED strategy back. The only thing that ever did so was
`tools/readmit_misattributed.py`, a one-shot operator repair that was deleted as not a
reusable tool - a decision `docs/MIGRATION.md` records and which this measurement supports.

So when the window rolls and `fixed-size-sell-006`'s error rate falls from 0.571 to 0.000,
**nothing will happen.** Its recorded pause reason becomes false and its state stays PAUSED
forever. The chain is:

```
window rolls -> the pause reason stops holding -> (nothing) -> [missing link] -> screen -> promotion
```

## 3. The structural fact: 6 of 60 strategies can be selected at all

Directly from the library on disk and the last observed price (SPY 769.58):

```
library: 60
lifecycle: PAUSED 33, PROBATION 14, RETIRED 11, BASELINE 1, ACTIVE 1

PAUSED or RETIRED, so ineligible for selection at all   44
PROBATION that cannot trade at this price                9   (all TREND_FOLLOW)
PROBATION and tradeable now                              5
ACTIVE and tradable                                      1   (tiny-fixed-size-001)
------------------------------------------------------
selectable                                               6 of 60
```

The selector's own filter excludes PAUSED and RETIRED, and the fix from turn 8 correctly
skips the 9 dormant probation candidates. Both are working as designed. The consequence is
that **54 of 60 strategies are unreachable**, and the library is a museum with six live
exhibits: one ACTIVE strategy, two new FIXED_SIZE candidates, and three tradeable
TREND_FOLLOWs.

This is not a consequence of any single defect. It is what 60 strategies and 14 days of
6.5-hour sessions produce when the only thing that promotes is broker-verified PnL: the
library grows faster than anything can be evaluated, and the states that end evaluation
(PAUSED, RETIRED) are terminal by construction.

## 4. The exploration floor is dead permanently, and was before turn 8

`StrategySelector.select` reaches its escape hatch only when:

```python
if not explored and self._exploration_floor_engaged(result_by_id):
```

`explored` is every strategy with `result.trade_attempts > 0`. Two do -
`fixed-size-sell-005` and `fixed-size-sell-006`, both currently PAUSED. So `explored` is
non-empty, **the floor can never fire again**, and the condition that would re-arm it (no
strategy anywhere has traded) is unreachable while those two files exist.

The floor was written for the HOLD latch, where nothing had traded. It has been superseded
by a single verified winner, and nothing re-arms it. Note what the floor is *not* doing:
the probation queue still rotates, so the system is not fully stuck - but the argmax half of
the selector is.

## 5. What is not proposed, and why

Two changes are obviously indicated and neither is proposed this turn.

**A re-adjudication path for PAUSED strategies.** It has no instance today - turn 9 built
`stale_pauses()`, found it returns 0 on every real strategy, and deleted it for exactly that
reason. Its instance appears when the window rolls. Building it before then is the same
speculative move, and it changes strategy states, which is risk-relevant and belongs to the
objective's owner rather than to a measurement.

**Any change to selection policy.** The tension is now fully measured and is real:

- raising `min_probation_cycles` starves the one ACTIVE strategy (turn 8);
- leaving it at 3 gives each candidate ~2 informative decisions against a 10-decision gate,
  so nothing is ever screened (turn 1, unchanged);
- 44 of 60 strategies are terminal, so widening eligibility means changing what PAUSED and
  RETIRED mean, which is a larger semantic change than a constant.

Each of those is a policy decision about how much the live champion's throughput is worth
against the chance of finding a better strategy. The numbers to make it are now on the
record; the choice is not a measurement.

## 6. Still open

1. The missing link in section 2, whose instance arrives with the next trading day's cycles.
2. The selection-policy tension in section 5.
3. `STRATEGY_EVALUATION_RECORDED` ~2MB/day, and the `PNL_EVIDENCE_RECORDED` dedup that needs
   a cheap journal tail read.
4. Whether the champion's own result justifies its 100% of argmax cycles - broker-verified
   +362.66 is the only evidence that it does, and it is one closed lot.
