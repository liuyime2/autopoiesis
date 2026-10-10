# Close the audit gaps, starting with the measurement layer

Date: 2026-10-03
Status: **DONE for all four buildable items; two deliberately not built, which the plan itself predicted. Status line added 2026-10-05 - it had been empty for two days**
Baseline commit for rollback: `886c948`
Scope: five gaps from the 7x24/autonomy audit, ordered by dependency. Two of the five come
out as **do not build yet**, and that is a result, not a deferral.

---

## 0. The audit that started this, and what it got wrong

The audit told the user the model was miscalibrated (Brier 0.433 against 0.25) and needed
a self-healing calibration loop. **That diagnosis is contaminated by a bug, and gap 3b may
not exist at all.** The audit also reported a 90.3% HOLD rate as if low action were itself a
defect. Both need correcting before anything is built on them.

## 1. Baseline measurements (all re-runnable)

| # | Measurement | Value |
|---|---|---|
| B1 | Decision cycles | 1179 |
| B2 | HOLD share | 1065/1179 = 90.3% |
| B3 | Largest single HOLD cause | 347 "policy engine found no enabled admitted strategy" |
| B4 | Fail-closed HOLD on invalid model output | 135 |
| B5 | Cycles with a decision validation error | 147 = 12.5% |
| B6 | — of which `confidence` out of range / unparseable | 92 + 8 = 100 |
| B7 | — of which `HOLD` carried non-zero quantity | 28 |
| B8 | Counterfactual verdicts (latest, `scored`=607) | GOOD_HOLD 327, MISSED_ALPHA 195, **GOOD_TRADE 49**, FALSE_TRADE 27, NEUTRAL 9 |
| B9 | `calibration.INFORMATIVE` accepts | 549/607 — drops GOOD_TRADE (see B14 for the join-corrected figure) |
| B10 | `offline_validation.INFORMATIVE` accepts | 598/607 — the same set, plus GOOD_TRADE |
| B11 | `CalibrationRow.correct` | `verdict == GOOD_HOLD` only |
| B12 | Recorded `confidence` distribution | bimodal: 637 near 0, 542 near 1, **nothing between** |
| B13 | Gate | 49 classes, 0 failed, 1331 executions, 856 pytest |
| B14 | **Join-corrected** GOOD_TRADE impact | **8 decisions**, not 49 — see §2.1 |

## 2. P0 — Merge three drifted copies of the verdict set, and stop discarding winning trades

**Problem.** `calibration.py`, `counterfactual.py` and `offline_validation.py` each declare
the same verdict constants by hand. `offline_validation.py:68` includes `GOOD_TRADE` in
`INFORMATIVE`; `calibration.py:36` does not, and `calibration.py:29-32` does not define
`GOOD_TRADE` at all. So the verdict added by `7449ff3` ("give a winning trade a verdict")
stops at the counterfactual layer and never reaches calibration. **49 of 607 scored
decisions (8.1%) are dropped from calibration entirely.**

Compounding it, `CalibrationRow.correct` returns `verdict == GOOD_HOLD`, so even if they
were admitted, all 49 winning trades would score **incorrect**.

**Why this is first.** Every number the system reports about decision quality comes through
this function. Until it is right, the Brier score, the calibration buckets and the
MIS-CALIBRATED verdict are not evidence about the model — they are evidence about this bug.
Fixing anything else first means measuring on a broken ruler.

**This is a deletion, not an addition.** Three copies become one; nothing new is introduced.

### 2.1 The measured impact is much smaller than §2 first claimed, and the plan said otherwise

The 49 in B8/B9 is the counterfactual report's own `counts`, which covers rows that do not
all correspond to a journal cycle with a recorded confidence. Joining the counterfactual
verdicts to the cycles that actually recorded a confidence, on the live journal:

| | informative | correct | base rate | Brier |
|---|---|---|---|---|
| before | 497 | 287 | 0.5775 | **0.5399** |
| after | 505 | 295 | 0.5842 | **0.5342** |

