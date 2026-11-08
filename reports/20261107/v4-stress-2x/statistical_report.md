# G4 v3 statistical report

Conclusion: **NOT_PROVEN** (D not run).

| row | return % | max DD % | return/DD |
|---|---:|---:|---:|
| A | +20.31 | 5.15 | 3.943 |
| B | +59.95 | 11.31 | 5.299 |
| C | +47.98 | 10.13 | 4.736 |
| BENCH_POOL | +309.23 | 25.71 | 12.026 |
| BENCH_EXPOSURE_A | +36.71 | 8.16 | 4.501 |
| BENCH_EXPOSURE_B | +184.33 | 20.23 | 9.112 |
| BENCH_EXPOSURE_C | +156.06 | 19.04 | 8.198 |
| N1_random_timing_B | +70.11 | 9.50 | 7.377 |
| N1_random_timing_C | +69.67 | 12.76 | 5.458 |
| N2_reversed_B | +68.06 | 12.97 | 5.246 |
| N3_shuffled_B_drift | +73.07 | 19.39 | 3.768 |

## Primary family (Holm)

| pair | mean daily diff | 95% CI | p two-sided | Holm p | >= min effect | verdict |
|---|---:|---|---:|---:|---|---|
| B-A | +0.000191 | [+0.000017, +0.000373] | 0.033 | 0.066 | True | NOT_PROVEN |
| C-B | -0.000052 | [-0.000196, +0.000098] | 0.502 | 0.502 | False | NOT_PROVEN |

## Descriptive (not adjusted)

| pair | mean daily diff | 95% CI | p two-sided |
|---|---:|---|---:|
| B-BENCH_EXPOSURE_B | -0.000407 | [-0.000716, -0.000099] | 0.016 |
| C-BENCH_EXPOSURE_C | -0.000385 | [-0.000691, -0.000102] | 0.013 |
| A-BENCH_EXPOSURE_A | -0.000085 | [-0.000165, -0.000009] | 0.033 |
| B-N1_random_timing_B | -0.000041 | [-0.000168, +0.000099] | 0.590 |
| C-N1_random_timing_C | -0.000088 | [-0.000247, +0.000079] | 0.310 |
| B-N2_reversed_B | -0.000033 | [-0.000249, +0.000192] | 0.799 |
| B-N3_shuffled_B_drift | -0.000058 | [-0.000194, +0.000092] | 0.424 |

Random-timing seeds beaten (of 10): {'B': 0, 'C': 0}
PBO over 17 configs: {'pbo': 0.6190476190476191, 'splits': 252, 'n_strategies': 17}
DSR of C (n_trials 17): {'sharpe': 0.05646942, 'expected_max_sharpe': 0.0462035, 'dsr': 0.65769584, 'n': 1579, 'n_trials': 17}

Invariants: {"A": {"sum_of_peak_gross_exposure": 20364.06, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": 4039.83, "cash_never_negative": true, "worst_day_pnl": -672.6, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "B": {"sum_of_peak_gross_exposure": 58878.12, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -59.2, "cash_never_negative": false, "worst_day_pnl": -1666.27, "daily_loss_limit": 500.0, "worst_day_within_limit": false}, "C": {"sum_of_peak_gross_exposure": 58615.3, "gross_limit": 90000.0, "gross_within_limit": true, "min_sub_book_cash": -46.77, "cash_never_negative": false, "worst_day_pnl": -1816.64, "daily_loss_limit": 500.0, "worst_day_within_limit": false}}
