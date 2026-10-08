# Why no brain showed judgment, and a judgment method that can evolve

Date: 2026-10-08
Status: **DONE - results in `docs/evidence/signal-research-2026-10-08/README.md`.** The summary is at the end.

## The problem

The model-zoo comparison (`docs/evidence/fast-brain-2026-10-08/`) found that no local LLM, of any
size or family, and no combination of them, predicted SPY's next hour. The question asked: is that
the system's setup, the context, or something else - and what method of judgment, one that can
improve itself, would work?

## Baseline: what is wrong, measured before anything changes

| | Finding | Number |
|---|---|---|
| D1 strategy space | The research search tries only `FIXED_SIZE` (always buy) and `TREND_FOLLOW` (crossing a fixed absolute price). Neither conditions on the market's state. The RULE DSL can, and is never searched. | 70 trials; 0-4 out-of-sample trades each against 34-84 required |
| D1b the paired rule | The rule the model is compared with is BUY in 268 of 289 benchmark cycles. | |
| D2 context | The market block is computed from the agent's own sparse journal (17 non-contiguous days over four months). | regime/trend/volatility missing on 23% of prompts, 1-day return on 17% |
| D3 power | 289 overlapping 1h labels are ~72 independent outcomes: only correlations above ~0.24 are detectable. Realistic intraday ICs are 0.02-0.05 and need 1,600-10,000 independent outcomes. | SE(rho) ~ 0.12 |
| D4 the brains | Every LLM's signal is past-hour momentum restated (0.75-0.92 correlation with it). | |

So the comparison could not have found a realistic edge even if one existed (D3), the models were
given a thin, gappy picture (D2) and asked to out-guess a coin on a horizon where any edge is
small, and the self-evolution loop searched a space with no market-conditioned rule in it (D1).

## Success criterion

A judgment method is accepted only if, on real bars it was **not selected on**:

1. its signal's correlation with the forward return has a day-block bootstrap interval that
   excludes zero, in the held-out period;
2. it replicates in the same direction on other liquid ETFs it was not selected on;
3. a cost-aware backtest of the rule (0.05% round trip) is positive in the held-out period;
4. the selection counted every variant tried (multiple-testing), as the research ledger does.

If nothing passes, that is the result, stated as such, with the power the test had.

## Phases

1. **Data.** Real 5-minute bars from Alpaca for SPY, QQQ, IWM, DIA, TLT, XLF, XLE, XLB, as far
   back as the feed gives. Point-in-time features only.
2. **Signal research with power.** A small, fixed family of hypotheses stated before looking:
   momentum and reversal over 1-78 bars, distance from the moving average, the opening
   half-hour predicting the last half-hour (documented for SPY), the overnight gap, time of day,
   and the 4-hour reversal seen in the model-zoo data. Selected on the first 60% of days, tested
   once on the last 40%, replicated across symbols.
3. **Rule and backtest.** A survivor is written as a RULE strategy (the DSL already expresses
   return-over-N and distance-from-SMA) and backtested with costs and walk-forward folds.
4. **Self-evolution.** The research driver searches RULE parameters, counting every trial,
   so the loop that proposes, tests, promotes and retires has a market-conditioned space to
   search. Promotion to the live library stays behind the existing lifecycle and Guardian.
5. **The model's role, measured again.** Given the surviving signal as context, does the model's
   override improve on the rule, on the powered benchmark?


## Result

- **D1-D4 confirmed, and then made irrelevant by the answer.** The benchmark was under-powered, the
  context had gaps, the search space had no market-conditioned rule - and fixing all three left the
  answer unchanged. 47 price hypotheses on 2.75-10 years of real bars (SPY intraday, eight ETFs,
  120 stocks), none carried a direction; the two that cleared the bar were volatility persisting
  (real) and high-volatility stocks earning more (beta, caught by the confound control).
- **Success criterion, point by point.** (1) interval excludes zero in the held-out period: only
  volatility persistence; (2) replicates on instruments not selected on: yes, +0.10 to +0.60 on
  all eight; (3) a cost-aware rule is positive in the held-out period: a volatility target lowers
  drawdown (SPY -18.8% to -11.1%) and gives up return - risk management, not alpha; (4) counted
  every variant tried: the ledger bar is 0.05 / 61.
- **The model's role, measured again (phase 5).** Given text, a language model reads the direction
  of the market's reaction (rho +0.11 to +0.15) but the reaction is over by the open (tradable
  next-day rho within 0.03 of zero). Given prices, it has nothing to read.
- **What was built.** `research/signal_test` (the gate, the ledger, the confound control);
  `risk_judgment` (a volatility forecast with its own evidence-earned status) in the decision
  context and the doctor. Nothing was added to the strategy library: no directional rule earned
  a place, and the library's rule is that none gets one for not having been disproved.
