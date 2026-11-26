# Plan and pre-registration (written 2026-11-26, before any candidate was run)

Approved by the account holder 2026-11-26: fix the three defects below. Daily-loss limit, configured
ETFs and the five retired strategies stay as they are.

## 1. Repeated screening inflates both error rates
- Object: `offline_validation.validate` at 6b86f93, re-run after every day as the daemon does.
- Baseline (SYNTHETIC no-skill strategies on journalled outcomes, 2000 runs, 20 days): ever PASS
  5.8-5.9% (single look 1.4-1.6%), ever REJECT 5.8-6.7% (single look 3.0-3.4%). REJECT is permanent.
- History: every day the symbol has (not only 20), 3 outcomes per day, paired rows, 2000 runs per
  policy: null_cycle, null_day, null_hold. Symbols with >=20 such days; rows before 2026-11-26.
- Criterion: for each policy, the Wilson-95 upper bound of ever-PASS <= 5% and of ever-REJECT <= 5%.
- Candidates, fixed here, tighten only: PASS z in {1.645, 2.0, 2.33, 2.58}; REJECT multiple in
  {2.0, 2.33, 2.58, 3.0}. Take the smallest of each that meets the criterion for all three policies.
- Also report recall (ever PASS, ever REJECT) at skill q=0.5 and 1.0 for current and chosen values.
- If no candidate meets the criterion, change nothing and say so.

## 2. The raw-ratio PASS reason always says "none were losing trades"
State the number of losing trades. Test with one.

## 3. The curriculum proposes families admission must refuse
- Baseline (journal since 11-12): 148 proposals, 21 accepted, 45 refused as a full signal family.
- Tell the model which families are full. Admission rules do not change.
- Verified by unit test; the live refusal share is only measurable after deploy (report after 5
  sessions, whatever it shows).

## Amendment 1 (2026-11-26, after the first run stopped on its own assertion, before any result)
The run stopped at null_hold seed 455: 18 of 35 right against 18.0 expected, every day's margin 0
(across-days se 0), screened PASS. The margin was computed from the ratio rounded to 6 decimals, so
a residue of +2.9e-7 cleared a threshold of 0. A defect, fixed first (tighten only): the margin uses
the unrounded ratio and a pass needs it to exceed the threshold by more than 1e-9. The calibration's
object is the screen with that fix; the candidates and criterion above are unchanged. The script's
reconstruction compares the 6-decimal fields with a tolerance of 1e-6 and keeps its assertion.

## Amendment 2 (2026-11-26, same, before any result)
A second stop was the script's: a margin within the 6-decimal rounding of -2 standard errors. The
script now takes the screen's own verdict at the current thresholds and still asserts that its
reconstruction differs only within 1e-5 of a threshold.

## Result (`result.json`; two runs identical)

SPY only, 31 days, 2000 runs per policy. "Ever" = at any of the daily looks.

| Policy | ever PASS, 1.645 -> 2.0 | ever REJECT, 2.0 -> 2.58 |
|---|---|---|
| null_cycle | 6.3% -> 2.5% | 10.9% -> 2.2% |
| null_day | 6.1% -> 2.5% | 11.0% -> 2.3% |
| null_hold | 10.3% -> 4.0% (upper 4.95%) | 7.8% -> 1.1% |
| skill q=0.5 | 55.5% -> 37.5% | 0.3% -> 0.1% |
| skill q=1.0 | 98.8% -> 95.9% | 0% -> 0% |

Chosen: PASS 2.0, REJECT 2.58, the smallest candidates meeting the criterion for all three nulls.
The PASS side is tighter. The REJECT side retires less: a no-skill strategy is also retired less
often, so it stays in the library longer (never adding, since adding needs a PASS).
Limits: one symbol; 31 days; the error rates rise again as the history grows, so this has to be
re-run as days accumulate. null_hold clears its bound by 0.05 points. Not evidence of profit.
