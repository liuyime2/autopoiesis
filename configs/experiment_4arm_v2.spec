# Exploratory v2 diagnostic, registered before the v2 run.
# v1 was invalidated by the leverage defect recorded in
# docs/REPLAY_INTEGRITY_PLAN_2026-11-06.md. This spec names the rules because
# "core sleeve" and "fixed rule" are not executable without them.
hypothesis: under the production Guardian and next-open fills, A/B/C produce positive net returns but do not beat a risk-budgeted SPY buy-and-hold; D's incremental value is untested until a weekly LLM engine exists
family: four-arm-abcd-v2-diagnostic
ledger_ref: none
instrument: SPY
interval: 1Day
window_start_utc: 2020-07-27T00:00:00Z
window_end_utc: 2026-11-05T00:00:00Z
rule: four_arm_abcd_v2
primary_metric: daily_net_return_pct
expected_trades: 100
candidate_budget: 4
evaluator: paired_block_bootstrap_holm_v2
n_folds: 1
min_train_bars: 30
commission_pct: 0.005
slippage_pct: 0.001
arms: A=core_sleeve_vol_target_10pct_pool_20000,B=fixed_sma20_band_1pct,C=sma20_50,D=llm_weekly_not_run
benchmark: buy_and_hold_spy_at_max_position_value
controls: random_seeded,reversed_sma20,shuffled_history_sma20
multiple_testing: holm
stopping: INSUFFICIENT while D is NOT_RUN
notes: exploratory diagnostic, not confirmatory; the only unseen test is forward data after deployment
