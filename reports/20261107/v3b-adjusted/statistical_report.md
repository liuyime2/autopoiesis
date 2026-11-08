# G4 v3 statistical report

Conclusion: **NOT_PROVEN** (D not run).

| row | return % | max DD % | return/DD |
|---|---:|---:|---:|
| A | +22.50 | 5.61 | 4.009 |
| B | +43.64 | 7.75 | 5.631 |
| C | +29.64 | 9.27 | 3.198 |
| BENCH_POOL | +137.03 | 18.75 | 7.307 |
| BENCH_EXPOSURE_A | +40.39 | 8.87 | 4.553 |
| BENCH_EXPOSURE_B | +87.00 | 12.99 | 6.695 |
| BENCH_EXPOSURE_C | +72.00 | 12.05 | 5.975 |
| N1_random_timing_B | +41.47 | 12.56 | 3.301 |
| N1_random_timing_C | +38.76 | 8.85 | 4.379 |
| N2_reversed_B | +46.07 | 12.05 | 3.822 |
| N3_shuffled_B_drift | +58.91 | 10.89 | 5.409 |

## Primary family (Holm)

| pair | mean daily diff | 95% CI | p two-sided | Holm p | >= min effect | verdict |
|---|---:|---|---:|---:|---|---|
| B-A | +0.000106 | [-0.000005, +0.000222] | 0.061 | 0.122 | True | NOT_PROVEN |
| C-B | -0.000066 | [-0.000165, +0.000031] | 0.201 | 0.201 | False | NOT_PROVEN |

## Descriptive (not adjusted)

| pair | mean daily diff | 95% CI | p two-sided |
|---|---:|---|---:|
| B-BENCH_EXPOSURE_B | -0.000180 | [-0.000344, -0.000029] | 0.020 |
| C-BENCH_EXPOSURE_C | -0.000188 | [-0.000324, -0.000059] | 0.006 |
| A-BENCH_EXPOSURE_A | -0.000091 | [-0.000178, -0.000009] | 0.034 |
| B-N1_random_timing_B | +0.000008 | [-0.000101, +0.000105] | 0.895 |
| C-N1_random_timing_C | -0.000044 | [-0.000128, +0.000042] | 0.331 |
| B-N2_reversed_B | -0.000012 | [-0.000166, +0.000133] | 0.910 |
| B-N3_shuffled_B_drift | -0.000066 | [-0.000160, +0.000021] | 0.149 |

Random-timing seeds beaten (of 10): {'B': 2, 'C': 0}
PBO over 17 configs: {'pbo': 0.8928571428571429, 'splits': 252, 'n_strategies': 17}
DSR of C (n_trials 17): {'sharpe': 0.0453072, 'expected_max_sharpe': 0.04666104, 'dsr': 0.47884952, 'n': 1579, 'n_trials': 17}

Invariants: {"A": {"sum_of_peak_gross_exposure": 20364.06, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": 4053.49, "cash_never_negative": true, "worst_day_pnl": -671.68, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "B": {"sum_of_peak_gross_exposure": 52511.97, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -16.28, "cash_never_negative": false, "worst_day_pnl": -1156.01, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "C": {"sum_of_peak_gross_exposure": 51863.23, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -28.76, "cash_never_negative": false, "worst_day_pnl": -1204.05, "daily_loss_limit": 500.0, "worst_day_within_limit": false}}
