# One bar on correct outcomes: delete the presence test

Date: 2026-10-03
Status: **DONE - implemented, measured on the shipped code, gated**
Baseline commit for rollback: `7449ff3`
Scope: `src/autopoiesis/offline_validation.py`,
`tests/autopoiesis/test_offline_validation.py`, plus the figures three documents quote.

Directly the `next need` recorded by
[`2026-10-03-give-a-winning-trade-a-verdict.md`](2026-10-03-give-a-winning-trade-a-verdict.md).

---

## 0. A correction to last turn's reasoning

Last turn's plan said the presence test "inverts the screen in both directions" and
implied it admitted strategies whose every decision was wrong. **That was wrong, and it came
from a bad model of the code.** I wrote a `passes_presence(res) = res.false_trades == 0`
helper and treated that as the rule. The code is:

```python
if result.false_trades:            # an ADDITIONAL rejection
    return REJECT
if result.correct_outcome_ratio < min:   # always applied
    return REJECT
```

The ratio is *always* applied. `trend-follow-20260611-003`, whose correct-outcome ratio is
0.000, is rejected — by the ratio, not waved through. The measured consequence in that plan
("the current rule passes 8 of 13, including a 0.000-ratio strategy") was an artefact of the
model. The real current rule passes **5 of 13**.

The finding survives, smaller and accurate. See section 1.

## 1. What the presence test actually did

It only ever removed strategies that had **already cleared the ratio bar**. Over the 13
strategies on this journal with ≥10 scored decisions, it rejected exactly three, and every
one was a trader with a good ratio:

```
strategy                    scored  correct-ratio  losing fills
tiny-fixed-size-001             21           0.857        3 of 21
fixed-size-buy-001              21           0.762        3 of 21
fixed-size-sell-005             12           0.583        5 of 12
```

`tiny-fixed-size-001` is the champion of the entire system — broker-verified realised PnL
**+362.66** — and it was refused by the evidence gate for having lost three times out of
twenty-one. That is what a 0.857 correct-outcome ratio *means*.

The comment immediately above the ratio already stated the intent: *"so a strategy cannot
buy a good ratio by trading constantly and luckily."* The ratio expresses that. The presence
test was a second, stricter rule that the sentence never claimed.

## 2. The change

**Delete the presence test.** The rejection reason keeps naming the losing fills, because
"all ten of your trades lost" is what a reader needs and the ratio alone does not say it:

```
only 0 of 10 scored decisions went the right way (0 correct hold(s), 0 profitable
fill(s), 10 of them filled trades that lost money; ratio 0.000 < 0.50)
```

Nothing is admitted that the ratio would not admit. The passing set becomes exactly
"correct-outcome ratio ≥ 0.5", over the same denominator, at the same threshold.

## 3. Measured on the shipped code, live journal

```
strategy                       scored   ratio  false  verdict
fixed-size-buy-001                 21   0.762      3  PASS_SCREENED   (trader)
fixed-size-probe-0001              11   0.455      5  REJECT          (trader)
fixed-size-sell-005                12   0.583      5  PASS_SCREENED   (trader)
tiny-fixed-size-001                21   0.857      3  PASS_SCREENED   (trader)
trend-follow-20260611-001          44   0.795      0  PASS_SCREENED   (hold-only)
trend-follow-20260611-002          12   0.667      0  PASS_SCREENED   (hold-only)
trend-follow-20260611-003          10   0.000      0  REJECT          (hold-only)
trend-follow-20260611-004          11   0.545      0  PASS_SCREENED   (hold-only)
trend-follow-20260611-005          11   0.545      0  PASS_SCREENED   (hold-only)
trend-follow-20260611-006          10   0.400      0  REJECT          (hold-only)
trend-follow-buy-001               10   0.400      3  REJECT          (trader)
trend-follow-buy-010               10   0.600      0  PASS_SCREENED   (hold-only)
trend-follow-sell-002              43   0.093      0  REJECT          (trader)

PASS_SCREENED: 8   (was 5, and 0 of the 5 traded)
```

Three traders now clear the evidence gate where none could. Every rejection has a ratio
below 0.5, so nothing is let through on a technicality.

