# Two corrections, and a capability not added

Date: 2026-10-03
Status: **DONE - two claims corrected, one speculative capability reverted**
Baseline commit for rollback: `3af2403`
Scope: no source change. This turn is measurement and two corrections to the record.

---

## 0. What this turn set out to do

Last turn ended with: *"the decision-quality promotion path is now reachable - it has never
fired in the project's history."* The screen was verified; the promotion path was not. It
has four further conditions, and it does not need the market to test them.

## 1. Correction: the promotion path is still unreachable

Driving the **real** `StrategyLifecycleManager` with what the real daemon hands it - the
library on disk, the results from the real reflection snapshot, and the offline evidence
rebuilt from the real journal:

```
library: 60 strategies, lifecycles [PAUSED 33, PROBATION 14, RETIRED 11, BASELINE 1, ACTIVE 1]
reflection snapshot: 13 strategy results
offline evidence rebuilt: 42 strategies, 8 PASS_SCREENED

lifecycle decisions produced: 0
```

Zero. Every one of the eight strategies that now passes the screen is blocked, and each
blocker is individually correct:

| strategy | lifecycle | cycles ≥ 5 | submitted > 0 | scored | verdict |
|---|---|---|---|---|---|
| `fixed-size-buy-001` | RETIRED | 0 | 0 | 21 | PASS_SCREENED |
| `fixed-size-sell-005` | PAUSED | 5 | 2 | 12 | PASS_SCREENED |
| `tiny-fixed-size-001` | ACTIVE | 0 | 0 | 21 | PASS_SCREENED |
| `trend-follow-20260611-001` | PAUSED | 0 | 0 | 44 | PASS_SCREENED |
| `trend-follow-20260611-002` | PAUSED | 0 | 0 | 12 | PASS_SCREENED |
| `trend-follow-20260611-004` | PAUSED | 0 | 0 | 11 | PASS_SCREENED |
| `trend-follow-20260611-005` | PAUSED | 0 | 0 | 11 | PASS_SCREENED |
| `trend-follow-buy-010` | **PROBATION** | 4 | 0 | 10 | PASS_SCREENED |

The only PROBATION candidate is `trend-follow-buy-010`, and it is held back by
`submitted_orders > 0` - the guard established two turns ago as correct, because it is what
prevents promotion by inaction. So **the screen fix is necessary but not sufficient**: it
opened the gate, and nothing is currently standing in it.

Last turn's accurate claim is the first half only: a trading strategy can now *pass the
screen*. Whether one can be *promoted* depends on strategies being in PROBATION with
submitted orders, and there are none.

## 2. Correction: the `in_flight_order` fix is not yet free

Last turn concluded the three strategies killed by that defect were freed. **They are not,
and cannot be yet.**

Four strategies are PAUSED for "error rate above lifecycle threshold":
`fixed-size-probe-0001`, `trend-follow-20260627-001`, `fixed-size-sell-005`,
`fixed-size-sell-006`. Every one of their recorded errors is `in_flight_order` — the class
removed from the error channel — so recomputed over their whole history all four have an
error rate of **0.000**:

```
strategy                       cycles  err_all   rate  err_fixed   rate
fixed-size-probe-0001              16        3  0.188         0  0.000
trend-follow-20260627-001           5        2  0.400         0  0.000
fixed-size-sell-005                17        3  0.176         0  0.000
fixed-size-sell-006                 7        4  0.571         0  0.000
```

But the lifecycle reads `result.errors`, and that comes from the **reflection window** - the
last 50 cycles, `reflection_window = 50`. The window currently spans
`2026-10-02 11:03` to `15:58`, entirely *before* the fix went live, and **7 of the 12
historical `in_flight_order` records are still inside it**. The journal is append-only, so
those records are permanent; they will be counted until enough new cycles push them out.
`fixed-size-sell-006` still shows `cycles: 7, errors: 4` - a rate of 0.571 - and its pause
correctly still holds.

The market has been closed since Friday 16:00 EDT, so no cycle has been recorded since. The
condition lifts on its own, as a matter of arithmetic, once trading resumes.

So the honest statement of that fix is: it stops the misclassification reaching **future**
decisions, and the **past** records age out of a 50-cycle window. It is not a restoration of
the strategies it once killed, and it was never going to be immediate.

## 3. The capability I built and then deleted

Chasing section 2, I added `StrategyLifecycleManager.stale_pauses()` - a read-only
re-adjudication that re-runs the authoritative rule sequence over PAUSED strategies by
presenting them as PROBATION, and reports any whose current verdict differs from PAUSED.

It works, and it is correct by construction: it reuses `_review_one` rather than copying
its rules, and it changed no lifecycle state (60 strategies before and after).

**It returns 0 on every real strategy**, because the pause reasons still hold given the
window as currently recorded. Its non-zero case is hypothetical, and resolves itself as the
window rolls. Its first draft was also wrong in a way worth recording: it reported any
decision at all, so it flagged the four strategies paused for "probation produced no
exploration evidence" - which remains true of them. A check that reports 4 correct pauses as
stale is a check that cries wolf.

Deleted, not shipped. The objective asks every round *"are we solving a problem that has not
happened yet?"* and this was one: infrastructure with no instance, added ahead of evidence
that it earns its place. The tree is back to `3af2403` with nothing added.

## 4. What is left, honestly

1. **The promotion path is open and empty.** Reaching it needs a strategy in PROBATION with
   submitted orders and ≥10 scored decisions. Three strategies now pass the screen and two
   of them (`fixed-size-sell-005` at 0.583, `fixed-size-buy-001` at 0.762) have submitted
   orders; both are currently PAUSED/RETIRED on pause reasons that section 2 shows will
   lapse. So the chain is: window rolls → pause lapses → strategy returns to PROBATION →
   screen passes → promotion fires. **Every link except the first two is now verified.**
2. That it actually fires is a trading-day observation, and nothing offline can substitute
   for it.
3. The asymmetry between a wrong hold and a losing fill, with `fixed-size-sell-005` as the
   deciding case.
4. `STRATEGY_EVALUATION_RECORDED` ~2MB/day, and the `PNL_EVIDENCE_RECORDED` dedup that
   needs a cheap journal tail read.