**8 decisions, not 49**, and the Brier score moves by 0.0059. This correction matters more
than the fix does:

- The defect is real and worth removing — it is a silent filter on the evidence layer, and
  three copies of a verdict set is how it drifted in the first place.
- **It does not explain the MIS-CALIBRATED verdict.** Brier stays far above the 0.25 of a
  constant-0.5 claim, so the model really is miscalibrated, and gap 3b stands as a real gap
  rather than an artifact.
- The earlier claim that "the Brier score describes this bug rather than the model" was
  wrong. Recorded here rather than quietly dropped, because the whole point of measuring
  before designing is to catch exactly this.

- Success criterion: one authoritative verdict set; `GOOD_TRADE` is scored; `correct` is true
  for `GOOD_HOLD` and `GOOD_TRADE`; calibration admits 598/607; Brier is recomputed and the
  MIS-CALIBRATED verdict is restated against the corrected number, not the old one.
- Verify: `pytest tests/autopoiesis/test_calibration.py`; a new test that a `GOOD_TRADE` row
  scores as correct and is admitted as informative; `grep` proving one definition of the
  verdict set remains; `make verify`.

**Correction owed to the user, in STATUS.md and in the commit body:** "the model is
miscalibrated, Brier 0.433" is not established. It is an artifact until this lands.

## 3. P0 — The decision instruction is inline code, and it omits the confidence range

**Problem.** The entire instruction is a string literal at `llm_decision.py:100-113`. It
specifies the `action` enum, the `quantity` rule and the full `hold_reason` enumeration — and
never states that `confidence` must lie in `[0, 1]`. The JSON schema *does* carry
`minimum: 0, maximum: 1`, so the bound was available to constrained decoding and 92
violations still reached validation; the numeric range does not survive into the decoding
grammar, so the words are the only place it can be enforced.

Cost: **100 cycles lost to a malformed `confidence`, plus 135 fail-closed HOLDs on invalid
output — ~20% of the entire history**, and every one of them was a cycle the market was open
for.

**Why this is the self-representation gap.** The system has no surface representing its own
instructions: they live inside a method body, so they cannot be audited as data, diffed,
tested in isolation, or corrected without a code change and a daemon restart. This is the
observed need — a missing spec line with a measured 20% cost — and it does **not** justify
building a prompt-management subsystem.

**Minimal capability.** Extract the instruction to a single owned data constant, add the
missing `confidence` bound in words, and keep one authoritative copy. No prompt optimiser, no
auto-rewriting, no prompt DSL.

- Success criterion: the instruction exists once, outside a method body; it states the
  confidence range; a test asserts the instruction mentions every field constraint the
  validator enforces, so the two cannot drift again.
- Verify: new test; `make verify`; after Monday's open, measure the malformed-output rate
  against the 12.5% baseline.

## 4. ~~P1 — Nothing re-adjudicates a PAUSED strategy~~ — measured, and NOT built

**This section originally proposed re-adjudication. Measurement said do not build it.**

Two measurements, both against the 847 real cached bars rather than against an impression:

```
33 PAUSED strategies, over the real price range 758.79-772.65:
  7  ever actionable
  26 never actionable at any price in the range
```

