# Refuse a strategy that cannot express a view about the market it is admitted into

Date: 2026-10-03
Status: **DONE - implemented, measured, gated**
Baseline commit for rollback: `b40e947`
Scope: `src/autopoiesis/strategy_admission.py`, `src/autopoiesis/curriculum.py`,
`tests/autopoiesis/test_llm_provenance.py`, plus the figures three documents quote. No new
check class: the rule is guarded by unit tests, because the replay that measures it reads
`runtime/` and cannot run on a fresh clone.

---

## 0. The question this answers

The objective asks, every round: *what is the biggest real bottleneck right now, and is
it a capability problem or an infrastructure problem?*

Measured answer: **neither, exactly. It is a selection problem.** The evolution loop is
fully built - curriculum, admission, offline screening, probation, lifecycle, knowledge -
and it is not selecting, because most of what it selects cannot be judged.

## 1. The baseline, as a measurement

Everything below is counted from `runtime/autopoiesis/journal.jsonl` (1179 cycle records,
16317 typed events, 2026-06-10 to 2026-10-03) and from `runtime/autopoiesis/strategies/`.
None of it is estimated.

### 1.1 The library cannot be fed

| Fact | Value |
|---|---|
| Strategies admitted, ever | 61 |
| Strategies on disk now | 57 |
| Admitted strategies that produced **zero** informative (BUY/SELL) decisions, ever | **52 / 61** |
| Admitted strategies that ever cleared the ≥10-informative-decision screen gate | **4 / 61** |
| Offline screens returning `INCONCLUSIVE_INSUFFICIENT_EVIDENCE` | 90 of 113 |
| Lifecycle changes retiring a strategy for `probation produced no exploration evidence` | 36 |

The screen gate needs 10 informative decisions. At the observed rate of ~66 market-open
cycles/day, and 57 strategies competing for them, a candidate cannot reach the gate. The
library is roughly an order of magnitude larger than the evidence supply can feed.

### 1.2 Selection pressure runs backwards

In the most recent window (2026-09-28 to 2026-10-02, 328 market-open cycles):

| Strategy | Informative decisions | Lifecycle now |
|---|---|---|
| `fixed-size-buy-001` | 21 | **RETIRED** |
| `fixed-size-sell-005` | 14 | **PAUSED** |
| `fixed-size-probe-0001` | 12 | **PAUSED** |
| `tiny-fixed-size-001` | 3 | ACTIVE |

The three strategies that produced the most real trading evidence are dead. The only
ACTIVE strategy has the fewest of the four.

### 1.3 Why most of the library cannot trade: the frozen spot anchor

A `TREND_FOLLOW` fires when price leaves `[ref·(1−thr), ref·(1+thr)]`. 38 of the 57
strategies on disk are `TREND_FOLLOW`. 26 of them have a `reference_price` between 762
and 770 - a frozen SPY spot price from the day the proposal was made, differing from each
other only in the second decimal place.

Measured against the price range the system is currently learning from (SPY 759.37 to
772.39 over the last five recorded trading days):

- **25 of 38 `TREND_FOLLOW` strategies could not emit a single order in that window**
- **0 of 38 could fire in both directions**
- 26 of the 32 strategies selected in that window produced zero informative decisions

## 2. The defect, stated as one sentence

`StrategyAdmission._reference_price_rejection_reason` already knows that a trend-follow
anchor far from the market makes a strategy permanently degenerate, and refuses it when
`|ref − price| / price > 0.5`. Its docstring says why: *"Price was therefore always far
above the reference, so every one of them was permanently degenerate and could only ever
emit the same action."*

**It fixes one direction of the same failure and leaves the mirror image in place.** A
reference *at* spot puts the trigger band around the present, so the strategy is
permanently `HOLD` - it emits no action at all. That is the same degeneracy, and because
`threshold_pct` is capped at 0.20, such a reference is never more than 20% from spot and so
can never trip the 50% rule.

Net effect: the gate's own quality rule is what teaches the model to anchor on spot, and
then admits 27 strategies that were incapable of trading the moment they were admitted.

## 3. Falsification, run before writing production code

Replaying the **345 real `CURRICULUM_PROPOSED` events** - real model output, not synthetic -
and asking for each whether the band contained that day's SPY price:

```
  27  accepted while the band contained spot     <- the defect, in real recorded data
  16  accepted with the band clear of spot        <- the rule leaves these alone
   0  refused while the band contained spot       <- current rules never catch this
  43  TREND_FOLLOW proposals that reached the gate with a price on hand
```

The 16 left alone include `trend-follow-buy-001`, band `[727.66, 742.36]` against spot
765.49 - a strategy that **did** produce 6 informative decisions. The rule is therefore
precisely targeted: it refuses the set that cannot trade and leaves the set that can.

