# Give a winning trade a verdict, so the screen can see one

Date: 2026-10-03
Status: **DONE for the missing primitive; the pass condition is the next need**
Baseline commit for rollback: `bda625b`
Scope: `src/min_agent/counterfactual.py`, `src/min_agent/offline_validation.py`,
`tests/min_agent/test_counterfactual.py`, `tests/min_agent/test_offline_validation.py`,
`tests/min_agent/test_replay_determinism.py`, plus the figures three documents quote.

---

## 0. How this was found

Chasing a different hypothesis first. The `evidence_rank` comment in `strategy_engine`
warns that *"cycles in a reflection window are an artefact of which records happen to be
in that window"*, and both `_needs_probation` and `min_active_cycles` count
`result.cycles`, which is exactly such a window count (`reflection_window = 50` cycles,
about three-quarters of a trading day). If it reset, probation credit could never
accumulate.

**That hypothesis is falsified.** The window does not reset for a strategy that keeps being
selected: `trend-follow-sell-002` sits at `cycles: 4` across 97 consecutive snapshots and
reaches 41; `fixed-size-buy-001` reaches 21. Strategies whose count falls are the ones that
stopped being selected, which is correct behaviour.

Reading the code that the question led to found something larger. In
`counterfactual.evaluate`, the branch for a decision that was actually taken was:

```python
signed = net if action == "BUY" else -net
verdict = FALSE_TRADE if signed < -dead_band_pct else NEUTRAL
```

**Two outcomes only: a trade that lost, or NEUTRAL.** A filled order that *won* was
NEUTRAL - the same label the module uses for a move too small to clear the 0.05% cost. And
`NEUTRAL` is excluded from `scored`, which is `INFORMATIVE = {GOOD_HOLD, MISSED_ALPHA,
FALSE_TRADE}`.

So a strategy's `scored` count was composed **only of its mistakes**, and `validate` then
rejected on `if result.false_trades:`. A trading strategy could not pass the screen at any
profit, because passing required zero scored rows that were fills.

## 1. Why this is a defect in the instrument, not a thin sample

It is not that there is too little data. The rule makes the outcome unreachable: no amount
of profit changes it. `validate`'s own pass message says it outright - *"N of M scored
decisions were correct holds and none were losing trades"* - the screen only knows how to
reward holding.

Measured consequences on this journal, before the change:

| | |
|---|---|
| Strategies that have ever passed the screen | 6 - **all of them 0 BUY, 0 SELL** |
| Strategies that have ever traded and passed | **0** |
| Model-authored trades | 34: 26 winners, 8 losers, none profitable |
| The 26 winners | labelled NEUTRAL, excluded from `scored`, invisible |

And the only strategy ever promoted - `tiny-fixed-size-001`, broker-verified +362.66 -
was promoted on **realised PnL**, not on decision quality, because the decision-quality
path is structurally unreachable for a trader.

This is the `in_flight_order` defect one level up. That one charged a strategy for the risk
system working; this one makes the risk system unable to register a strategy acting well.
Both select for inaction.

## 2. The change

**`counterfactual`**: `GOOD_TRADE`, for a filled order that gained net of cost. Cost is
still the bar, so a move inside the dead band stays NEUTRAL, and a losing fill stays
`FALSE_TRADE`.

**`offline_validation`**: `GOOD_TRADE` joins `INFORMATIVE`; `good_trades` is counted and
published; the pass condition reads `correct_outcomes / scored` -
`(good_holds + good_trades) / scored` - instead of `good_holds / scored`.

The threshold is **unchanged** and the denominator is **unchanged**. Nothing is loosened:
`if result.false_trades: REJECT` still fires exactly as before, so one losing fill still
rejects. What changed is only *which outcomes are allowed to count as correct*, because a
filled order is never a hold and so `good_holds` was zero by construction for any trader.

### The replay-determinism gate

Changing the vocabulary made the gate red on 49 rows, correctly: the journal is append-only
and records what the code computed at the time, so those rows cannot be made to agree again
and must not be rewritten.

The carve-out is **derived, not an allowlist**, and it is deliberately narrow:

```python
_RELABELLED = frozenset({("NEUTRAL", "GOOD_TRADE"), ("GOOD_TRADE", "NEUTRAL")})
```

and it applies only when the recomputed net return **matches to the digit**. A genuine
numeric divergence, or any other pair of labels, still fails. So the gate continues to prove
that the arithmetic reproduces, and separately names how many rows were renamed. The count
is printed, not swallowed.

