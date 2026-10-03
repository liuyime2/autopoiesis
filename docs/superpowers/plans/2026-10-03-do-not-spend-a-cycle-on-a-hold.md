# Do not spend a probation cycle on a strategy that would only HOLD

Date: 2026-10-03
Status: **DONE - implemented, measured, gated**
Baseline commit for rollback: `3f62f11`
Scope: `src/min_agent/strategy_engine.py`, `tests/min_agent/test_strategy_engine.py`,
plus the figures three documents quote.

This is the `next need` recorded by
[`2026-10-03-journal-on-change-not-on-cadence.md`](2026-10-03-journal-on-change-not-on-cadence.md),
deferred twice with the trade-off unmeasured. It is now measured, and it reversed the
answer.

---

## 0. Why this was deferred, and what measuring it did

The standing question was the probation budget. Measured, not assumed:

> A `FIXED_SIZE` needs 11-13 selected cycles to produce the 10 informative decisions the
> promotion gate requires. `min_probation_cycles = 3`. Raising it looked like the fix.

Replaying the real selector over the real recorded cycles, with the observed
informative-decision rate per strategy:

```
 budget  probation%  other%  reached gate  informative from probation
      3         12%     88%             3                        62.8
      5         20%     80%             3                        62.0
      8         32%     68%             3                        62.9
     11         44%     56%             3                        40.6
     13         52%     48%             2                        35.8
     16         64%     36%             1                        29.2
     20         80%     20%             1                        24.1
```

**The hypothesis is false.** Raising the budget to 13 does not let more candidates be
judged - it lets *fewer*: candidates reaching the gate fall 3 -> 2, the informative
decisions produced fall 62.8 -> 35.8, and the strategies that currently trade lose more
than half their cycles. A deeper queue is worse than a shallow one, monotonically after
about 8.

The simulation also produced the clue: the median observed informative rate across
probation candidates is **0.00**. The extra cycles were going to strategies that cannot
use them.

## 1. The actual composition of the queue

At the last observed price (SPY 769.58, 2026-10-02T15:58:54), of 13 probation candidates:

```
8  cannot fire anywhere in the recent price range (759.37 - 772.39)
1  dormant now, reachable if price moves out of its band
4  can trade at this price
```

The 8 are `TREND_FOLLOW` strategies whose trigger band is wider than the market's entire
recent range - the frozen spot anchors admitted before the gate learned to refuse them.
Across the whole library, 25 of 58 are in that state.

The queue is `sorted(probation, key=(created_at, strategy_id))[0]`: strictly oldest-first.
So it is the one place in `select` that is blind to whether a strategy can act on this
snapshot. The argmax over scores is not - a strategy that never traded scores low and
loses. **The queue serves whatever has been waiting longest, including strategies that
would emit HOLD.**

## 2. The change

Filter the probation queue to strategies that can act, and fall through when none can:

```python
actionable = [s for s in probation if self._declared_actions(s, last_price)]
if actionable:
    ...serve oldest-first from actionable...
```

`_declared_actions` is **the existing predicate** - it already takes `last_price` and
returns an empty set when `_produced_action` yields no action, which is the case where the
band contains the price. It is the same predicate `strategy_admission` now uses to refuse
a spot-anchored `TREND_FOLLOW`, so the gate and the selector agree by construction instead
of by two implementations agreeing. No new helper, no second definition.

The fall-through exposed a second half. With the queue skipped, the argmax had no score to
separate candidates yet, `max` returned the first of equals, and the untradeable strategy
was served anyway - skipping would have bought nothing. So the same filter is applied to
the candidate list the fall-through lands on, **with a fallback to the unfiltered list when
nothing can act**, because an empty candidate set would raise rather than trade.

Checked directly: with every strategy dormant, `select` returns a strategy and does not
raise; with no price, with an empty library, and with everything disabled it behaves as
before. Skipping never becomes stopping.

Nothing is retired. The predicate is price-dependent and reversible, which is the same
reason the admission rule is a rule and not a purge: a frozen anchor is dormant, not wrong.
A test asserts `lifecycle` is untouched and that the strategy is served again once price
leaves its band.

## 3. Effect, replayed over the recorded price path

Serving the whole recorded recent price path to the real queue, budget 3, all 13
candidates:

```
                            cycles consumed   could trade   wasted on a HOLD   released
OLD unfiltered oldest-first         39              9              30 (76%)            0
NEW tradeable-first                 15             15               0 (0%)           313
```

30 wasted cycles become 0, and 313 of 325 cycles are released to the strategies that do
trade. "Candidates given 3 cycles" falls from 13/13 to 5/13, and that is the point rather
than a cost: the other 8 cannot trade at any price in the window, so their cycles were the
waste.

## 4. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| A banded-in strategy would HOLD anyway | a journal cycle where one emitted BUY or SELL |
| Skipping does not starve the queue | probation candidates reaching the 10-scored gate getting *worse* after the change |
| The agent does not freeze | `select` returning `None` or raising when nothing can act |
| It is reversible, not a purge | a strategy staying unselectable after price leaves its band |

The first is the same falsification the admission rule carries, and the third is the
dangerous direction, so both are tested directly rather than argued.

## 5. Verification

Four tests in `tests/min_agent/test_strategy_engine.py`:

| Test | Pins |
|---|---|
| `test_probation_queue_skips_a_candidate_that_cannot_trade_this_snapshot` | the defect: an older untradeable candidate is passed over for one that can act |
| `test_an_untradeable_probation_queue_falls_through_instead_of_freezing` | the dangerous direction: returns a strategy, not `None` |
| `test_the_queue_decision_follows_the_price_rather_than_purging` | reversibility, and that `lifecycle` is untouched |
| `test_a_probation_strategy_with_no_price_is_still_served` | a missing price is not treated as untradeable |

The second test caught a real defect during the work: the first implementation filtered the
queue but not the fall-through, and `max` over equal scores returned the untradeable
strategy anyway. It passed the first test and failed this one.

`make verify`: 49 classes, 0 failed, 1305 test executions across 61 files, 840 collected.
`make lint` clean; `make type` clean on 41 files. Daemon restarted onto `db1022cb21174358`.

## 6. Still open

1. **The 25 library-wide dormant strategies.** The queue no longer wastes cycles on the 8
   that sit in probation, and admission refuses new ones, but they still hold library
   slots and are still re-screened. Nothing is lost by leaving them - they cost nothing
   now that nothing selects them - so purging them is not indicated by any measurement.
2. **Whether the released cycles actually produce evidence.** 313 cycles going to
   argmax-selected strategies is only a win if those strategies are the ones worth
   evaluating. That is observable on the next real trading day and is the measurement that
   would close or reopen this.
3. **`STRATEGY_EVALUATION_RECORDED`** ~2MB/day and **`PNL_EVIDENCE_RECORDED`** ~4MB/day,
   both journal weight, both still decomposed-but-unchanged. Now that the counterfactual,
   screen and selector paths are fixed, these are the remaining journal lines and the
   tail read that would unblock the PnL one properly is still unwritten.
