# G4 v3 statistical report

Conclusion: **NOT_PROVEN** (D not run).

| row | return % | max DD % | return/DD |
|---|---:|---:|---:|
| A | +20.45 | 5.14 | 3.977 |
| B | +61.70 | 11.07 | 5.573 |
| C | +49.12 | 10.05 | 4.889 |
| BENCH_POOL | +309.26 | 25.71 | 12.029 |
| BENCH_EXPOSURE_A | +36.72 | 8.16 | 4.502 |
| BENCH_EXPOSURE_B | +184.35 | 20.23 | 9.113 |
| BENCH_EXPOSURE_C | +154.93 | 18.95 | 8.176 |
| N1_random_timing_B | +72.37 | 9.38 | 7.719 |
| N1_random_timing_C | +69.96 | 13.66 | 5.121 |
| N2_reversed_B | +70.29 | 12.76 | 5.509 |
| N3_shuffled_B_drift | +74.56 | 19.16 | 3.891 |

## Primary family (Holm)

| pair | mean daily diff | 95% CI | p two-sided | Holm p | >= min effect | verdict |
|---|---:|---|---:|---:|---|---|
| B-A | +0.000197 | [+0.000025, +0.000377] | 0.027 | 0.054 | True | NOT_PROVEN |
| C-B | -0.000054 | [-0.000196, +0.000095] | 0.475 | 0.475 | False | NOT_PROVEN |

## Descriptive (not adjusted)

| pair | mean daily diff | 95% CI | p two-sided |
|---|---:|---|---:|
| B-BENCH_EXPOSURE_B | -0.000400 | [-0.000711, -0.000093] | 0.018 |
| C-BENCH_EXPOSURE_C | -0.000377 | [-0.000682, -0.000092] | 0.015 |
| A-BENCH_EXPOSURE_A | -0.000084 | [-0.000165, -0.000008] | 0.033 |
| B-N1_random_timing_B | -0.000042 | [-0.000168, +0.000098] | 0.571 |
| C-N1_random_timing_C | -0.000085 | [-0.000243, +0.000088] | 0.315 |
| B-N2_reversed_B | -0.000035 | [-0.000250, +0.000193] | 0.782 |
| B-N3_shuffled_B_drift | -0.000057 | [-0.000190, +0.000093] | 0.429 |

Random-timing seeds beaten (of 10): {'B': 0, 'C': 0}
PBO over 17 configs: {'pbo': 0.5277777777777778, 'splits': 252, 'n_strategies': 17}
DSR of C (n_trials 17): {'sharpe': 0.05788815, 'expected_max_sharpe': 0.04618293, 'dsr': 0.67843848, 'n': 1579, 'n_trials': 17}

Invariants: {"A": {"sum_of_peak_gross_exposure": 20364.06, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": 4053.49, "cash_never_negative": true, "worst_day_pnl": -671.68, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "B": {"sum_of_peak_gross_exposure": 58878.12, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -52.11, "cash_never_negative": false, "worst_day_pnl": -1666.09, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "C": {"sum_of_peak_gross_exposure": 58616.54, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -43.93, "cash_never_negative": false, "worst_day_pnl": -1822.24, "daily_loss_limit": 500.0, "worst_day_within_limit": false}}
