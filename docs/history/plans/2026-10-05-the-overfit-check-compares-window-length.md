# The overfit check compares a 51x-longer window against a short one

Date: 2026-10-05
Status: **DONE - degraded folds 46/52 -> 15/52; the OVERFIT verdict is now a real signal**
Baseline commit for rollback: `35acc7c`
Scope: `src/min_agent/research/walk_forward.py` and its tests. No change to
`required_trades`, `BASE_REQUIRED_TRADES`, `DEGRADATION_LIMIT`, the search space, the driver, the
daemon, or any risk limit.

---

## 0. The question

Every candidate that clears the evidence bar returns `OVERFIT`, on 24,270 real bars, and the
verdict text is `"the out-of-sample leg is far worse than the in-sample leg"`. Is that a real
measurement of a rule that does not generalise, or is the comparison itself broken?

It is the comparison. `run_walk_forward` builds each fold like this:

```python
train = list(bars[:start])          # grows every fold: 20 -> 23,786 bars
test  = list(bars[start:end])       # fixed 466 bars
```

and `degraded` is:

```python
return self.out_of_sample.return_pct < self.in_sample.return_pct * DEGRADATION_LIMIT
```

So a **fixed 466-bar** out-of-sample leg is compared against an in-sample leg that reaches
**23,786 bars** — a ratio of 51:1 by the last fold, measured across the whole run:

```
train/test length ratio: min=0.043 max=50.043
  fold 0:  train=20     test=466
  fold 51: train=23786  test=484
```

## 1. Why a longer window always looks "better in-sample"

`BacktestResult.return_pct` is `realized / spent` (`backtest.py:325`) — it **accumulates** across
round trips rather than annualising. The same rule over longer windows:

```
n=  100  trades=3  return_pct=  -0.207%   price move -0.01%
n= 1000  trades=7  return_pct=  +0.209%   price move +2.80%
n=12000  trades=7  return_pct=  +1.833%   price move +14.18%
n=23800  trades=7  return_pct=  +2.134%   price move +16.28%
```

Seven trades either way, ten times the return, because the extra bars were mostly still-held drift
that the force-close at the window's last bar books as realized. On a rising series that is
guaranteed: **more bars means a bigger number, so `oos < is * 0.5` trips on length alone.**

And it is not a marginal effect. 46 of 52 folds trip it.

## 2. The measurement that isolates it

Replace the growing in-sample with a **length-matched** one — `bars[start-test_size:start]`,
exactly as many bars as the out-of-sample leg — and change nothing else:

```
degraded with CURRENT (growing IS): 46/52
degraded with MATCHED-IS          : 15/52
```

```
fold  ISlen    IScur  ISmatch      OOS  cur deg match deg
   4   1884   +0.952   +1.945   -0.350    True      True
   5   2350   +0.908   -0.350   -0.040    True     False
   6   2816   +1.021   -0.040   -0.149    True     False
  51  23786   +2.087   -0.222   +0.474    True     False
```

Folds 5, 6, 7 and 51 are flagged only by the length mismatch: matched, the in-sample return is
*negative* (`IS <= 0`), the `degraded` guard returns `False`, and the fold is fine.

**31 of 52 folds were failing a test they did not fail.** The remaining 15 are the real signal —
folds where the rule genuinely did much worse out-of-sample than in.

## 3. The fix

Build the in-sample leg to the same length as the out-of-sample leg it is compared against:

```python
train = list(bars[max(0, start - len(test)):start])
```

Three properties worth stating, because two of them are tempting to get wrong:

- **Length-matched, not truncated-at-zero.** Fold 0 has `start = 20 < 466`, so there is not
  enough history. `bars[max(0, ...)]` yields 20 bars and the fold is honestly labelled with a
  short in-sample leg rather than being skipped or silently padded.
- **Contiguous and immediately preceding**, so it is the same regime as the test leg. A
  non-contiguous or strided sample would compare different market conditions, which is the mistake
  this whole plan exists to avoid.
- **`required_trades` is untouched.** It counts independent out-of-sample bets and this changes
  none of them.

The growing train window is not *wrong* for its other purpose — `train_bars` documents how much
history preceded each fold, and the final fold legitimately uses all of it. The defect is using
that window as the denominator of the overfit comparison.

## 4. What this does not do

It does not make anything pass. The matched run still has **15 degraded folds**, and `OVERFIT`
still returns for this candidate. That is the correct answer for a rule whose out-of-sample leg
genuinely collapses on 31 of 52 folds.