The 302 proposals not counted are `FIXED_SIZE`, or refused before a spec was ever written.
That is stated rather than hidden: the measurement covers 43 of 345 proposals, which is
every proposal for which the question is well posed.

## 4. The minimal change

**Complete the existing rule. Do not add a subsystem.**

One condition inside `_reference_price_rejection_reason`: refuse when the observed price
lies inside the candidate's own trigger band. The function already fetches the price,
already reads `reference_price`, already knows the failure it exists to prevent, and
already returns a reason string in the house style. No new module, no new service, no new
state, no new configuration variable.

```
if low <= price <= high:
    return (f"trigger band [{low:.2f}, {high:.2f}] already contains the real "
            f"{symbol} price {price}; a reference at spot can only ever HOLD. "
            f"Anchor it outside the current price so the strategy can express a view.")
```

Nothing is deleted. The 50% rule stays: it catches the far-from-market anchor, this
catches the at-market anchor, and the two are complementary.

### What this deliberately does not do

- **No question queue, no experiment registry, no new agent, no orchestration.** Those are
  L3/L4 and the objective forbids reaching past a level that is not yet stable.
- **No purge of the 25 dormant strategies already admitted.** That is a separate decision
  with a different risk profile, recorded as the next need rather than smuggled in here.
- **No change to the curriculum prompt.** If the prompt tells the model to anchor on spot,
  the gate and the prompt now disagree - but the refusal reason names the exact correction,
  and changing the prompt is a separate experiment, not part of completing this rule.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| A strategy whose band contains spot cannot trade | A journal cycle where such a strategy emitted a BUY or SELL |
| The rule refuses only the untradeable | A refused strategy that would have traded - the replay found 0 |
| The library stops filling with unevaluable entries | Dormant-entry count keeps rising after the change |

The first two are checkable now against the existing journal. The third needs the change
to be live.

## 6. When to delete this

If, after this change, the curriculum stops proposing `TREND_FOLLOW` entirely and the
library stops growing without any loss of capability, then the rule is doing the job the
curriculum prompt should have been doing, and the prompt becomes redundant work worth
removing. If instead the loop starts refusing everything and no new capability enters, the
rule is too strict and `threshold_pct` handling is the thing to revisit.

## 7. Verification

- Unit test pinning: a band containing spot is refused; a band clear of spot is admitted;
  the far-from-market case still hits the 50% rule; a candidate with no price on hand is
  still skipped rather than guessed; the band edge counts as inside; the rule follows the
  price rather than purging once.
- Replay of the 345 recorded proposals as the acceptance measurement, not a unit test.
- `make verify` - the whole gate, not just the new class.

The replay cannot become a permanent check class: it reads `runtime/`, which is gitignored
and absent on a fresh clone. The unit tests are the permanent guard; the replay is the
one-time measurement that says the rule was worth adding.

## 8. Result, measured against production code

`StrategyAdmission` itself was driven over the recorded proposals - not a
reimplementation of the rule - using the observed SPY close for each proposal's day as
the price:

```
346 CURRICULUM_PROPOSED events on record

  43  TREND_FOLLOW judged against a real price
  43  accepted back then
  27  refused now by the new band rule
   9  refused now by the pre-existing far-from-market rule (unchanged behaviour)
   7  still admitted - the ones that can trade
   6  no SPY price recorded for that day
 297  not a TREND_FOLLOW, or refused before a spec was ever written
```

So: of 43 proposals the gate can judge, it would now refuse 36, and the 27 the new rule
catches are exactly the set that was admitted unable to trade. The 7 that survive are the
resting triggers, including the shape of `trend-follow-buy-001`, which produced 6
informative decisions.

`make verify`: 49 classes, 0 failed, 1282 test executions across 61 files, 827 tests
collected. The daemon was restarted onto the new code (fingerprint `bf406103943411c0`,
matching the worktree).

## 9. What this change did not do, and what is next

**Not done, deliberately:** the 25 dormant strategies already on disk are untouched. This
change stops the population growing; it does not shrink it. They still hold library slots
and probation turns.

**The next need, stated so it can be judged rather than assumed:** those 25 entries cost
the evidence budget. `min_probation_cycles` is 3 and the screen gate needs 10 informative
decisions, so a newly admitted strategy is judged on a tenth of the evidence required to
clear it and is then retired for `probation produced no exploration evidence` - 36 times on
this journal. Either the probation budget has to cover the screen gate, or admission has
to stop outrunning the observation rate. Which one is right is a separate measurement, and
the measurement is: how many selected cycles does a candidate actually need to reach 10
informative decisions, given the per-strategy informative rate observed in the recent
window. That number is not yet known, and guessing it would be building on nothing.
