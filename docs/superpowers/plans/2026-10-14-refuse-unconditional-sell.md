# P1.3 — refuse a sell spec that cannot express a condition

2026-10-14. Plan before edit, per the repository rule.

## Problem

Measured on the live account. The curriculum treats "this position has no strategy that
can sell it" as a gap, and `FIXED_SIZE` is the only kind it reached for. `FIXED_SIZE`
has no way to express a condition:

    FIXED_SIZE      action, quantity, confidence
    TREND_FOLLOW    reference_price, threshold_pct, quantity, confidence
    RULE            signal, lookback, threshold_pct, when_above, when_below, quantity, confidence

So `fixed-size-tlt-sell-001` is not "a sell that rarely fires". It is a sell that cannot
not fire. Selected in a cycle, it sells, and the only thing that stops it is the position
running out. Broker-verified: TLT 188 shares, XLE and XLF emptied entirely. Today alone
five TLT shares, every ~32 minutes.

The three were paused through `strategy_pause` on 2026-10-14 at 12:09:36 EDT and the
pause was observed to hold: zero TLT sell intents in the 77 minutes after, against a
cadence that had put two or more due. That stopped the loss. It did not stop a fourth.

## Baseline

A probe of this shape is admitted today. `make verify` 53/55, `make check` 1840 passed.
The admission gate has `_reference_price_rejection_reason`, `_progression_rejection_reason`
and the capacity check, and none of them asks whether a sell can ever decline to fire.

## Change

One refusal in `StrategyAdmission.admit()`, before the capacity and progression checks,
because it is a property of the spec alone and does not depend on library state:

- A strategy whose `parameters.action == "SELL"` must carry at least one market-trigger
  field. If it carries none, refuse.

The trigger set is derived from the parameter schemas rather than typed out separately,
so a kind that gains a trigger is recognised without editing two lists.

This is deliberately kind-agnostic. The test is "does this spec have something that can
be false", not "is this kind FIXED_SIZE" — a future kind with no condition would be
caught, and a `FIXED_SIZE` that somehow gained one would be allowed.

## What this does not do

- It does not add the cumulative cap ("at most one fill, then end"). That is a runtime
  budget and belongs to the lifecycle, not to admission; it is the other half of P1.3 and
  is still open.
- It does not stop a sell with a condition from being badly specified. That is what the
  reference-price band and the duplicate check are for.
- It does not un-pause anything. The three stay paused until they are re-proposed with a
  condition the library can express.

## Success criterion

- A `FIXED_SIZE SELL 1` is refused, with a reason naming the missing condition.
- A `TREND_FOLLOW` sell and a `RULE` sell are unaffected.
- A `FIXED_SIZE BUY` is unaffected: an unconditional buy is capped by Guardian's position
  and cash limits, and a buy cannot drain a position that was never opened.
- The refusal is journalled by the existing `STRATEGY_ADMISSION_REVIEWED` path.
- Focused tests, then the frozen full check.

## Stop condition

If refusing this spec turns out to make the library unable to express any exit for a
position — an uncovered position with no sellable strategy — then the honest outcome is
not to weaken the rule but to record that the position has no exit, which is the state
`HOLD_ZERO_WITHOUT_ATTRIBUTION` already describes. That must be visible, not papered over.