This is the first time a trading strategy has been able to pass the screen on decision
quality. Combined with last turn's `GOOD_TRADE`, the decision-quality promotion path is now
reachable - it has never fired in the project's history.

## 4. A test that had to be corrected rather than deleted

`test_a_losing_trade_is_still_rejected`, written yesterday, asserted *"One losing fill
rejects, as before"* and was cited as evidence the gate was not weakened. That assertion was
true when written and is the reason the champion is refused. Deleting it would have hidden
the change; so it was rewritten to the property that actually matters:

`test_a_losing_trade_drags_the_ratio_rather_than_rejecting_on_presence` - one loss in
thirteen still passes at 12/13; eight losses in twelve is rejected at 4/12, and the reason
names them. "A losing trade must be able to reject a strategy" is the real requirement and
that is where it is now pinned.

The docstring says plainly that it is a correction, so the next reader does not assume the
stricter property was always there.

## 5. The margin worth watching

`fixed-size-sell-005` newly passes at 0.583 with **five losing fills out of twelve**. That
is the weakest strategy admitted by this change, and it is the case that decides whether a
later refinement is warranted.

An alternative was measured and not taken: a second threshold, `false_trade_rate <= 0.25`,
mirroring `StrategyLifecycleManager.max_error_rate`. It passes 7 rather than 8, dropping
`fixed-size-sell-005`.

There is a real asymmetry behind that alternative - a losing *fill* cost money while a wrong
*hold* cost nothing, so identical ratios are not identical outcomes. It was not adopted
because it adds a second threshold with no stated basis, and the objective's order is delete
before add. It is recorded here as the measured alternative, with the one strategy that
distinguishes them, so the decision can be made on evidence rather than re-derived.

## 6. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| The presence test only removed strategies that cleared the ratio | a strategy rejected with ratio ≥ 0.5 *and* zero losing fills |
| One bar, same denominator and threshold | the passing set differing from "ratio ≥ 0.5" |
| Losses still reject | a strategy with a majority of losing fills passing |
| Hold-only and trading strategies face the same bar | different verdicts at equal ratio |

The first is the claim the whole change rests on, and it is checkable directly from the
table: the only strategies whose verdict changed are the three with ratio ≥ 0.5 and a
non-zero false-trade count.

## 7. Verification

Tests in `tests/autopoiesis/test_offline_validation.py`:

- `test_a_strategy_whose_every_scored_decision_was_wrong_is_rejected` - 10 missed rallies,
  ratio 0.000, rejected. Guards the ratio from ever becoming optional.
- `test_the_champion_with_three_losing_fills_is_not_rejected_for_them` - 15 profitable fills,
  3 good holds, 3 losing, 18/21 = 0.857, passes.
- `test_trading_luckily_cannot_buy_a_pass` - 6/4 at 0.6 passes, 4/6 at 0.4 rejected. This
  is the intent the presence test was reaching for.
- `test_a_hold_only_strategy_faces_the_same_bar_as_one_that_trades` - equal ratios, equal
  verdicts, different actions.
- `test_a_losing_trade_drags_the_ratio_rather_than_rejecting_on_presence` - the corrected
  safety property.

Full suite: **854 passed, 0 failed**.

`make verify`: 49 classes, 0 failed, 1329 test executions across 61 files, 854 collected.
`make lint` clean; `make type` clean on 41 files. Daemon on `f6ff5a020a507ad2`.

## 8. Still open, in order

1. **Whether the newly-passing strategies actually get promoted.** The lifecycle's other
   promotion route requires broker-verified realised PnL; the decision-quality route
   additionally requires `submitted_orders > 0`, which all three have. That a promotion
   actually fires is observable on the next real trading day and is the thing that confirms
   this whole line of work.
2. **The asymmetry in section 5** - a losing fill is not the same as a wrong hold - with
   `fixed-size-sell-005` as the deciding case.
3. **`STRATEGY_EVALUATION_RECORDED`** ~2MB/day, and the `PNL_EVIDENCE_RECORDED` dedup that
   needs a cheap journal tail read.
