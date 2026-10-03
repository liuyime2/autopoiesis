# Stop charging a correctly-refused duplicate order to the strategy

Date: 2026-10-03
Status: **DONE - implemented, measured, gated**
Baseline commit for rollback: `26c341d`
Scope: `src/min_agent/loop.py`, `tests/min_agent/test_loop.py`,
`tests/min_agent/test_score_latch.py`, plus the figures three documents quote.

Follows [`2026-10-03-refuse-untradeable-strategy.md`](2026-10-03-refuse-untradeable-strategy.md),
which fixed the admission gate. This one fixes the lifecycle.

---

## 0. The measurement that started it

The previous plan's next question was how many selected cycles a candidate needs to reach
10 informative decisions. Measuring per-strategy informative rate over the recent window
(328 market-open cycles, 2026-09-28 to 2026-10-02) produced this:

| Strategy | Informative | Selected | Rate | Lifecycle |
|---|---|---|---|---|
| `fixed-size-buy-001` | 21 | 23 | 0.91 | **RETIRED** |
| `fixed-size-sell-005` | 14 | 17 | 0.82 | **PAUSED** |
| `fixed-size-probe-0001` | 12 | 16 | 0.75 | **PAUSED** |
| `tiny-fixed-size-001` | 3 | 6 | 0.50 | ACTIVE |
| `fixed-size-sell-006` | 3 | 7 | 0.43 | **PAUSED** |

The strategies that trade are the ones that are dead. The only survivor has the fewest
trades of the group. `FIXED_SIZE` trades at 0.43-0.91; almost every `TREND_FOLLOW` trades
at 0.00.

**The rate is not the question, though.** A `FIXED_SIZE` needs 11-13 selected cycles to
reach 10 informative decisions, and the probation budget is 3 - a real mismatch, and the
next thing to measure. But it is not why these particular strategies died. Their recorded
retirement reasons are not "insufficient cycles".

## 1. Why they died

Counting Guardian rejections per strategy and classifying each with the production
`is_system_rejection`:

```
fixed-size-sell-005:    8 rejections, 8 system-caused, 0 strategy fault, 3 errors
fixed-size-probe-0001:  2 rejections, 2 system-caused, 0 strategy fault, 3 errors
fixed-size-sell-006:    0 rejections, 0 system-caused, 0 strategy fault, 4 errors
fixed-size-buy-001:    12 rejections,12 system-caused, 0 strategy fault, 0 errors
tiny-fixed-size-001:    2 rejections, 2 system-caused, 0 strategy fault, 0 errors
```

The rejection channel is clean - the `SYSTEM_REJECTION_REASONS` fix holds, zero strategy
faults. But **every strategy carrying errors has the same error**, and the two with none
are the two that survived.

Every one of those errors is `in_flight_order: an identical order was submitted in cycle ...`

## 2. The defect

`loop.run_once` refuses to re-submit an order it already submitted and has not seen filled.
On firing it rewrites the decision to a fail-safe `HOLD`, runs nothing, takes no risk - and
then appended the skip to `errors`, which becomes `record.error`.

`DeterministicEvaluator` turns `record.error` into `StrategyResult.errors`, and the
lifecycle computes:

```
failure_rate = (errors + fault_rejections) / cycles
  >= 0.75  ->  RETIRED  "severe operational failure rate"
  errors/cycles > 0.25  ->  PAUSED  "error rate above lifecycle threshold"
```

So the guard that prevents a doubled position was counted as the strategy failing
operationally. And it selects for inaction, because **a strategy that wants to trade while
its order is pending is precisely a strategy that trades.** The measured result is the
table above: 14, 12 and 3 informative decisions, all dead; 3 and 0, both alive.

This is the same defect as `SYSTEM_REJECTION_REASONS`, on the other channel. That fix split
Guardian rejections into system-caused and strategy-fault so the system could stop charging
a correct refusal to the strategy. The error channel never got that split.

### What is deliberately *not* changed

The `if result.submitted_orders <= 0: return None` guard on the promotion path. Five
strategies carry `PASS_SCREENED` with 10-44 scored decisions and have never been promoted,
because they emitted zero BUY and zero SELL - they held, correctly, and the counterfactual
ledger scored those holds positively.

That looked like the bug. It is not. The guard's stated purpose is to stop promotion "by
inaction", the defect this repository already fixed once, and `ARCHITECTURE.md` names that
fix. Removing the guard would promote "holds well" to ACTIVE, which is evidence of not
losing money, not evidence of making it. The guard is correct; the hypothesis that it was
a bug was tested and dropped.

## 3. The change

**Delete one line.** The skip stays on the cycle twice over: the decision's own `rationale`
carries the specific reason, and the executor returns `SKIPPED`, which the evaluator counts
as a skipped order. Nothing is lost, and `record.error` goes back to meaning "this cycle
went wrong" - which is the only meaning the failure rate can be computed from.

The two `errors.append` sites that remain are real failures: `decision_error` and
`trade_counter_error`, both of which fail closed to a HOLD.

## 4. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| A refused duplicate is not an operational failure | An `in_flight_order` cycle that actually risked a position, lost money, or left the system in a bad state |
| Dropping it stops the lifecycle killing traders | `severe operational failure rate` / `error rate above lifecycle threshold` still firing on strategies whose only errors were in-flight skips |
| Genuine failures are still caught | A broker ERROR or a `decision_error` cycle no longer counting toward the failure rate |

`test_a_charged_in_flight_skip_would_have_retired_the_strategy` pins the counterfactual
directly: the same cycle with the skip on the error channel still retires the strategy, so
the test suite holds both states and the change is the only difference between them.

`test_an_in_flight_skip_is_not_charged_as_a_strategy_error` reads `loop.py` and asserts the
`errors.append` is absent from that block. Re-adding it is a one-line change that every
metric-level test would still pass, so the arithmetic alone is not a sufficient guard.

## 5. Verification

- `make verify`: 49 classes, 0 failed, 1286 test executions across 61 files, 831 collected.
- `make lint` clean; `make type` clean on 41 files.
- Daemon restarted onto the new code (fingerprint `824b3717884b8c7d`).

## 6. Still open, stated so it is not lost

1. **The probation/screen mismatch, now measured.** A `FIXED_SIZE` needs 11-13 selected
   cycles to reach the 10-informative gate; `min_probation_cycles` is 3 and
   `min_active_cycles` is 5. Raising the probation budget is not free - probation has
   absolute priority over ACTIVE in the selector, so a deeper budget diverts cycles from
   strategies that currently trade. The number to measure first is what the trade-off is
   worth, not what the new constant should be.
2. **98% of offline screens are redundant.** 9661 of 9832 `OFFLINE_VALIDATION_COMPLETED`
   events repeat the same strategy's previous verdict unchanged, and 98 strategies are
   re-screened on a fixed cadence regardless of whether their decisions changed. The
   verdict is a pure function of recorded decisions, so most of this is recomputation of a
   known answer - and it is most of why the journal is 51MB. Whether to skip unchanged
   screens needs the answer to one question first: does anything downstream distinguish
   "not screened recently" from "screened, unchanged"?
