# Control the calibration report for the day, because the day is what it is measuring

Date: 2026-10-03
Status: **DONE - the report now controls for the day; the lesson it feeds is gated on the
controlled number**
Baseline commit for rollback: `bba8b79`
Scope: `src/min_agent/calibration.py`, the two call sites that read its verdict
(`src/min_agent/doctor.py`, `src/min_agent/daemon.py`), and the figures three documents
quote. No new check class; no new module.

---

## 0. The question this answers

`doctor` has reported `MIS-CALIBRATED` every day for weeks, and the daemon publishes a
lesson to the model built from it:

> On this account your stated confidence does not predict whether you are right, and the
> relationship runs backwards. … a 0.6-0.8 confidence bucket was right 6% of the time while
> a 0.0-0.2 bucket was right 91%. … Judge your own past decisions by what followed them,
> not by how sure you felt.

**This is a measurement the system acts on — it changes the model's behaviour — so it has to
be a measurement of the model.** The claim to test: does the reported inversion survive
control for the trading day?

## 1. The baseline, as a measurement

Same inputs `doctor` uses: `config.counterfactual_horizon_hours = 24.0`,
`assumed_round_trip_cost_pct = 0.05`, `min_confidence = 0.5`, source `llm`. 314 LLM
decisions, 241 scored. Reproduced exactly: `Brier 0.396`, `base_rate 53.1%`, top bucket
`13.9%`.

### 1.1 Correctness is a property of the day, not of the decision

Per-day base rate of scored decisions:

| day | scored | correct | rate |
|---|---|---|---|
| 2026-09-28 | 65 | 63 | **96.9%** |
| 2026-09-29 | 64 | 17 | **26.6%** |
| 2026-09-30 | 47 | 40 | **85.1%** |
| 2026-10-01 | 65 | 8 | **12.3%** |

That is the market's direction, not the model's skill: on a down day a HOLD is right and on
an up day a HOLD is `MISSED_ALPHA`. Any statistic that compares decisions across days is
comparing days.

### 1.2 Within a day, the confidence strata are indistinguishable

Hold accuracy, split by the model's own stated confidence:

| day | `conf < 0.5` | `conf ≥ 0.5` |
|---|---|---|
| 2026-09-28 | 29/29 = 100% | 33/33 = 100% |
| 2026-09-29 | 0/3 = 0% | 1/44 = 2% |
| 2026-09-30 | — | 34/35 = 97% |
| 2026-10-01 | — | 0/52 = 0% |

The strata move **together**. The raw 90.6%-versus-6.3% spread is entirely between days.

### 1.3 How much of the Brier is the day

Comparing each bucket against the base rate of the days it appears on:

```
Brier raw          0.3962
Brier day-adjusted 0.2705     <- expectation = that day's own base rate
skill (raw - adj) +0.1256    -> a third of the reported mis-calibration is the day
```

Per-bucket margin over its own days, all decisions:

| bucket | n | accuracy | expected from days | margin |
|---|---|---|---|---|
| 0.0-0.2 | 32 | 0.906 | 0.903 | **+0.003** |
| 0.4-0.6 | 125 | 0.680 | 0.567 | +0.113 |
| 0.6-0.8 | 79 | 0.139 | 0.329 | **−0.190** |

HOLDs only — every bucket within **one point** of its days:

| bucket | n | accuracy | expected | margin |
|---|---|---|---|---|
| 0.0-0.2 | 32 | 0.906 | 0.908 | −0.002 |
| 0.4-6.0 | 96 | 0.635 | 0.639 | −0.003 |
| 0.6-0.8 | 63 | 0.063 | 0.074 | −0.010 |

## 2. The defect, stated as one sentence

**`calibrate` compares decisions across trading days to decide whether confidence predicts
correctness, so it reports the market's direction as the model's character — and the daemon
publishes that reading to the model as a lesson on every maintenance pass.**

The 90.6% "accurate" bucket is the 96.9% base rate of the one day it came from. It is not a
model that is right when unsure; it is a Tuesday.

## 3. What survives the control, and what does not

- **Not supported:** "confidence is inverted", "worse than useless", "the most confident
  bucket is 13.9% against a 53.1% base rate", and the lesson built on all three.
- **Still true:** day-adjusted Brier is 0.2705 against the 0.25 a constant claim scores, so
  stated confidence adds *negative* information overall — but by a third of what is
  reported, and not for the reason given.
- **Genuinely adverse, and worth keeping:** on trades the 0.6-0.8 bucket is **19 points
  below** the days it appears on. High-confidence trades underperform. That is the finding
  the report should be making.

  Measured later the same day, that margin is a fact about the model rather than an argument
  for moving the gate: the LLM's lowest stated trade confidence is exactly `min_confidence`,
  so nothing sits below any threshold to block, and every setting above 0.5 blocks sets that
  underperform their own days. See
  `docs/history/plans/2026-10-03-min-confidence-what-it-is-worth.md`.

