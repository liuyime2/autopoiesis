# Pre-registered four-arm experiment (G4). Written before any run.
hypothesis: LLM self-evolution adds net value beyond fixed rules and non-LLM search
family: four-arm-abcd
ledger_ref: none
instrument: SPY
interval: 1Day
window_start_utc: 2020-07-27T00:00:00Z
window_end_utc: 2026-11-05T00:00:00Z
rule: four_arm_abcd_v1
primary_metric: daily_net_return_pct
expected_trades: 100
candidate_budget: 4
evaluator: paired_block_bootstrap_v1
n_folds: 1
min_train_bars: 0
commission_pct: 0.005
slippage_pct: 0.001
arms: A,B,C,D
stopping: NOT_PROVEN after two rounds
notes: D-arm LLM runs weekly proposals only (compute budget)