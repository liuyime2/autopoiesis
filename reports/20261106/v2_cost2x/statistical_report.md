# Statistical report

Spec SHA-256: `8d024b263d47be41b9e3e0d297fbffc1a473580511f5d7ba161f77cede61275f`
Data SHA-256: `e93e077b701f1be48c19a659c53fa64f1ef9544a454c3de075e64bfd14b32d13`
Code commit: `b3ed494be12ac9f98de84fd9539e4846d8d7efbd`
Bars: 1579 SPY 1Day 2020-07-27..2026-11-05
Costs: commission 1.0000%, slippage 0.2000%
Minimum effect for PROVEN: 0.00000000 per bar

## Arms and benchmarks

| arm | role | final equity | return | max drawdown | return/DD | fills | min cash | max gross |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | arm | 107196.91 | 7.20% | 2.75% | 2.62 | 95 | 83153.44 | 20898.71 |
| B | arm | 108279.97 | 8.28% | 6.03% | 1.37 | 691 | 75313.27 | 28249.29 |
| C | arm | 110717.76 | 10.72% | 5.34% | 2.01 | 404 | 76094.06 | 29006.08 |
| BENCH_20K | benchmark | 124826.34 | 24.83% | 6.77% | 3.67 | 1 | 80087.11 | 47525.10 |
| BENCH_25K | benchmark | 131338.17 | 31.34% | 8.36% | 3.75 | 1 | 74864.06 | 59990.70 |

## Controls

| arm | role | final equity | return | max drawdown | return/DD | fills | min cash | max gross |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| N1_random | control | 100103.38 | 0.10% | 0.41% | 0.25 | 63 | 97850.62 | 2431.56 |
| N2_reversed | control | 101751.34 | 1.75% | 2.81% | 0.62 | 675 | 79681.79 | 19053.16 |
| N3_shuffled | control | 117238.30 | 17.24% | 8.17% | 2.11 | 289 | 73965.93 | 45966.90 |

## Primary comparisons (pre-registered family)

- C - A: mean daily diff 0.00002125, 95% CI [-0.00001493, 0.00006117], bootstrap p(one-sided)=0.12, p(two-sided)=0.24, Holm p=0.48
- B - A: mean daily diff 0.00000697, 95% CI [-0.00002398, 0.00003868], bootstrap p(one-sided)=0.297, p(two-sided)=0.594, Holm p=0.594

## Arm vs benchmark (descriptive, not Holm-adjusted)

The difference is arm minus benchmark; a negative value means the arm underperformed. Compare A with BENCH_20K (same 20k sleeve pool) and B/C with BENCH_25K (same position cap).

- A - BENCH_20K: mean daily diff -0.00009963, 95% CI [-0.00018280, -0.00002412], p(two-sided)=0.014
- A - BENCH_25K: mean daily diff -0.00013385, 95% CI [-0.00024752, -0.00002995], p(two-sided)=0.018
- B - BENCH_20K: mean daily diff -0.00009266, 95% CI [-0.00016410, -0.00002695], p(two-sided)=0.01
- B - BENCH_25K: mean daily diff -0.00012688, 95% CI [-0.00022774, -0.00003313], p(two-sided)=0.01
- C - BENCH_20K: mean daily diff -0.00007838, 95% CI [-0.00014846, -0.00001020], p(two-sided)=0.026
- C - BENCH_25K: mean daily diff -0.00011260, 95% CI [-0.00021277, -0.00001871], p(two-sided)=0.022

## Overfitting diagnostics

- A: Sharpe 0.0370 per bar, expected max under null 0.0330, DSR 0.5616
- B: Sharpe 0.0317 per bar, expected max under null 0.0330, DSR 0.4800
- C: Sharpe 0.0381 per bar, expected max under null 0.0330, DSR 0.5788
- PBO (CSCV, 10 splits): 0.8650793650793651

## Verdict

`INSUFFICIENT`

- D NOT_RUN: no LLM weekly engine was supplied; the primary comparison D-C was not evaluated