`known_state_findings.json` would have been the wrong home: those entries are for state that
is *missing or unreconstructable*. This state is neither - it is correct under the semantics
in force.

## 3. What it changed on real data

Recomputing the whole live journal under the new rules:

```
before:  GOOD_HOLD 327  MISSED_ALPHA 195  PENDING 63  GAP 50  NEUTRAL 50  FALSE_TRADE 17
after :  GOOD_HOLD 327  MISSED_ALPHA 195  PENDING 63  GAP 50  GOOD_TRADE 49  FALSE_TRADE 9  NEUTRAL 9
```

49 winning fills that were invisible are now scored. Seven strategies now have scored
trades, and **every one of them reaches the ten-decision threshold the promotion gate
requires** - 43, 21, 21, 12, 11, 10, 1. That number was 1 before.

## 4. And what it exposed: the pass condition is still wrong

All seven are `REJECT_POOR_DECISIONS`, because `if result.false_trades` rejects on the
presence of a loss rather than its rate:

```
strategy                     good_trades  false_trades  scored  verdict
fixed-size-buy-001                    16             3      21  REJECT
tiny-fixed-size-001                   15             3      21  REJECT
fixed-size-sell-005                    7             5      12  REJECT
fixed-size-probe-0001                  5             5      11  REJECT
trend-follow-buy-001                   3             3      10  REJECT
trend-follow-sell-002                  1             0      43  REJECT
```

`tiny-fixed-size-001` - the broker-verified champion, +362.66 - has fifteen winning fills
and three losing ones and is rejected by the screen.

So making success visible did not complete the job; it *measured* the second half. The
stated design intent at `offline_validation.py:202` was *"so a strategy cannot buy a good
ratio by trading constantly and luckily"*. That is a real concern, and a rate expresses it
directly - the codebase already uses rates for exactly this judgement in
`StrategyLifecycleManager` (`max_error_rate = 0.25`, `severe_failure_rate = 0.75`).

**Deliberately not done in this change.** Loosening a promotion gate needs its own change,
its own tests, and its own evidence - and the evidence now exists and is written down above.
Bundling a gate relaxation into "add the missing verb" would be the kind of change that is
hard to review and easy to get wrong. At a 0.25 false-trade rate, two of the seven would
pass where none can today; that is a decision to make deliberately, with the champion's
record in view, not as a side effect.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| Winning fills were invisible | a winning fill recorded as anything but NEUTRAL before this change |
| No trader could pass | a trading strategy with a passing verdict on the old code |
| Cost is still the bar | a move inside the dead band scored as a good trade |
| The gate is not weakened | a strategy with a losing fill now passing |
| The carve-out cannot hide a defect | a real numeric divergence passing as a rename |

The fourth is the one that matters most and is tested directly.

## 6. Verification

Tests added or changed:

- `test_counterfactual.py`: a winning BUY and a winning SELL are `GOOD_TRADE`; a losing
  fill is still `FALSE_TRADE`; a fill inside the dead band is still `NEUTRAL`. The first
  caught my own arithmetic error - `net_return_pct` is 9.95, not 10.0, because cost is
  charged on every row.
- `test_offline_validation.py`: `GOOD_TRADE` counts toward `scored` and
  `correct_outcomes` and reaches `PASS_SCREENED`; a strategy that both holds and trades well
  is not penalised for trading; a losing trade is still rejected; NEUTRAL is still excluded.
- `test_replay_determinism.py`: the live-journal replay now passes, with the relabel count
  printed.

Full suite: **850 passed, 0 failed** - the change is backward-compatible everywhere else.

`make verify`: 49 classes, 0 failed, 1321 test executions across 61 files, 850 collected.
`make lint` clean; `make type` clean on 41 files.

## 7. Still open, in order

1. **The pass condition rejects any losing fill**, which rejects the system's own champion.
   Evidence is in section 4; a rate is the codebase's own idiom for this judgement.
2. **Whether the 313 released probation cycles produced evidence** - needs a trading day.
3. **The 34 model-authored trades** are still too few to judge the model, and the sample
   grows at ~7/day.
4. **`STRATEGY_EVALUATION_RECORDED`** ~2MB/day, and the `PNL_EVIDENCE_RECORDED` dedup that
   needs a cheap journal tail read.
