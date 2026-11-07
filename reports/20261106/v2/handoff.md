# Handoff — G4 v2 diagnostic

## Verdict

**INSUFFICIENT.** A/B/C ran; D is `NOT_RUN`. The primary comparison D-C was not evaluated, so this is not a four-arm verdict.

## Run

- Experiment code: `84b961fb2164fb09bfb037e1339e78a8abdcf72f`
- Spec: `/localscratch/liuyime2/QuantGroup/.worktrees/edge-first/configs/experiment_4arm_v2.spec` (SHA-256 `8d024b263d47be41b9e3e0d297fbffc1a473580511f5d7ba161f77cede61275f`)
- Data: 1579 SPY 1Day bars, 2020-07-27..2026-11-05, SHA-256 `e93e077b701f1be48c19a659c53fa64f1ef9544a454c3de075e64bfd14b32d13`
- Costs: commission 0.5000%, slippage 0.1000%
- Benchmarks: BENCH_20K, BENCH_25K
- Controls: N1_random, N2_reversed, N3_shuffled
- Minimum effect for PROVEN: 0.00000000 per bar

## Results

| trial | return | max drawdown | return/DD | fills | refusals | min cash | max gross |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 8.67% | 2.70% | 3.21 | 95 | 3 | 83426.80 | 20898.71 |
| B | 10.22% | 5.47% | 1.87 | 691 | 389 | 76068.42 | 28249.29 |
| C | 11.84% | 5.03% | 2.35 | 404 | 605 | 76235.80 | 29006.08 |
| BENCH_20K | 24.94% | 6.76% | 3.69 | 1 | 0 | 80205.17 | 47525.10 |
| BENCH_25K | 31.49% | 8.35% | 3.77 | 1 | 0 | 75013.08 | 59990.70 |
| N1_random | 0.30% | 0.37% | 0.81 | 63 | 15 | 98005.15 | 2431.56 |
| N2_reversed | 3.71% | 2.53% | 1.47 | 675 | 405 | 80291.59 | 19053.16 |
| N3_shuffled | 17.91% | 7.72% | 2.32 | 289 | 1207 | 74635.51 | 45966.90 |

## Primary comparisons (pre-registered family)

- C - A: mean daily diff 0.00001897, 95% CI [-0.00001649, 0.00005794], two-sided p=0.278, Holm p=0.556
- B - A: mean daily diff 0.00000958, 95% CI [-0.00002137, 0.00004140], two-sided p=0.486, Holm p=0.556

## Benchmarks (descriptive, not Holm-adjusted)

The difference is arm minus benchmark. Compare A with BENCH_20K (same 20k sleeve pool) and B/C with BENCH_25K (same position cap).

- A - BENCH_20K: mean daily diff -0.00009159, 95% CI [-0.00017391, -0.00001540], two-sided p=0.022
- A - BENCH_25K: mean daily diff -0.00012593, 95% CI [-0.00023830, -0.00002168], two-sided p=0.022
- B - BENCH_20K: mean daily diff -0.00008202, 95% CI [-0.00015340, -0.00001541], two-sided p=0.016
- B - BENCH_25K: mean daily diff -0.00011635, 95% CI [-0.00021724, -0.00002163], two-sided p=0.014
- C - BENCH_20K: mean daily diff -0.00007262, 95% CI [-0.00014220, -0.00000364], two-sided p=0.04
- C - BENCH_25K: mean daily diff -0.00010695, 95% CI [-0.00020599, -0.00001289], two-sided p=0.028

## Overfitting diagnostics

- A: Sharpe 0.0445 per bar, expected max under null 0.0331, DSR 0.6728
- B: Sharpe 0.0390 per bar, expected max under null 0.0331, DSR 0.5921
- C: Sharpe 0.0420 per bar, expected max under null 0.0331, DSR 0.6371
- PBO (CSCV): 0.8809523809523809

## What the evidence says

- All three arms underperform both buy-and-hold benchmarks in absolute return and in return per unit of max drawdown.
- The arm-minus-benchmark CIs exclude zero in all six descriptive comparisons, but those comparisons were not pre-registered and are not Holm-adjusted; the consistent direction is the stronger evidence.
- The shuffled-history control returns more than every arm, so a rule that cannot beat a destroyed history is not evidence of signal.
- The primary family does not survive Holm, and PBO is high; the arm ranking is not a reliable selection.

## What this does not prove

- It does not test D: no weekly LLM engine exists.
- It is historical development evidence, not unseen data.
- The 0.5% commission is a stress assumption. Lower costs would help the high-turnover arms, but the benchmarks already lead before costs.
- The arms are deterministic, so their seed variance is zero. Only the random control has seed variance, and only seed 0 was run.
- `min_effect` is 0.0 because the registered spec did not declare one; a confirmatory spec must declare an economic floor before the run.

## Next

1. Do not promote any arm to G5 on this evidence.
2. Build the weekly LLM D arm with persisted request/response hashes, or pre-register a three-arm confirmatory experiment with a declared `min_effect` and a forward window.
3. Fix the sizing mismatch before a new arm is added: B/C buy one share per signal and accumulate, while A targets a dollar pool. A fair comparison needs the same position-sizing rule across arms.
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
