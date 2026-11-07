# Handoff — G4 v2 diagnostic

## Verdict

**INSUFFICIENT.** A/B/C ran; D is `NOT_RUN`. The primary comparison D-C was not evaluated, so this is not a four-arm verdict.

## Run

- Experiment code: `b3ed494be12ac9f98de84fd9539e4846d8d7efbd`
- Spec: `/localscratch/liuyime2/QuantGroup/.worktrees/edge-first/configs/experiment_4arm_v2.spec` (SHA-256 `8d024b263d47be41b9e3e0d297fbffc1a473580511f5d7ba161f77cede61275f`)
- Data: 1579 SPY 1Day bars, 2020-07-27..2026-11-05, SHA-256 `e93e077b701f1be48c19a659c53fa64f1ef9544a454c3de075e64bfd14b32d13`
- Costs: commission 1.0000%, slippage 0.2000%
- Benchmarks: BENCH_20K, BENCH_25K
- Controls: N1_random, N2_reversed, N3_shuffled
- Minimum effect for PROVEN: 0.00000000 per bar

## Results

| trial | return | max drawdown | return/DD | fills | refusals | min cash | max gross |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 7.20% | 2.75% | 2.62 | 95 | 3 | 83153.44 | 20898.71 |
| B | 8.28% | 6.03% | 1.37 | 691 | 389 | 75313.27 | 28249.29 |
| C | 10.72% | 5.34% | 2.01 | 404 | 605 | 76094.06 | 29006.08 |
| BENCH_20K | 24.83% | 6.77% | 3.67 | 1 | 0 | 80087.11 | 47525.10 |
| BENCH_25K | 31.34% | 8.36% | 3.75 | 1 | 0 | 74864.06 | 59990.70 |
| N1_random | 0.10% | 0.41% | 0.25 | 63 | 15 | 97850.62 | 2431.56 |
| N2_reversed | 1.75% | 2.81% | 0.62 | 675 | 405 | 79681.79 | 19053.16 |
| N3_shuffled | 17.24% | 8.17% | 2.11 | 289 | 1207 | 73965.93 | 45966.90 |

## Primary comparisons (pre-registered family)

- C - A: mean daily diff 0.00002125, 95% CI [-0.00001493, 0.00006117], two-sided p=0.24, Holm p=0.48
- B - A: mean daily diff 0.00000697, 95% CI [-0.00002398, 0.00003868], two-sided p=0.594, Holm p=0.594

## Benchmarks (descriptive, not Holm-adjusted)

The difference is arm minus benchmark. Compare A with BENCH_20K (same 20k sleeve pool) and B/C with BENCH_25K (same position cap).

- A - BENCH_20K: mean daily diff -0.00009963, 95% CI [-0.00018280, -0.00002412], two-sided p=0.014
- A - BENCH_25K: mean daily diff -0.00013385, 95% CI [-0.00024752, -0.00002995], two-sided p=0.018
- B - BENCH_20K: mean daily diff -0.00009266, 95% CI [-0.00016410, -0.00002695], two-sided p=0.01
- B - BENCH_25K: mean daily diff -0.00012688, 95% CI [-0.00022774, -0.00003313], two-sided p=0.01
- C - BENCH_20K: mean daily diff -0.00007838, 95% CI [-0.00014846, -0.00001020], two-sided p=0.026
- C - BENCH_25K: mean daily diff -0.00011260, 95% CI [-0.00021277, -0.00001871], two-sided p=0.022

## Overfitting diagnostics

- A: Sharpe 0.0370 per bar, expected max under null 0.0330, DSR 0.5616
- B: Sharpe 0.0317 per bar, expected max under null 0.0330, DSR 0.4800
- C: Sharpe 0.0381 per bar, expected max under null 0.0330, DSR 0.5788
- PBO (CSCV): 0.8650793650793651

## What the evidence says

- All three arms underperform both buy-and-hold benchmarks in absolute return and in return per unit of max drawdown.
- The arm-minus-benchmark CIs exclude zero in all six descriptive comparisons, but those comparisons were not pre-registered and are not Holm-adjusted; the consistent direction is the stronger evidence.
- The shuffled-history control returns more than every arm, so a rule that cannot beat a destroyed history is not evidence of signal.
- The primary family does not survive Holm, and PBO is high; the arm ranking is not a reliable selection.

## What this does not prove

- It does not test D: no weekly LLM engine exists.
- C is a fixed SMA20/50 rule, not the non-LLM automatic search the G4 design calls for, and B and C are standalone SPY rules rather than core sleeve plus satellites. The questions 'value of automatic optimization' and 'core + rule' are not tested by this run.
- The universe is one symbol (SPY). Cross-asset risk budgeting, VIX, FRED, EDGAR and news are not in this dataset.
- No parameter is fitted in any arm, so purging and embargo have nothing to separate; they become required the moment a search arm is added.
- It is historical development evidence, not unseen data.
- The 1.00% commission is a stress assumption. Lower costs would help the high-turnover arms, but the benchmarks already lead before costs.
- The arms are deterministic, so their seed variance is zero. Only the random control has seed variance, and only seed 0 was run.
- `min_effect` is 0.0 because the registered spec did not declare one; a confirmatory spec must declare an economic floor before the run.

## Next

1. Do not promote any arm to G5 on this evidence.
2. Build the weekly LLM D arm with persisted request/response hashes, or pre-register a three-arm confirmatory experiment with a declared `min_effect` and a forward window.
3. Fix the arm composition before a new arm is added: B/C buy one share per signal and accumulate, while A targets a dollar pool, and none of the three is a core sleeve plus satellites on a multi-symbol universe. A fair comparison needs the same position-sizing rule across arms and the planned core-plus-satellite composition.
4. Keep both benchmarks and the negative controls in every future report.

## Data quality

- Duplicate timestamps: 0
- Non-positive prices: 0
- Zero-volume bars: 0
- Max calendar gap: 4 days
- Weekday coverage: 96.3392% (holidays count as uncovered)

## Reproduce

```sh
python tools/run_experiment.py --offline --out reports/20261106/v2
python tools/report_artifacts.py --report reports/20261106/v2 \
    --cache runtime/autopoiesis/replay/SPY_1Day_2020-07-27_2026-11-05.json
python -m autopoiesis.research.prereg verify configs/experiment_4arm_v2.spec
```