It also does not change what `OVERFIT` means. It makes the verdict mean *"worse out-of-sample than
in-sample over comparable windows"* instead of *"the in-sample window was longer"*, which is the
only reading under which the label is honest.

## 5. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The length mismatch causes most OVERFIT folds | the matched run still flagging ~46/52 |
| `return_pct` accumulates with window length | `return_pct` at n=100 equal to `return_pct` at n=23800 |
| The matched fold is the same regime | any discontinuity between the train and test boundaries |
| No risk or evidence rule moved | any diff to `required_trades`, `DEGRADATION_LIMIT`, or the daemon |

## 6. Verification

- A test asserting `train_bars == test_bars` for every fold except fold 0, which is capped by
  available history.
- A test pinning the measured pair: the growing-IS run flags fold 5 and the matched run does not.
- The existing leakage, stress and multiple-testing tests unchanged.
- `make verify` - 52/52 classes, 0 failed.
- A real driver run on the 24,270-bar cache, reporting whatever verdict comes out.

## 7. Explicitly not in scope

- Widening the search space, disabling shadow mode, or anything touching the account.
- Changing `DEGRADATION_LIMIT` or `required_trades`. The bar is not the problem; what is measured
  against it was wrong.
---

## Result

One line in `run_walk_forward`: the in-sample leg is now
`bars[max(0, start - len(test)):start]` instead of `bars[:start]`.

Measured on the same 24,270 real bars, same candidate, nothing else changed:

```
degraded folds: 15/52      (was 46/52)
train/test ratio: min=0.043 max=1.000   (was max=50.043)
folds where train != test: 1  -> [(0, 20, 466)]   fold 0, capped by available history
```

Fold 5 is the illustrative one:

```
fold 5   train=466 test=466  IS=-0.350%  OOS=-0.040%  degraded=False
```

It was `degraded=True` before, purely because its in-sample figure came from 2,350 bars and the
out-of-sample figure from 466.

Gate: **52 classes, 0 failed, 1506 test executions across 64 files**, 944 pytest, ruff and mypy
clean. Four new tests; one existing test needed rewriting rather than deleting.

**The existing test asserted a property the fix removes.** `test_each_fold_is_evaluated_only_on_bars_it_never_trained_on`
asserted `train_bars` strictly increases and that `train_bars + test_bars == len(bars)` — both
true only because the in-sample leg was the growing history. Asserting forward progress on a
length that deliberately stops growing is asserting the bug. It now asserts the property that
actually matters and that I had verified by construction but not written down: each fold's test
leg *is* the next fold's fitted history, contiguous, no gap and no overlap. The `min_train_bars`
warm-up is never tested, by design — it exists to be fitted on, not to be evidence — which is why
no fold needs to partition the series and that old equality was always slightly wrong.

### What the verdict means now

The real run on 24,270 bars, with the corrected comparison:

```
search-trend-follow-0p0005    OVERFIT   oos +0.158% over 170 trades
search-trend-follow-0p0010    OVERFIT   oos +0.135% over 117 trades
search-trend-follow-0p0020    OVERFIT   oos +0.112% over  80 trades
search-fixed-size-buy-hold    OVERFIT   oos +0.202% over  77 trades
```

`OVERFIT` still returns, and it should. Measured against the only benchmark that matters — the
same out-of-sample windows, bought once and held:

```
mean buy-and-hold over the 52 OOS legs: +0.3110%
mean TREND_FOLLOW over the same legs   : +0.1216%
difference                            : -0.1894%
folds where the rule beat holding     : 16/52
```

The rule is worse than doing nothing on two thirds of the folds. **The negative result survived
the correction to the instrument**, which is the only way to know the correction was worth making:
a broken gate that had been repaired would have produced a pass, and a pass here would have been
the alarming outcome, not the reassuring one.

### The structural ceiling this exposes

The search space cannot express an edge, and it is worth naming why rather than leaving it as
"OVERFIT":

- `backtest.py` is **long-only by design** — "A SELL with no position is a no-op, not a short".
- Both rule kinds inherit that, and `TREND_FOLLOW` derives its action from the sign of drift, so
  a SELL can only close a position the agent already holds.
- Therefore no rule in the space can profit from a fall without already holding shares, and the
  ceiling on any of them is buy-and-hold's own return — which on this window is +17.70%.

Every entry-timing variant is competing for a slice of that, and the measured spread between
buying at the window's low (+18.83%) and at its high (-0.66%) is the entire prize. That is a fact
about the rule space, not about the gate, and widening it is new capability rather than a fix.
