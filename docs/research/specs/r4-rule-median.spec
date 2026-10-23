# R4 treatment, rule-based selection. Frozen before the run.
# Control: R3 round 1 (0.0005, in-sample selected, OVERFIT). Treatment: median of the grid (0.00625), chosen by a rule, not by in-sample performance.
# Economic prior: the overfit in round 1 came from selecting the threshold that looked best in-sample. A threshold chosen by a rule that cannot see the data (the median of the candidate grid) removes that selection channel. If it still overfits, selection was not the cause.
hypothesis: a threshold chosen by the median of the candidate grid (0.00625) on SPY has positive out-of-sample return after cost, with less overfit than the in-sample-selected 0.0005 threshold
family: spy-price-threshold
instrument: SPY
interval: 5Min
window_start_utc: 2026-04-10T13:30:00Z
window_end_utc: 2026-10-09T19:40:00Z
rule: threshold_grid_v1
primary_metric: oos_return_pct_after_cost_v1
expected_trades: 20
thresholds: 0.00625
candidate_budget: 2
evaluator: walk_forward_v1
n_folds: 60
min_train_bars: 20
commission_pct: 0.005
slippage_pct: 0.005