The 26 are not a lifecycle problem at all. Their `reference_price` values are **100, 120,
150, 170, 180, 200** while SPY trades at **769.88** — placeholder anchors copied out of an
old prompt example, so their bands (98-102, 148.5-151.5, ...) are unreachable by
construction. `StrategyAdmission._reference_price_rejection_reason` **already refuses both
failure directions**, and its docstring records this exact defect ("nine strategies were
admitted with reference_price in {100, 120, 150, 170, 180, 200}"). These are pre-gate
residue; the gate is correct and no new ones can arrive.

So restoring PAUSED strategies means restoring 6 actionable ones. And that is where the
proposal inverts:

```
actionable at 769.88: 13  (7 PROBATION / 6 PAUSED)
probation queue is SERIAL - sorted(actionable, created_at)[0], one candidate per cycle
the promotion gate is PER CANDIDATE - 10 informative decisions ~= 13 dedicated cycles

drain today's queue:              7 x 13 =  91 cycles ~= 1.4 trading days
drain it with the 6 restored:   13 x 13 = 169 cycles ~= 2.6 trading days
```

**Restoring them makes the first promotion slower, not faster.** Adding candidates does not
accelerate any candidate past a gate that is counted per candidate and served from a serial
queue — it lengthens everyone's wait.

The lifecycle-rule work described below therefore stays undone, on purpose. It would have
been three coordinated changes to a safety-adjacent subsystem (plumbing price into `review`,
an actionability gate, and resetting evaluation records) bought at the cost of delaying the
one thing the project is actually waiting for. `StrategySpec.lifecycle_reason` landed
separately because it is a data-loss fix that stands on its own.

### What was originally proposed here, and why it is wrong



**Problem.** 56 strategies are PAUSED and there is no route back. Four have pause reasons
that have since expired. Meanwhile the single largest HOLD cause in the whole history is
**347 cycles where the policy engine found no enabled admitted strategy** (B3) — the library
had nothing it was allowed to trade.

**Minimal capability.** On each lifecycle pass, a PAUSED strategy whose recorded reason no
longer holds becomes eligible for the admission gate again. No resurrection, no bypass: it
re-enters as a candidate and must earn its way back.

- Success criterion: a PAUSED strategy with an expired reason is re-adjudicated rather than
  permanently skipped; one with a live reason is left alone.
- Verify: tests for both directions plus the never-resurrect case; `make verify`.

## 5. P1 — Rotation silently truncates everything recomputed from the journal

**Problem.** ~14 days of retention at 18.7 MB/day with four generations. Anything rebuilt
from the journal each cycle — calibration (314 decisions), the experiment chain (123
strategies), champion history — loses its older history **silently**, past that horizon.
No test pairs rotation with any of them.

**Minimal capability.** Make the truncation visible: when a computation reads a journal
whose oldest retained generation is newer than the window it needs, say so instead of
reporting a confident number computed from a fragment.

- Success criterion: a bounded reader states the window it actually covered; the doctor
  reports truncation rather than passing a fragment off as the whole.
- Verify: a test that a truncated journal produces an explicit truncated marker; `make verify`.

## 6. P2 — Do NOT build the off-hours learning loop yet

**The audit listed this as the top gap. The evidence does not support building it.**

The claim was "evolution only happens in the few hours the market is open". True. But the
binding constraint is **evidence, not compute**: 7697 of 10130 offline validations returned
`INCONCLUSIVE_INSUFFICIENT_EVIDENCE`, and no candidate has ever reached the promotion gate.
An off-hours loop would run more evaluations over the same insufficient evidence and produce
the same inconclusive verdicts, faster.

Adding it before the evidence exists is exactly the pre-built capability the objective
forbids. **Revisit when the P0/P1 fixes have produced candidates that actually reach the
gate** — then off-hours compute has something to chew on. Recording the reasoning here so
the next session does not re-derive it as an obvious gap.

## 7. P2 — Proving an edge cannot be done by writing code

87.7% of realised PnL is one exit at one price (23 of 35 lots, three strategies at once).
No strategy has a demonstrated repeatable edge, and none can acquire one from this
repository. The only honest deliverable is the path plus real, cross-session evidence.

- Success criterion: candidates reach the screen's ten-informative gate; at least one
  `PASS_SCREENED` decision is recorded from a candidate that earned it; the PnL record is
  attributable across more than one exit event.
- Verify: Monday's real cycles. **Synthetic data may not stand in for this** — it would
  violate the no-fabricated-data rule and prove nothing about behaviour.

## 8. Order and dependency

```
P0 measurement integrity   -> every later number depends on it
P0 instruction surface     -> fixes ~20% wasted cycles
P1 PAUSED re-adjudication  -> restores candidate supply (B3)
P1 truncation visibility   -> stops silent evidence loss
P2 off-hours loop          -> NOT until the gate is reachable
P2 prove an edge           -> real time only
```

## 9. What is explicitly not being built, and why

| Candidate capability | Why not |
|---|---|
| Prompt optimisation / auto-rewriting engine | No evidence prompts are the bottleneck for *edge*; the measured prompt defect was one missing line |
| A general prompt-management subsystem | A constant plus a test covers the observed need |
| Off-hours learning loop | Evidence-starved; would multiply inconclusive verdicts |
| Live trading | Forbidden until paper behaviour is audited and hard risk controls verified |
| Anything to "prove an edge" | Not a code problem |

## 10. Rollback

Each item is one commit touching one layer. Reverting a commit returns the tree to the
measured baseline; `886c948` is the pre-plan state.
---

## Status settled (2026-10-05)

This plan's `Status:` line was empty, which a sweep for unfinished plans flagged. Checking each
item against the commits rather than against memory:

| Item | Outcome |
| --- | --- |
| §2 P0 merge three drifted verdict copies, stop discarding winning trades | `e26b098` |
| §3 P0 the decision instruction is inline code and omits the confidence range | Done and verified |
| §4 P1 PAUSED re-adjudication | **Not built, as the plan itself concluded** - `c7ab775` |
| §5 P1 rotation silently truncates recomputation | Fixed in `b17ea05` |
| §6 P2 off-hours learning loop | **Not built, as the plan itself concluded** |
| §7 P2 proving an edge | **Not a code problem**, as the plan itself concluded |

So all four buildable items landed, and the two "do not build yet" items are still not built -
which is the plan working, not the plan stalling.

§3 is worth stating precisely because it was the plan's largest measured win and its verification
was never recorded here. The instruction is now `DECISION_INSTRUCTION` at module level in
`llm_decision.py:53`, it states the confidence bound in words
(`llm_decision.py:58`: "a decimal number between 0 and 1 inclusive"), and
`tests/autopoiesis/test_llm_decision.py` carries both halves of the demanded guard:
`test_the_decision_instruction_lives_outside_the_method_body` (the literal is not duplicated back
inside `decide`) and `test_the_instruction_states_every_constraint_the_validator_enforces`, which
is a correspondence test against `TradeDecision.model_json_schema()` so a schema change without
an instruction line now fails the build.

**Measured, 2026-10-05.** The rate is recoverable after all, from the untyped cycle records rather
than from an event type — 1,232 of them carry the full `snapshot`/`decision`/`guardian` shape, and
the malformed cases are visible in the decision itself:

```
LLM-authored cycles : 277   (2026-09-29 .. 2026-10-05)
  malformed confidence : 0  (0.00%)
  HOLD with quantity   : 0  (0.00%)
  combined             : 0  (0.00%)
```

against the plan's recorded baseline of 12.5% (92 malformed `confidence` values plus 135
fail-closed HOLDs). Every one of those cycles would appear in this count, so the 12.5% -> 0.00%
drop is a measurement and not an absence of evidence.

**One caveat that matters for reading it.** The 277 LLM cycles span only 2026-09-29 onward, so the
malformed era is *not* in this population — the journal's earlier cycles were authored by a
different model field or none at all. So this measures "no malformed output since the fix" and
cannot by itself show the rate *before* it. The 12.5% baseline came from the plan's own
contemporaneous reading of the same journal, so the comparison is like-for-like in time but not in
population. Stated rather than rounded up to "fixed".

The second reason this plan's status was empty: like the research-gate plan, it was written,
executed, and never revisited. Two plans in a row whose Status line outlived their work is
enough to justify the sweep that found them — reading `Status:` against the commit log, which is
what this session did.
