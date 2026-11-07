# Statistical report

Spec SHA-256: `8d024b263d47be41b9e3e0d297fbffc1a473580511f5d7ba161f77cede61275f`
Data SHA-256: `e93e077b701f1be48c19a659c53fa64f1ef9544a454c3de075e64bfd14b32d13`
Code commit: `84b961fb2164fb09bfb037e1339e78a8abdcf72f`
Bars: 1579 SPY 1Day 2020-07-27..2026-11-05
Costs: commission 0.5000%, slippage 0.1000%
Minimum effect for PROVEN: 0.00000000 per bar

## Arms and benchmarks

| arm | role | final equity | return | max drawdown | return/DD | fills | min cash | max gross |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | arm | 108668.08 | 8.67% | 2.70% | 3.21 | 95 | 83426.80 | 20898.71 |
| B | arm | 110220.99 | 10.22% | 5.47% | 1.87 | 691 | 76068.42 | 28249.29 |
| C | arm | 111835.26 | 11.84% | 5.03% | 2.35 | 404 | 76235.80 | 29006.08 |
| BENCH_20K | benchmark | 124944.40 | 24.94% | 6.76% | 3.69 | 1 | 80205.17 | 47525.10 |
| BENCH_25K | benchmark | 131487.19 | 31.49% | 8.35% | 3.77 | 1 | 75013.08 | 59990.70 |

## Controls

| arm | role | final equity | return | max drawdown | return/DD | fills | min cash | max gross |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| N1_random | control | 100297.53 | 0.30% | 0.37% | 0.81 | 63 | 98005.15 | 2431.56 |
| N2_reversed | control | 103707.18 | 3.71% | 2.53% | 1.47 | 675 | 80291.59 | 19053.16 |
| N3_shuffled | control | 117907.88 | 17.91% | 7.72% | 2.32 | 289 | 74635.51 | 45966.90 |

## Primary comparisons (pre-registered family)

- C - A: mean daily diff 0.00001897, 95% CI [-0.00001649, 0.00005794], bootstrap p(one-sided)=0.139, p(two-sided)=0.278, Holm p=0.556
- B - A: mean daily diff 0.00000958, 95% CI [-0.00002137, 0.00004140], bootstrap p(one-sided)=0.243, p(two-sided)=0.486, Holm p=0.556

## Arm vs benchmark (descriptive, not Holm-adjusted)

The difference is arm minus benchmark; a negative value means the arm underperformed. Compare A with BENCH_20K (same 20k sleeve pool) and B/C with BENCH_25K (same position cap).

- A - BENCH_20K: mean daily diff -0.00009159, 95% CI [-0.00017391, -0.00001540], p(two-sided)=0.022
- A - BENCH_25K: mean daily diff -0.00012593, 95% CI [-0.00023830, -0.00002168], p(two-sided)=0.022
- B - BENCH_20K: mean daily diff -0.00008202, 95% CI [-0.00015340, -0.00001541], p(two-sided)=0.016
- B - BENCH_25K: mean daily diff -0.00011635, 95% CI [-0.00021724, -0.00002163], p(two-sided)=0.014
- C - BENCH_20K: mean daily diff -0.00007262, 95% CI [-0.00014220, -0.00000364], p(two-sided)=0.04
- C - BENCH_25K: mean daily diff -0.00010695, 95% CI [-0.00020599, -0.00001289], p(two-sided)=0.028

## Overfitting diagnostics

- A: Sharpe 0.0445 per bar, expected max under null 0.0331, DSR 0.6728
- B: Sharpe 0.0390 per bar, expected max under null 0.0331, DSR 0.5921
- C: Sharpe 0.0420 per bar, expected max under null 0.0331, DSR 0.6371
- PBO (CSCV, 10 splits): 0.8809523809523809

## Verdict

`INSUFFICIENT`

- D NOT_RUN: no LLM weekly engine was supplied; the primary comparison D-C was not evaluated
