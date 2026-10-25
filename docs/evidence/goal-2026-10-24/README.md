# Volatility-targeted SPY sleeve on 33 years of real daily data (2026-10-24)

`vol_target_eval.py` replays the production EWMA forecast (`autopoiesis.risk_judgment.ewma_series`,
`vol_scaled_fraction`) on SPY adjusted closes 1993-01-29..2026-10-08 (8,481 days, Yahoo daily,
split- and dividend-adjusted). Output: `vol_target_eval.json`. Pre-stated parameters: target 10%,
band 10 points, 5 bp cost per unit traded, weight capped at 1, cash 0% (and 2% as a second column).
No VIX floor (it can only lower exposure). Survivorship does not apply (single index fund).

Reproduce: `PYTHONPATH=src python docs/evidence/goal-2026-10-24/vol_target_eval.py <yahoo_long.pkl> out.json`
(`yahoo_long.pkl` is the daily panel built by `docs/evidence/signal-research-2026-10-08/part2/fetch_*`.)

## Result (target 10%, cash 0%)

| Period | Buy&hold CAGR / Sharpe / MaxDD | Sleeve CAGR / Sharpe / MaxDD |
|---|---|---|
| 1993-2012 | 8.07% / 0.49 / -55.2% | 5.77% / 0.62 / -28.9% |
| 2013-2022 | 12.46% / 0.76 / -33.7% | 7.94% / 0.83 / -14.7% |
| 2023- | 22.21% / 1.42 / -18.8% | 12.58% / 1.23 / -11.3% |
| full | 10.86% / 0.65 / -55.2% | 7.15% / 0.75 / -28.9% |

Reading: it roughly halves the worst drawdown and lifts Sharpe over the long record, at the cost of
return, and it lost on Sharpe in the 2023- bull run. It is a risk product, not alpha: it does not
beat SPY on raw return in any period. Annual turnover about 1.6x of the pool.

# VIX term structure on top of the production volatility forecast (2026-10-25)

`vix_term_structure.py` -> `vix_term_structure.json`. One hypothesis stated before the run: adding
`log(VIX3M/VIX)` to a regression of the next 21 days' realised SPY volatility on the production EWMA
forecast and VIX lowers out-of-sample error. Fit 2006-07-17..2017-12-29 (2,886 days), scored on
2018-01-02..2026-09-09 (2,183 days), read once; month-block bootstrap.

Result: out-of-sample MSE of log-volatility 0.1507 -> 0.1473 (-2.2%), 95% interval for the
improvement [-0.0026, +0.0096], which includes zero. **NO_DETECTABLE_EFFECT.** So the term structure is
not worth a new daily data feed, and the "VIX term structure" item is closed as refuted rather than
open. This is one hypothesis on one instrument; it is not in `signal_trials.jsonl` (that ledger is runtime
state shared with the daemon), and this file is its trace.
