# G4 v3: core + satellites, registered before any v3 run (2026-11-07).
# Replaces the v2 design flaws recorded in docs/evidence/g4-v2-results-2026-11-07.md:
# 0.5% commission, a drift-keeping "shuffled" control used as the timing control,
# one symbol, a fixed rule standing in for a search, accumulating 1-share sizing, min_effect 0.
hypothesis: on a SPY core sleeve plus six ETF satellites, neither a fixed trend rule (B) nor a walk-forward-searched rule (C) adds return over the core alone (A) beyond 1%/yr of capital, and neither beats buy-and-hold at its own average exposure or random timing at the same exposure
family: portfolio-abc-v3
ledger_ref: none
instruments: SPY(core), QQQ, IWM, TLT, GLD, XLF, XLE (satellites)
interval: 1Day
feed: iex (prices not adjusted for dividends; applies to arms and benchmarks alike)
window_start_utc: 2020-07-27T00:00:00Z
window_end_utc: 2026-11-07T00:00:00Z
capital: sleeve pool 20000 (SPY, 10% target vol, band 10%); 5000 per satellite; 50000 total
books: one sub-book per symbol through the production Guardian and next-open replay executor; curves summed; account limits (gross <= 90000, cash >= 0, daily loss <= 500) checked on the sum and reported
sizing: satellites are long/flat; long = floor(0.98 * min(pool, sub-book equity) / price) shares; rebalance only when |target - held| >= max(1, 10% of target); identical for every arm and control
arm_A: core sleeve only; satellites hold cash
arm_B: A + per satellite: long when close > SMA20 * 1.01, flat when close < SMA20 * 0.99, else unchanged
arm_C: A + per satellite walk-forward selection over 17 configs {SMA band fast 10/20/50/100/200 x band 0/1%; SMA cross 10/50, 20/100, 50/200; momentum 20/60/126/252 > 0}; train 504 bars, refit every 126, embargo 5 bars (labels end 5 bars before the refit), score = in-sample Sharpe net of 3 bps per switch; flat before the first fit
arm_D: NOT_RUN (account holder's decision 2026-11-07)
costs: commission 0, slippage 0.0003; stress slippage 0.0006
primary_metric: daily portfolio return on 50000 of capital
primary_family: {B-A, C-B}, paired block bootstrap (block 5, 2000 resamples), Holm
min_effect: 1.0% per year of capital (mean daily difference >= 0.01/252)
verdict: PROVEN for a comparison only if Holm p < 0.05 and mean difference >= min_effect; otherwise NOT_PROVEN
benchmarks: BENCH_POOL (every symbol bought and held at its full pool); BENCH_EXPOSURE_<arm> (every symbol bought and held at pool x the arm's average deployed fraction on it)
controls: N1 random timing at the arm's own per-symbol long fraction and mean holding length (10 seeds, the primary timing control); N2 B's signal reversed; N3 B's rule on a shuffled close history (a drift control, not a timing control)
diagnostics: DSR for C with n_trials 17; PBO (CSCV, 10 splits) over the 17 configs; seed sweep; 2x-cost stress; byte-identical rerun
notes: historical development evidence, not unseen data (2020-2026 has been looked at); the only unseen test is forward data
amendment_2026-11-07: sizing changed from floor(0.98 * pool / price) to floor(0.98 * min(pool, sub-book equity) / price) before the registered run. A smoke run of the uncommitted runner showed the pool-based rule refusing 890 of XLE's buys for insufficient buying power after the first loss (2 fills in six years), so it measured the refusal, not the rule. That smoke run's statistics were seen (B-A and C-B not significant; B and C behind their exposure benchmarks); a second smoke after the fix was checked only for refusals, fills and exposure. Everything else in this spec is unchanged.
