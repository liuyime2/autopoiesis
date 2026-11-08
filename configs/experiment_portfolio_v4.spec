# G4 v4: the production-permitted universe on total-return-adjusted bars, registered before any v4 run (2026-11-07).
# Replaces v3 for two reasons found after v3 was run, both recorded in docs/evidence/g4-v3-results-2026-11-07.md:
#  1. v3's daily bars were RAW: splits are price jumps (XLE and XLB -50.2% on 2025-12-05, NVDA -89.9% 2024-06-10,
#     AAPL -74.1% 2020-08-31). A book holding XLE booked a loss that never happened, so v3's numbers for every row
#     that holds XLE (all arms, benchmarks, controls) are contaminated.
#  2. v3's satellites included TLT, which production forbids trading, and IWM and GLD, which are not on the allowlist.
hypothesis: on a SPY core sleeve plus satellites drawn from the symbols a strategy may actually be admitted on today, neither a fixed trend rule (B) nor a walk-forward-searched rule (C) adds return over the core alone (A) beyond 1%/yr of capital, and neither beats buy-and-hold at its own average exposure or random timing at the same exposure
family: portfolio-abc-v4
ledger_ref: none
instruments: SPY(core), XLB, XLE, XLF, QQQ, NVDA, MSFT, AAPL (satellites) = allowlist - sleeve - hold-only - cash
interval: 1Day
feed: iex
adjustment: all (Alpaca: split and dividend adjusted, i.e. total-return prices; identical for arms, benchmarks and controls; fills happen at adjusted prices)
window_start_utc: 2020-07-27T00:00:00Z
window_end_utc: 2026-11-07T00:00:00Z
capital: sleeve pool 20000 (SPY, 10% target vol, band 10%); 5000 per satellite; 55000 total
books: one sub-book per symbol through the production Guardian and next-open replay executor; curves summed; account limits (gross <= 90000, cash >= 0, daily loss <= 500) checked on the sum and reported. Known limit: sub-books do not share cash.
sizing: satellites are long/flat; long = floor(0.98 * min(pool, sub-book equity) / price) shares; rebalance only when |target - held| >= max(1, 10% of target); identical for every arm and control
arm_A: core sleeve only; satellites hold cash
arm_B: A + per satellite: long when close > SMA20 * 1.01, flat when close < SMA20 * 0.99, else unchanged
arm_C: A + per satellite walk-forward selection over the same 17 configs as v3; train 504 bars, refit every 126, embargo 5 bars, score = in-sample Sharpe net of 3 bps per switch; flat before the first fit
arm_D: NOT_RUN (account holder's decision 2026-11-07)
costs: commission 0, slippage 0.0003; stress slippage 0.0006
primary_metric: daily portfolio return on 55000 of capital
primary_family: {B-A, C-B}, paired block bootstrap (block 5, 2000 resamples), Holm
min_effect: 1.0% per year of capital (mean daily difference >= 0.01/252)
verdict: PROVEN for a comparison only if ALL of: Holm p < 0.05; the mean daily difference >= min_effect; and the lower end of its 95% bootstrap CI >= min_effect. Otherwise NOT_PROVEN. (v3 required only the first two.)
reported_not_decisive: lag-1 autocorrelation of each compared difference series, and the two-sided p at block sizes 5, 10 and 20 (a calibration on synthetic series, docs/evidence/goal-2026-11-07/bootstrap_calibration.json, found the test about twice too liberal when differences are strongly autocorrelated)
benchmarks: BENCH_POOL (every symbol bought and held at its full pool); BENCH_EXPOSURE_<arm> (every symbol bought and held at pool x the arm's average deployed fraction on it)
controls: N1 random timing at the arm's own per-symbol long fraction and mean holding length (10 seeds, the primary timing control); N2 B's signal reversed; N3 B's rule on a shuffled close history (a drift control, not a timing control)
diagnostics: DSR for C with n_trials 17; PBO (CSCV, 10 splits) over the 17 configs; seed sweep; 2x-cost stress; byte-identical rerun
amendment_none: no sizing or rule change relative to v3's amended sizing
notes: historical development evidence, not unseen data (2020-2026 has been looked at); the only unseen test is forward data. The single-stock satellites (NVDA, MSFT, AAPL) have one 2020-2026 path each; the result says nothing about stocks in general.
