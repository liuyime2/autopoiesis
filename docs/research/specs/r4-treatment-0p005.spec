# R4 treatment hypothesis. Frozen before the run.
# Control: R3 round 1 (0.0005 threshold, OVERFIT). Treatment: 0.005 threshold.
# Economic prior: round 1's 0.0005 threshold overfit because selection on 144 trades inflated in-sample; 0.005 has 41 trades, enough to judge and less prone to selection bias. If 0.005 also overfits, the problem is the method, not the threshold.
hypothesis: a 0.005 close-to-close threshold on SPY has positive out-of-sample return after cost, with enough trades to judge, and less overfit than the 0.0005 threshold
family: spy-price-threshold
instrument: SPY
interval: 5Min
window_start_utc: 2026-04-10T13:30:00Z
window_end_utc: 2026-10-09T19:40:00Z
rule: threshold_grid_v1
primary_metric: oos_return_pct_after_cost_v1
expected_trades: 40
thresholds: 0.005
candidate_budget: 2
evaluator: walk_forward_v1
n_folds: 60
min_train_bars: 20
commission_pct: 0.005
slippage_pct: 0.005