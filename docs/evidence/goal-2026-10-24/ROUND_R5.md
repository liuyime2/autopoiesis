# Round R5 (preregistered 2026-10-24, before any treatment-period strategy exists)

**Question.** Does showing the curriculum the signal ledger, and refusing cash-ETF and TREND_FOLLOW
proposals at admission, change what the curriculum proposes for the better?

**Baseline.** Strategies created 2026-10-15 .. 2026-10-24T23:55Z under the old rules, as the script
counts them at registration: 44, of which 38 TREND_FOLLOW, 12 on cash-equivalent ETFs, 0 citing the
ledger, and 10 old enough to judge, all 10 RETIRED or PAUSED (dead share 1.0). **Treatment.** Strategies created from
2026-10-25T01:57Z (build b4868493: refusals + ledger summary in the prompt).

**Measured, from the strategy files only** (`round_r5.py <runtime/autopoiesis/strategies>`):
number proposed, kind mix, proposals on cash-equivalent ETFs, proposals whose rationale cites the
ledger, and the share of *mature* (>= 7 days old) strategies that are RETIRED or PAUSED.

**Verdict rule, fixed now.**
- NOT_YET_JUDGEABLE until >= 15 treatment strategies exist and >= 10 of them are 7 days old.
- IMPROVED only if all hold: 0 cash-ETF proposals; >= 50% cite the ledger; mature dead-share < 0.8.
  (A first draft said "<= the baseline's", which at a baseline of 1.0 cannot fail; corrected on
  2026-10-25 while the treatment set was still empty, so no result could have steered it.)
- Otherwise NOT_IMPROVED, and that is reported as the result.

**What IMPROVED would and would not mean.** It would mean the proposals are less wasteful and more
tied to evidence. It would not mean they make money: that is the 60-valid-day benchmark's question
(27/60 on 2026-10-23), and the signal ledger still holds no price-direction signal.
