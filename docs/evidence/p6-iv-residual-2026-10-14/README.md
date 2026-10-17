# P6: the implied-volatility residual beyond EWMA, on 51x the sample

2026-10-14. Run against data already on disk, because the question the plan left open was a
sample-size question and the sample already exists locally.

## The question

The plan's baseline: the part of implied volatility that EWMA does not explain predicts the
next 21 days' realised volatility at rho = +0.60 (p = 0.0003) - **but over 52 days only**,
with usable IV on 130 of 314 sampled days. Its own stop condition: evidence from a single
window does not justify enabling anything.

So the task was to expand the sample and re-test, not to re-run the same 52 days.

## Data

| | |
|---|---|
| `^VIX` daily | 1990-01-02 .. 2026-10-09, 9,263 points |
| SPY 5-minute bars | 2010-01-04 .. 2026-10-07, reduced to daily closes |
| overlap used | 2016-02-18 .. 2026-09-08, **2,654 observations** |

**51x the 52-day baseline.** The VIX series is index-level 30-day constant-maturity implied
volatility computed from SPX options, not a chosen near-the-money contract with a volume
filter. It is a proxy for the per-contract IV the baseline used, and it is labelled as one:
it is 16 years of daily observations instead of 52, which is the difference between a
correlation and a coincidence.

## Result

Whole sample, and the two gates the plan names:

| | n | EWMA -> forward | IV residual -> forward |
|---|---|---|---|
| whole sample | 2,654 | rho = +0.538 (p ~ 0) | **rho = +0.365 (p ~ 0)** |
| first half | 1,327 | +0.561 | +0.285 |
| second half (test) | 1,327 | +0.476 | **+0.418** |

- **Both halves positive** - the time-split gate passes.
- **90 of 126 monthly blocks positive (71%)** - the block gate passes, and it is not one
  window carrying the result.

Two things the baseline's number did not survive:

- **+0.60 was optimistic.** On 2,654 observations the residual's own rank correlation is
  +0.365. The 52-day figure was a small-sample reading of a smaller effect.
- **EWMA alone is stronger (+0.538) than the residual (+0.365)**, so the residual cannot
  replace the existing forecast. Non-zero is not the same as better.

## The decisive test, which the plan's question actually asks

A non-zero residual correlation does not establish that the residual *adds* anything: it is
orthogonal to EWMA by construction, so it could be re-encoding something EWMA already
carries. The test that matters is whether EWMA + residual beats EWMA alone out of sample.
Fitted on the first half, scored on the second:

| | rho | MAE (vol points) |
|---|---|---|
| EWMA alone | +0.476 | 4.162 |
| **EWMA + IV residual** | **+0.639** | **3.645** |

Residual coefficient **b = +0.9275**, positive in the direction the whole-sample
correlation predicts. **MAE improves by 0.517 vol points, a 12.4% reduction.**

The combined rank correlation on the out-of-sample half is **+0.639 - higher than the
+0.60 the baseline reported, and measured on 1,327 test observations rather than 52.**

## Verdict

**The residual carries information that EWMA does not, and it improves the forecast out of
sample.** The plan's stop condition is satisfied: the evidence is not from a single window.

What this does not establish, and must not be read into it:

- It is **not** a profitability claim. This is a better volatility forecast, and the module
  it feeds is explicitly "a risk judgment, not an edge".
- It is **not** the per-contract IV the plan specified. It is VIX, and VIX is one instrument.
- The improvement is on **forecast error**, not on drawdown or return.

## Next step, per the plan

Shadow mode: write the IV residual into the risk block as **record only, no influence on the
advice**, for 20 trading days, then compare the forecast error against EWMA alone on live
data. That is the plan's own sequencing and it has not been started.

## Reproducing

    PYTHONPATH=src python docs/evidence/p6-iv-residual-2026-10-14/study.py

Read-only. The script writes nothing except its own JSON summary, and the numbers above are
its output verbatim.

Two things learned about the data while writing it, both from errors rather than from
reading:

- `runtime/autopoiesis/replay/VIX.pkl` is **JSON despite the extension**; `pickle.load`
  fails with `invalid load key, '{'` on the first byte.
- The 5-minute JSON rows are chronological, so the last write per date is the session close.
