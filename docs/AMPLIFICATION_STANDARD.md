# Amplification standard — when the agent may run with more capital

Written 2026-10-25, before any result that would make it convenient. The point of
writing it now is that "we beat the benchmark" is only a claim if the bar was set
before the run.

## The bar

A candidate may be amplified (more capital, wider universe, longer horizon) only when
**all** of the following hold on a single consistent-policy cohort of at least 60
effective trading days, with the same capital and the same costs as the baseline:

1. **Risk-adjusted return after costs is not below SPY buy-and-hold** on the same
   window. "Risk-adjusted" means Sharpe or Sortino, not raw return. Raw return alone
   is not enough, because the core sleeve already shows that lower drawdown can be
   bought with lower return, and that trade is only worth making if the risk-adjusted
   figure holds.
2. **Maximum drawdown is strictly smaller than SPY's** on the same window. Both
   figures computed from daily marks, peak to trough, including the sleeve's own
   positions.
3. **No hard risk limit was ever touched.** Guardian refusal, daily loss cap, total
   exposure cap, stale-quote refusal, market-open check: zero breaches across the
   whole 60 days. A breach voids the cohort regardless of the other two.
4. **The cohort is one policy version.** (Amended 2026-10-31, see below.) A change to the **code, the
   configuration, the Guardian thresholds, the model, or the admission and accumulation rules** resets
   the counter. The 60 days must be contiguous under one such version, or the run is not comparable.
   **The set of strategies the system itself generates, tests, pauses and retires is not part of the
   version**: that is the self-improvement being evaluated, and a standard that restarts every time it
   moves can never be met. Version is read from the daemon's source fingerprint in
   `docs/evidence/deployments.jsonl` (`make benchmark` prints the cohort length); a change to the
   environment file is not detected by it and must be recorded by hand.
5. **The benchmark window is the account's own window** (account opened 2025-10-21,
   12 months before the earliest recorded cycle), not a window chosen to flatter.
   Mixing windows to obtain a passing figure is the same defect as fabricating data.

## What does not count

- A positive raw return that is below SPY's. The agent has not earned the right to
  more capital if it merely made money in a rising market.
- A passing figure on a 27-day window. 27 days is 27 days; the number is not
  negotiable and is not rounded up.
- A `PASS_SCREENED` verdict. That is decision quality, not alpha, and the 2026-10-20
  review measured 165 trials with 0 PASS for exactly this reason.
- A result from a cohort that changed policy mid-run. The counter resets; the
  prettier window does not.
- A result whose costs were assumed rather than observed. Observed fees are tracked
  separately and reported alongside.

## What happens if the bar is not met

The result is recorded, not hidden. A failing cohort is evidence that the current
approach does not earn more capital, and the next round states what will change and
why. The bar is not lowered to obtain a pass; that is the same defect as fabricating
data, and it is visible in the trials ledger.

## What happens if the bar is met

Amplification is a separate, deliberate step with its own record: the capital
increase, the date, the cohort it rests on, and the new policy version. It is not
automatic, and it is not retroactive.

## Amendment of item 4, 2026-10-31

Written before any 60-day result exists. The original wording reset the counter on "a change to the strategy
set". Between 2026-10-25 and 2026-10-30 the curriculum created 23 strategies and the lifecycle paused or
retired 13, so under that wording the counter could never reach 60 for a system whose purpose is to
evolve its own strategies; on the same days the deployed code changed four times, which is what actually
restarts it. The amendment moves the reset from the first to the second, and says so here rather than
reading the old wording more loosely.

## Amendment of 2026-11-05 — 60 days no longer blocks development

Written as part of `docs/EXECUTION_PLAN_2026-11-05.md` (step 0.6). The bar above is
**retained in full** and still governs amplification, claiming profitability, and
relaxing any limit. What changes is only its *gating* role:

- Engineering and research work continues in the simulated environment regardless
  of the 60-day count. Development is not frozen waiting for it.
- The 60-day cohort, or equivalent independent evidence, is still required before
  any of: increased capital, wider universe, longer horizon, or any public claim
  of profitability. `make benchmark` continues to print the live cohort count.
- The original text above is preserved verbatim; this amendment is additive.

## Status as of 2026-10-25 (historical; `make benchmark` prints the live cohort count)

- Effective trading days on the current policy cohort: 27 of 60.
- Model vs rule evidence: 9 days of 20+ required.
- R5 controlled experiment: treatment group 0 of 15 strategies, 0 of 10 at 7 days.
- Price-direction alpha: 0 SURVIVES in 180 trials.
- Core sleeve: enabled, SPY, $20,000 cap, 10% vol target. It is a risk-reduction
  tool, not a return source, and it does not count toward this bar on its own.