## 4. The minimal change

**Add the day's expectation to the comparison. Change no gate.**

1. `CalibrationRow` carries `day`, set in `build_rows` from the record's own snapshot
   timestamp. No new source of truth — the timestamp is already in the journal.
2. `calibrate` computes per-day base rates, a day-adjusted Brier, and a per-bucket margin
   against those rates. Both numbers appear in the payload next to the existing ones, so the
   report keeps its current fields and gains the control.
3. The verdict text uses the margin, not the raw comparison, and says which day effect it
   removed. `MIS-CALIBRATED` survives — the day-adjusted Brier is still above 0.25 — but the
   sentence about the top bucket becomes a statement about the margin.
4. `_record_calibration_lesson` quotes the day-adjusted numbers and, when the buckets do not
   separate within days, publishes no inversion claim at all.

Not done, deliberately: `min_confidence` is not touched. Changing a risk-adjacent gate needs
its own experiment with its own falsifier, not a side effect of fixing a measurement.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| Hold accuracy does not differ by confidence within a day | A day where the `conf<0.5` and `conf≥0.5` strata differ materially |
| ~a third of the Brier is the day | Day-adjusted Brier within noise of the raw one |
| The inversion is a day artefact | A negative margin on HOLD buckets after adjustment |

All three are measured above, on the live record, and the third is the strongest: after
adjustment the HOLD buckets are within one point of zero margin.

## 6. When to delete this

The control is only meaningful with several days. On a day with **one** trading day of
evidence there is nothing to control, and the day-adjusted figure degenerates to the raw one.
If the day count ever falls to one, the report should say the comparison is unavailable
rather than silently reproduce the cross-day number — and if a future account accumulates
enough days that the margins are stable and non-zero, the raw comparison becomes
unnecessary and the adjusted one is the only one worth keeping.

## 7. Verification

- Unit tests on synthetic rows: buckets that differ only by day produce no inversion claim;
  a bucket that underperforms its own days produces a negative margin and says so.
- The live numbers reproduced through the module's own entry point, before and after.
- `make verify`, and `doctor` re-run so the message is read on a real account.

## 8. The question this leaves open

**Should `min_confidence` exist at all?** The gate is passed by every trade the model has
proposed (209 of 241 scored decisions cleared it, one refusal in 1179 cycles) and it is
bypassed entirely for HOLDs, which return approved before the check. So it is a threshold
that has barely bound — while the one stratum that is genuinely worse than its days is the
most confident trades. That is a separate experiment with a stated falsifier, and it is the
next question rather than part of this change.

**Answered later the same day: keep it at 0.5.** Its one firing was correct — a baseline SELL
the counterfactual scores as a `FALSE_TRADE` — and every setting above 0.5 blocks sets that
underperform their own days. The "one refusal in 1179 cycles" above turns out to be exactly
right; measuring only the LLM's own decisions finds the gate innocent, which is how a guard
gets misjudged. See `docs/history/plans/2026-10-03-min-confidence-what-it-is-worth.md`.

## 9. Result, measured against the live record

`minictrl doctor`, before and after, same account and same verdicts:

```
before: MIS-CALIBRATED: Brier 0.396 against the 0.25 a constant 0.5 claim scores, so the
        stated confidence is worse than useless. Accuracy is 53.1% and the most confident
        bucket is 13.9% (margin -39.2% if positive), which on a base rate that high cannot
        discriminate anything
after:  MIS-CALIBRATED: Brier 0.396 against the 0.25 a constant 0.5 claim scores, so the
        stated confidence adds no usable information. Brier 0.271 against the 4 day(s) those
        decisions were taken on, so 0.126 of it is the market's direction rather than the
        model's. Measured against the whole record rather than the days, accuracy is 53.1%
        and the most confident bucket is 13.9% (margin -39.2% if positive). The worst
        bucket against its own days is -19.0%
```

Buckets after the control, as the report now prints them:

```
  0.0-0.2   n= 32  acc=0.906  exp=0.903  margin=+0.003
  0.4-0.6   n=125  acc=0.680  exp=0.567  margin=+0.113
  0.6-0.8   n= 79  acc=0.139  exp=0.329  margin=-0.190
  0.8-1.0   n=  5  (below the 10 needed to judge)
```

One bug found while building it: the expectation was applied only when it was truthy, so a
day on which nothing was right — expectation exactly 0.0 — had its margin discarded. That is
the bucket that matters most on a bad day. Counted, not truthiness-tested.

`make verify`: 52 classes, 0 failed, 1395 test executions across 62 files; 886 tests pass;
ruff clean; `mypy src/min_agent` 0 findings. The daemon was restarted onto the new code.
