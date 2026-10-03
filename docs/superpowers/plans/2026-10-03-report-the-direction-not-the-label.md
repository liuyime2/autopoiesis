# Report the direction of the miscalibration, not just the word for it

Date: 2026-10-03
Status: **DONE - implemented, measured, gated**
Baseline commit for rollback: `34f56d8`
Scope: `src/min_agent/doctor.py`, `tests/min_agent/test_doctor_proof_checks.py`, plus
the figures three documents quote.

---

## 0. The question

The market is closed until Monday, so the measurement from the previous plan - whether the
313 released cycles produce evidence - cannot be taken. This turn took the largest
unexamined finding instead: the model is **MIS-CALIBRATED, Brier 0.433 against the 0.25 a
constant 0.5 claim scores**, and `Guardian` gates on the model's stated confidence.

So: does the confidence gate help, and what does the reported number invite an operator to
do?

## 1. What the journal says, measured independently first

Pairing every decision with the counterfactual verdict later scored against it, for the
195 model-authored decisions that have an outcome:

```
confidence bucket     n   correct
  0.0-0.1            32     0.91
  0.5-0.6            51     0.71
  0.6-0.7            43     0.51
  0.7-0.8            66     0.02
  0.8-0.9             3     0.33
```

Monotonically **inverted**: the least confident decisions are right 91% of the time and
the most confident 2%.

Separating the two error kinds, because "harmful" and "missed opportunity" are not the
same thing:

```
TRADES (34 scored):  conf 0.5-0.6 -> 0.00 FALSE_TRADE
                     conf 0.6-0.7 -> 0.08
                     conf 0.7-0.8 -> 0.86      n=7
HOLDS  (161 scored): conf 0.0-0.1 -> 0.09 MISSED_ALPHA
                     conf 0.5-0.6 -> 0.31
                     conf 0.6-0.7 -> 1.00
                     conf 0.7-0.8 -> 1.00
```

Both directions are bad at high confidence: a confident trade loses 86% of the time, and a
confident hold misses every move. **There is no threshold that fixes this**, because
raising the gate rejects the low-confidence decisions that were right *and* admits the
high-confidence trades that lose.

And the gate is nearly inert: across 1179 cycles, exactly **one** decision was refused for
`decision confidence below minimum`. Of the 8 model-authored `FALSE_TRADE`s, 7 were let
through.

## 2. The defect

`calibration.calibrate` had already computed and stated the whole finding. Its verdict on
the live journal is:

> MIS-CALIBRATED: Brier 0.433 against the 0.25 a constant 0.5 claim scores, so the stated
> confidence is worse than useless. Accuracy is 46.2% and the most confident bucket is
> **5.6%** (margin **-40.6%** if positive), which on a base rate that high cannot
> discriminate anything

And `doctor` printed `result.verdict.split(":")[0]`, which is:

> MIS-CALIBRATED

The top-bucket accuracy, the base rate and the sign of the margin were computed, written
into a sentence, and then discarded on the way to the reader.

That is the same defect `docs/ARCHITECTURE.md` records three times already - "a fact was
computed, reported, and never reached anything that could act on it" - in a fourth place,
and the cruelest version: the fact reached the reporting layer intact and was thrown away
one line later.

It matters because the label alone invites a specific wrong action. "Miscalibrated" reads
as *overconfident*, so the obvious response is to raise `min_confidence`. The measured
direction says the opposite. A reader given only the label cannot tell those apart, and
acting on it would degrade a risk gate - which `AGENTS.md` §17 treats as a boundary not to
be weakened for a narrower success criterion.

## 3. The change

**Delete the truncation.** The verdict is reported whole.

The separate `, Brier ..., base rate ...` append was dropped rather than kept: every
non-`INSUFFICIENT` branch of `calibrate` already states both inside the verdict, so
appending them again printed each figure twice. An `INSUFFICIENT` verdict names neither,
and that absence is the information.

What the operator reads now, from the live journal:

```
[WARN] model calibration  314 llm decision(s), 210 scored, 71 still awaiting an outcome;
MIS-CALIBRATED: Brier 0.433 against the 0.25 a constant 0.5 claim scores, so the stated
confidence is worse than useless. Accuracy is 46.2% and the most confident bucket is 5.6%
(margin -40.6% if positive), which on a base rate that high cannot discriminate anything
```

The independent measurement in section 1 and the module's own figure agree: 2% and 5.6%
for the top bucket against a 46.2% base rate. Two derivations, same direction.

## 4. A second defect, in the test that should have caught the first

`_Cfg2`, the fixture for the calibration checks, had no `min_confidence`.
`_check_model_calibration` passes `config.min_confidence` into `calibrate`, so the check
raised `AttributeError`, caught it, and reported `evaluation failed: AttributeError`.

`test_a_miscalibrated_model_is_not_reported_as_healthy` asserts the result is a warning -
and `evaluation failed` **is** a warning. So a test named for miscalibrated models was
passing through the swallow-and-warn path without ever constructing one. The fixture's own
comment warns that omitting attributes "made the check report 'evaluation failed'... which
is its own swallow-and-warn path"; the same omission was still there for this field.

With the field added it reaches its real branch and reports `NO_DATA: no decisions from
'llm' are on record`. The lesson is the one already written in that comment: a check that
catches everything will hide a broken caller behind a plausible message, and a test that
accepts any warning cannot tell which warning it got.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| High confidence is worse, not better | a bucket ordering where accuracy rises with confidence |
| The module already knew | a `calibrate` verdict that omits the top-bucket comparison |
| The gate is inert | many decisions refused for `decision confidence below minimum` |

The third is checkable now and is 1. It also means **this change cannot improve trading
results**, because the gate it describes was barely doing anything. What it does is stop
the number from inviting a change that the measurement says would be harmful - which is
the honest outcome available while the market is shut.

## 6. Verification

Two tests in `tests/min_agent/test_doctor_proof_checks.py`, stubbing `calibrate` with the
live journal's figures because the property under test is what `doctor` does with the
verdict it is handed:

- `test_the_calibration_check_reports_the_verdict_and_not_only_its_label` - `5.6%`,
  `-40.6%` and `46.2%` all reach the reader; `detail != "MIS-CALIBRATED"`; each figure
  appears once.
- `test_an_insufficient_calibration_still_says_so` - the `INSUFFICIENT` case still reports,
  and prints no `Brier` because none exists.

`make verify`: 49 classes, 0 failed, 1309 test executions across 61 files, 842 collected.
`make lint` clean; `make type` clean on 41 files. Daemon on `571f3569c716e182`.

## 7. Still open

1. **The 34 model-authored scored trades** - 26 NEUTRAL, 8 FALSE_TRADE, none profitable -
   are too few to tune anything on. That is the binding constraint on every question about
   the model's trading ability, and it is not fixable by code: at ~7 model trades per
   trading day the sample grows slowly and the market is shut until Monday.
2. **Provenance is fixed going forward** - 197 scored decisions since 2026-09-28 with zero
   unattributed, against 350 unattributed in June. Nothing to do; recorded so the June
   figure is not mistaken for a live defect.
3. **Whether the 313 released cycles produce evidence**, which needs a real trading day.
4. **`STRATEGY_EVALUATION_RECORDED`** ~2MB/day and the **`PNL_EVIDENCE_RECORDED`** dedup
   that needs a cheap journal tail read, both still open from earlier.
