# G4 v3 statistical report

Conclusion: **NOT_PROVEN** (D not run).

| row | return % | max DD % | return/DD |
|---|---:|---:|---:|
| A | +20.21 | 5.08 | 3.978 |
| B | +31.08 | 7.73 | 4.018 |
| C | +18.57 | 8.26 | 2.248 |
| BENCH_POOL | +97.73 | 19.67 | 4.968 |
| BENCH_EXPOSURE_A | +34.67 | 9.01 | 3.848 |
| BENCH_EXPOSURE_B | +63.11 | 13.55 | 4.656 |
| BENCH_EXPOSURE_C | +55.48 | 12.77 | 4.344 |
| N1_random_timing_B | +34.76 | 11.21 | 3.1 |
| N1_random_timing_C | +36.05 | 9.12 | 3.952 |
| N2_reversed_B | +42.80 | 12.05 | 3.553 |
| N3_shuffled_B_drift | +45.75 | 9.79 | 4.675 |

## Primary family (Holm)

| pair | mean daily diff | 95% CI | p two-sided | Holm p | >= min effect | verdict |
|---|---:|---|---:|---:|---|---|
| B-A | +0.000061 | [-0.000061, +0.000189] | 0.314 | 0.462 | True | NOT_PROVEN |
| C-B | -0.000065 | [-0.000168, +0.000038] | 0.231 | 0.462 | False | NOT_PROVEN |

## Descriptive (not adjusted)

| pair | mean daily diff | 95% CI | p two-sided |
|---|---:|---|---:|
| B-BENCH_EXPOSURE_B | -0.000151 | [-0.000302, -0.000005] | 0.044 |
| C-BENCH_EXPOSURE_C | -0.000180 | [-0.000308, -0.000053] | 0.006 |
| A-BENCH_EXPOSURE_A | -0.000076 | [-0.000159, +0.000002] | 0.061 |
| B-N1_random_timing_B | -0.000019 | [-0.000129, +0.000077] | 0.707 |
| C-N1_random_timing_C | -0.000087 | [-0.000190, +0.000025] | 0.136 |
| B-N2_reversed_B | -0.000056 | [-0.000210, +0.000097] | 0.521 |
| B-N3_shuffled_B_drift | -0.000069 | [-0.000166, +0.000023] | 0.161 |

Random-timing seeds beaten (of 10): {'B': 0, 'C': 0}
PBO over 17 configs: {'pbo': 0.7420634920634921, 'splits': 252, 'n_strategies': 17}
DSR of C (n_trials 17): {'sharpe': 0.02956152, 'expected_max_sharpe': 0.04663634, 'dsr': 0.25164748, 'n': 1579, 'n_trials': 17}

Invariants: {"A": {"sum_of_peak_gross_exposure": 20898.71, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": 3686.48, "cash_never_negative": true, "worst_day_pnl": -671.68, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "B": {"sum_of_peak_gross_exposure": 53031.29, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": 14.12, "cash_never_negative": true, "worst_day_pnl": -2547.83, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "C": {"sum_of_peak_gross_exposure": 52342.67, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -73.79, "cash_never_negative": false, "worst_day_pnl": -1788.41, "daily_loss_limit": 500.0, "worst_day_within_limit": false}}
