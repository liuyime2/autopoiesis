# R3 — one falsifiable alpha experiment, pre-registered before it runs

This is the pre-registration R3 demands: a single hypothesis family, frozen by content
hash before the run, with the economic prior, the data source, the cost model, the
development window and the sealed holdout, the candidate budget, the minimum
independent sample, the test, and the stopping rule stated up front. The spec bytes
are registered through the existing preregistry (`src/autopoiesis/research/prereg.py`);
tampering after registration changes the hash and `research-verify` says so.

## 1. Hypothesis family

A 5-minute close-to-close momentum threshold on SPY has positive out-of-sample return
after an assumed round-trip cost, over a window whose later 40% of trading days was
not used to choose the threshold.

Family: `spy-price-threshold` (reuses the existing supported evaluator, no DSL
extension).

## 2. Economic prior (why this should work at all)

SPY intraday momentum at the 5-minute horizon is the smallest coherent bet this system
can make: it trades a single liquid instrument, it ignores everything the model knows
about other symbols, and the only claim it makes is that recent direction persists
long enough to survive a round-trip cost. If even this does not survive, the more
elaborate strategies in the library have nothing to stand on, because they are all
combinations of this kind of edge with extra assumptions. The prior is modest - a
small persistence effect in the most liquid equity market on earth - and the test is
deliberately harder than the prior, because a threshold chosen on the same data it is
tested on always looks positive.

## 3. Data source and content hash

- Source: cached SPY 5-minute bars, fetched via `make fetch-replay-bars`, stored in
  the replay cache, content-hashed by `check_tape`.
- Declared window: 2026-04-10T13:30:00Z .. 2026-10-09T19:40:00Z (the same window as
  the 2026-10-08 study, so the result is comparable to the recorded trials).
- The tape is checked with `make research-check-tape` before any verdict is computed:
  chronological, no duplicate timestamps, positive finite prices, no bar outside the
  declared window. A verdict from a tape that is not the tape the spec declared is a
  number with no meaning.

## 4. Cost model

- Commission: 0.05% round trip (the `commission_pct` field).
- Slippage: 0.05% round trip (the `slippage_pct` field).
- Total assumed round-trip cost: 0.10%.
- Observed broker fees are tracked separately, per the attribution rule, and are not
  substituted for the assumed cost. The verdict uses the assumed cost so it is
  reproducible on a re-run of the same tape.

## 5. Development and sealed holdout

- The trading days in the window are split by date, not by bar index: the earliest
  60% of trading days are development, the latest 40% are the sealed holdout.
- The threshold is chosen on the development days only. The holdout days are not read
  by the selection step in any form - not their prices, not their count, not their
  dates - and the spec's registration hash covers the window, so changing the split
  after seeing the data is a different file.
- The holdout is opened at most once. If the development result is negative the
  holdout is never opened and the round is recorded as closed with a negative result.
  Opening it repeatedly until something passes is the same defect as no holdout.

## 6. Candidate budget

- 11 candidates: 10 thresholds (0.0005, 0.001, 0.002, 0.005, 0.0075, 0.010, 0.015,
  0.020, 0.025, 0.030) plus one buy-and-hold baseline.
- `candidate_budget` must equal `thresholds + 1`, enforced by `execution_settings`.
- No further candidates may be added after registration. Adding one is a new spec.

## 7. Minimum independent sample

- 60 folds, each with at least 20 training bars.
- A fold is independent only if its test bars are disjoint in time from the
  selection bars; the walk-forward split enforces this.
- The expected trade count is 100 over the window. Below 30 realised trades the
  verdict is `INSUFFICIENT`, not `FAIL`, and the round records that fact rather than
  pretending to a result.

## 8. Test

- Primary metric: out-of-sample return percentage after cost, on the sealed holdout
  (the 40% of days), for the threshold selected on the development days.
- Secondary: the same figure on the development days, to show how much the selection
  flattered the result.
- Verdicts: `SURVIVES` if the holdout return is positive after cost and the
  one-sided test against zero is significant at the pre-declared level; `OVERFIT` if
  the development return is positive and the holdout is not; `INSUFFICIENT` if the
  sample is too small; `INCONCLUSIVE` otherwise.
- No candidate is promoted on this result. A surviving hypothesis becomes a
  candidate for R4's controlled run, which is where promotion lives.

## 9. Stopping rule

- One spec, one run, one verdict. The round closes when the verdict is written,
  whether it is `SURVIVES` or not.
- No cherry-picking thresholds, no re-running with a different split, no adding
  candidates to force a winner. The 165 trials already on record with 0 PASS are
  the reason this rule exists.
- A negative result is a closed round, not a failed one. It is recorded in the
  trials ledger with the full spec hash, so the next round can state exactly what
  was tried and why it did not work.

## 10. What this is not

- It is not a strategy. Nothing here places an order, and `PASS_SCREENED` is not
  alpha.
- It is not a claim about the model. The model is not involved; this tests a
  price rule, so a positive result says nothing about whether the LLM adds value.
  That is R4's question, and it needs R3's contract to be meaningful.
- It is not evidence yet. Until the run executes and the verdict is written, the
  only thing that exists is the frozen question.
EOF
echo "written"