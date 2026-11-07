# G4 v3 statistical report

Conclusion: **NOT_PROVEN** (D not run).

| row | return % | max DD % | return/DD |
|---|---:|---:|---:|
| A | +20.06 | 5.09 | 3.945 |
| B | +29.38 | 7.95 | 3.694 |
| C | +20.81 | 7.97 | 2.611 |
| BENCH_POOL | +97.70 | 19.68 | 4.965 |
| BENCH_EXPOSURE_A | +34.66 | 9.01 | 3.847 |
| BENCH_EXPOSURE_B | +63.10 | 13.56 | 4.654 |
| BENCH_EXPOSURE_C | +55.31 | 12.81 | 4.317 |
| N1_random_timing_B | +33.02 | 11.43 | 2.888 |
| N1_random_timing_C | +49.60 | 8.38 | 5.922 |
| N2_reversed_B | +41.15 | 12.25 | 3.36 |
| N3_shuffled_B_drift | +44.26 | 10.14 | 4.366 |

## Primary family (Holm)

| pair | mean daily diff | 95% CI | p two-sided | Holm p | >= min effect | verdict |
|---|---:|---|---:|---:|---|---|
| B-A | +0.000054 | [-0.000069, +0.000182] | 0.371 | 0.742 | True | NOT_PROVEN |
| C-B | -0.000045 | [-0.000150, +0.000056] | 0.415 | 0.742 | False | NOT_PROVEN |

## Descriptive (not adjusted)

| pair | mean daily diff | 95% CI | p two-sided |
|---|---:|---|---:|
| B-BENCH_EXPOSURE_B | -0.000159 | [-0.000310, -0.000013] | 0.034 |
| C-BENCH_EXPOSURE_C | -0.000167 | [-0.000295, -0.000037] | 0.010 |
| A-BENCH_EXPOSURE_A | -0.000077 | [-0.000159, +0.000001] | 0.058 |
| B-N1_random_timing_B | -0.000019 | [-0.000130, +0.000077] | 0.706 |
| C-N1_random_timing_C | -0.000135 | [-0.000230, -0.000033] | 0.009 |
| B-N2_reversed_B | -0.000056 | [-0.000212, +0.000096] | 0.512 |
| B-N3_shuffled_B_drift | -0.000071 | [-0.000169, +0.000022] | 0.152 |

Random-timing seeds beaten (of 10): {'B': 0, 'C': 1}
PBO over 17 configs: {'pbo': 0.6468253968253969, 'splits': 252, 'n_strategies': 17}
DSR of C (n_trials 17): {'sharpe': 0.03324552, 'expected_max_sharpe': 0.04664678, 'dsr': 0.29972302, 'n': 1579, 'n_trials': 17}

Invariants: {"A": {"sum_of_peak_gross_exposure": 20898.71, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": 3672.81, "cash_never_negative": true, "worst_day_pnl": -672.6, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "B": {"sum_of_peak_gross_exposure": 52999.89, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -19.89, "cash_never_negative": false, "worst_day_pnl": -2547.39, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "C": {"sum_of_peak_gross_exposure": 52130.34, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -22.21, "cash_never_negative": false, "worst_day_pnl": -1786.32, "daily_loss_limit": 500.0, "worst_day_within_limit": false}}
