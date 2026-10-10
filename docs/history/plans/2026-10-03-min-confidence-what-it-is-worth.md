# What `min_confidence` is worth: one refusal, and it was right

Date: 2026-10-03
Status: **DONE - measured; the gate is kept at 0.5 and its measured value disclosed**
Baseline commit for rollback: `5f434aa`
Scope: `configs/paper.env.example`, the docstring in `src/autopoiesis/calibration.py` that
says the gate decides whether an order may be sent, and `STATUS.md`. **No change to the
Guardian, no new test.**

---

## 0. The question, and a correction I had to make to my own answer

The open question carried from the previous entry was: *"Should `min_confidence` exist at
all? 209 of 241 scored decisions clear it, one refusal in 1179 cycles."* It is a
risk-adjacent knob, so the question is not whether it looks wrong but **what it has done, and
what any other setting would do.** Both are answerable from the journal and the
counterfactual verdicts the system already computes.

Measuring it a first time, restricted to the LLM's decisions, produced a clean and wrong
answer: *the gate has never refused anything, because the model's lowest stated trade
confidence is exactly 0.5.* That was true of the 314 LLM decisions and **false of the
system.** The 1179 cycles contain three decision sources, and the gate had in fact fired —
once. The scoping error was mine, and it is worth recording because the correction went
against the conclusion I was building, not for it.

## 1. What the gate actually sees

`Guardian.review` approves a HOLD before the confidence check, so the population is every
non-HOLD decision, from every source:

```
cycle records: 1179
  decision_source: baseline 852 | llm 314 | fallback_policy_engine 13

trades (the only decisions the gate can see): 114
  llm 56 | baseline 51 | fallback_policy_engine 7

refused by the confidence gate, any source: 1
  2026-06-09T12:40:03  SELL 54  confidence 0.30  source baseline
  -> counterfactual verdict: FALSE_TRADE
```

**The gate's one and only firing was correct.** It blocked a baseline SELL that the
counterfactual ledger scores as a `FALSE_TRADE` — 0% correct on a day whose base rate for
scored decisions was 22.2%. It removed a decision 22 points worse than its own day.

So the previous entry's "one refusal in 1179 cycles" was **exactly right**, and my first
measurement was the thing that was wrong. The gate is not dead code that has never run.

## 2. Why it looks inert, and that is a fact about the model

Among the LLM's 314 decisions — 56 trades — the gate has refused **none**:

```
LLM trades: 56
  below 0.5: 0    at or above: 56
  confidences seen: [0.5, 0.55, 0.6, 0.65, 0.7, 0.75]
```

The model's lowest stated trade confidence is exactly the threshold, so there is no
population on which it could bind. That is a property of **this model's behaviour
distribution**, not evidence about the gate. A different model, or this one under
distribution shift, states lower confidences — which is precisely what the one historical
firing was: a 0.30-confidence decision from a non-LLM source.

## 3. What every other setting would do

All 114 trades, 76 of them scored against the horizon, each blocked set compared against the
base rate of the days it appears on — the comparison that survives the finding that
correctness here is mostly the trading day's:

| threshold | blocks | blocked right | blocked wrong | day-adjusted gain |
|---|---|---|---|---|
| 0.00 | 0 | 0 | 0 | +0.0% |
| 0.30 | 0 | 0 | 0 | +0.0% |
| **0.50 (current)** | **1** | **0** | **1** | **−22.2%** |
| 0.55 | 1 | 0 | 1 | −22.2% |
| 0.60 | 7 | 3 | 4 | −4.3% |
| 0.65 | 37 | 24 | 13 | −2.3% |
| 0.70 | 55 | 39 | 16 | −3.8% |
| 0.75 | 73 | 48 | 25 | −0.5% |
| 0.80 | 73 | 48 | 25 | −0.5% |

**Every setting above the current one blocks sets that underperform their own days.** The
current setting is the only one that blocks anything at all, and what it blocks was wrong.
Raising it trades a 22-point-underperforming decision for sets that are 4 to 0.5 points
underperforming — smaller losses, and enormously more of them.

## 4. What this supports, and what it does not

**Supported:** keep the gate at 0.5. It has a 1-for-1 record of firing, and that firing was
correct. Raising it is measured-harmful, and the reason is specific — not "a guard is
prudence", but a table.

**Not supported, and deliberately not acted on:** anything about the LLM's calibration,
which the same measurement says is worth nothing on the trades it can see — 31/45 = 68.9%
correct against a 68.9% day expectation, margin −0.0%. That is a finding about the model,
recorded by `calibration.py`, and it is not a reason to touch a risk default.

The evidence does demand one thing: nobody should turn this knob on the strength of it
looking like an inert filter. So the measurement went where an operator would look first.

## 5. The minimal change

Two sentences. No new field, no test, no behaviour change: the claim is about runtime state,
not about a code path, and a fixture would assert nothing the journal does not.

1. `configs/paper.env.example` — the file an operator edits — now states what the gate has
   done and what raising it costs.
2. `calibration.py`'s docstring says the gate has one firing, that it was correct, and that
   the LLM's own decisions never reach it, instead of implying a filter with no record.

## 6. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| The gate fires rarely and correctly | A firing that blocks a `GOOD_TRADE` |
| The LLM's confidences sit at the threshold | An LLM trade below 0.5 appearing in the journal |
| Raising it is harmful | A threshold above 0.5 whose blocked set beats its own days |
| The LLM's confidence carries no information | A non-zero day-adjusted margin splitting LLM trades at any cut |

All four are checkable against the live record on any future run, which is what makes them
worth writing down. The first three are the ones that would make this entry wrong; the
fourth is about the model and is tracked by `calibration.py`.

## 7. Result

Landed: the value in `configs/paper.env.example` carries the measurement and the cost of
raising it, and `calibration.py`'s docstring carries the gate's actual record. No behaviour
changed, so the gate is green before and after.

The part worth keeping from this round is not the conclusion — it is that the first
measurement was scoped to the LLM and produced a confident, clean, wrong answer, and that
the previous entry's rougher number survived the check. A scoped-to-the-new-thing
measurement will always find the new thing innocent; the 852 baseline and 13 fallback
decisions sitting in the same journal under the same threshold are what gave the gate its
one real use.
