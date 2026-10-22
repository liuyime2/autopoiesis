# R3 pre-registration. Frozen before the run; the hash is the evidence.
# The holdout is the latest 40% of trading days and is not read by selection.
hypothesis: a 5-minute close-to-close threshold rule on SPY has positive out-of-sample return after an assumed round-trip cost, over a window whose later 40% was not used to choose the threshold
family: spy-price-threshold
instrument: SPY
interval: 5Min
window_start_utc: 2026-04-10T13:30:00Z
window_end_utc: 2026-10-09T19:40:00Z
rule: threshold_grid_v1
primary_metric: oos_return_pct_after_cost_v1
expected_trades: 100
thresholds: 0.0005,0.001,0.002,0.005,0.0075,0.010,0.015,0.020,0.025,0.030
candidate_budget: 11
evaluator: walk_forward_v1
n_folds: 60
min_train_bars: 20
commission_pct: 0.005
slippage_pct: 0.005