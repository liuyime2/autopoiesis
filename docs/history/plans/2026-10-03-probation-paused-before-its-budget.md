# The loop pauses every candidate at cycle 5, before the 13 cycles it is guaranteed

Date: 2026-10-03
Status: **DONE - the 5/13 split is closed; whether a candidate now clears the screen is a
live measurement, not a claim this document can close**
Baseline commit for rollback: `0f141dd`
Scope: `src/min_agent/strategy_engine.py` and its tests. No config, no new module, no new event.

---

## 0. The question

The objective's core mission is a self-evolving agent that improves its own strategies. The
loop cannot currently do that, and the reason is measurable without any new infrastructure:

> **Corrected 2026-10-03 — read this block last.** The figures below were taken by scanning
> `journal.jsonl` alone. The journal is rotated, so they cover roughly half the record. The
> corrected figures are at the end of this document and in STATUS.md; the conclusion survives
> but three of these numbers do not.

```
78 strategies proposed, 24 admitted, 107 with evaluation records
6485 offline validations across 107 strategies, 60.6 evaluations each
  INCONCLUSIVE_INSUFFICIENT_EVIDENCE  5280  (81%)
  PASS_SCREENED                         715
  REJECT_POOR_DECISIONS                 490
strategies that ever reached 10+ scored decisions: 12 of 107
strategies that ever placed a trade:              11 of 78
final lifecycle state of all 17 tracked strategies: PAUSED 17, ACTIVE 0
```

**Promotion is close to absent, not absent.** *(corrected: the original claim here was "zero
promotions ever", which was an artifact of reading one journal file — see the note above.)*
Eight transitions to `ACTIVE` are on record across both files, one of them in the current
two-phase format: `tiny-fixed-size-001`, 2026-09-30, on broker-verified realized PnL. The most
recent promotion of any kind is 2026-09-30 and none has happened since. Of 92 transitions, 46
are `PROBATION -> PAUSED`, and 36 of those share one reason: *"probation produced no
exploration evidence."*

## 1. Root cause: two numbers for one concept

A candidate on probation is **guaranteed 13 selected cycles** before the queue moves on:

```python
# StrategySelector
min_probation_cycles: int = 13          # _needs_probation: cycles < 13
```

The promotion bar is **10 informative decisions**, and the docstring justifies 13 as the
budget that reaches it: a FIXED_SIZE produces informative decisions at an observed 0.75-0.91
per selected cycle, so 11-13 cycles.

The probation-stage *verdict*, however, fires at **5**:

```python
# StrategyLifecycleManager
min_active_cycles: int = 5
if strategy.lifecycle == "PROBATION" and result.cycles >= self.min_active_cycles:
    if self._is_degenerate_no_exploration(strategy, result):
        return PAUSED, "probation produced no exploration evidence"
```

and `_is_degenerate_no_exploration` is `cycles >= min_active_cycles and trade_attempts == 0`.

So a candidate that has not attempted a trade is declared degenerate at cycle 5 and never
reaches its 13-cycle budget, let alone the 10-decision evidence gate. The measured
consequence, on the full record: **17 of the 18 strategies this pause ever hit accumulated
fewer than 10 decisions ever** — 5,5,5,5,5,5,6,6,6,6,7,7,7,7,7,8,9 — so they could not have
been screened even if the pause had not fired. The daemon ran on for dozens more cycles in
each of those windows.

The probation budget and the probation-stage verdict are the same stage. They were set in
different classes, at different times, and never reconciled.

## 2. Why the fix is not "give probation more cycles"

The tempting change is to raise `min_active_cycles`. That is wrong twice over: it would also
delay the *promoted*-strategy path at line 306, where 5 cycles of an ACTIVE strategy not
acting is a genuine problem, and it would leave the two numbers free to drift apart again.

The invariant is narrower and is the one worth encoding: **a candidate cannot be judged
degenerate before it has had the cycles it was guaranteed.** Required cycles are therefore
the probation budget while a strategy is on probation, and `min_active_cycles` everywhere
else.

## 3. The change

1. One module constant `PROBATION_CYCLES = 13`, carrying the reasoning already in the
   selector's docstring, used as the default by both classes. This deletes the duplicate
   literal rather than adding a third place for it to live.
2. `_is_degenerate_no_exploration` requires `min_probation_cycles` for a `PROBATION`
   strategy and `min_active_cycles` otherwise. The invariant lives in the one function that
   already encodes the rule.

Not done, deliberately: no new lifecycle event, no config knob, no change to the promotion
gate, the rejection rates, or the PnL-verified promotion path. Those are not what is broken.

## 4. The cost, measured rather than assumed

The selector's docstring justifies the original 3-cycle budget by a real cost, and I am not
going to pretend it does not apply:

> *"At 13, five tradeable candidates x 13 is 65 cycles - about one trading day at the observed
> 66 market-open cycles per day - during which the one ACTIVE strategy trades nothing."*

Two measurements change that arithmetic — and both are stronger than the argument they replace:

- **Probation already consumes every cycle.** The incumbent exists — `tiny-fixed-size-001`,
  promoted 2026-09-30 — and in the 146 cycles since its promotion it was **selected 0 times**,
  because with 18 PROBATION candidates the queue never empties. So the ACTIVE strategy is
  already getting nothing, and lengthening a candidate's budget from 5 to 13 spends cycles
  that were not going to the incumbent in the first place.
- **The total spend is small.** The 17 strategies that hit this pause starved ran 5-9 cycles
  each; taking them to 13 adds roughly 60 cycles across the entire history, about 5%.

So the change buys the capability that decides the whole loop — a candidate that can reach its
evidence gate — for cycles probation was already spending, against a record in which
promotion has happened **once** in the current format and not at all since 2026-09-30.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| Candidates are paused at cycle 5, before their 13-cycle budget | A paused candidate with 0 trades at 13+ cycles |
| Extending probation lets candidates reach the evidence gate | Still 0 strategies reaching 10+ scored decisions after the change |
| The degenerate rule is what stops them | The pause reason moves elsewhere while 0-trade candidates still die early |
| It is not costing an incumbent | An ACTIVE strategy losing cycles to probation while it has nothing to prove |

The second row is the one that decides whether this worked, and it is measurable from the
journal after the change has run — not from a test. A test can pin the invariant; only the
live record can show the promotion.

## 6. Tests

Three, all narrow:

- a `PROBATION` candidate with 0 trade attempts at 12 cycles is **not** paused;
- the same candidate at 13 cycles **is** paused, with the existing reason;
- a promoted (`ACTIVE`) strategy that stops acting is still paused at `min_active_cycles`,
  so this cannot be read as "raise the bar everywhere".

## 7. Verification

- `ruff`, `mypy`, the governance and lifecycle tests, then the full `make verify`.
- The live claim — that a candidate now survives to its evidence gate — is not provable by
  any test in this repository, and is recorded as a prediction with a measurement to make
  after the daemon has run.

## 8. Result

`PROBATION_CYCLES` is one constant, both classes default to it, and the degenerate guard
requires the probation budget while a strategy is on probation. `tools/audit_defects.py`'s D4
check now forbids the escape rather than the word, and three tests pin the behaviour that
check was proxying for.

The measurement that made the case is sharper than the plan predicted. It is not only that
candidates were paused at 5 of 13 cycles:

```
strategies paused for "probation produced no exploration evidence": 18
  their max decisions ever: 5 5 5 5 5 5 6 6 6 6 7 7 7 7 7 8 9 43
                                             ^ 17 of 18 never reached the gate of 10
strategies that reached >=10 scored decisions: 12
  of those, paused for no-exploration: 1
```

Seventeen of the eighteen had fewer decisions than the evidence gate requires, so the rule
fired almost entirely on the population it was structurally incapable of judging. The
eighteenth is the case the previous version of this document denied existed: 43 decisions and
43 scored, every one a HOLD, zero trade attempts. The offline screen passed it and the
degenerate rule paused it — which is consistent, because `trade_attempts == 0` is what "no
exploration evidence" means, and 43 scored HOLDs are not exploration. So the rule has one
honest firing here and seventeen starved ones.

**Not closed by this change.** It removes the reason promotion was unreachable; it does not
show a promotion. And the same sweep surfaced the next question rather than answering it: of
the 12 strategies that did reach 10+ scored decisions, **9 were never judged at all** — the
screen ran, the evidence was sufficient, and no lifecycle verdict was ever recorded. That is
a separate defect with a separate baseline, and it is where the next measurement goes.

## 9. Correction: this plan read half the journal

Measured by scanning `runtime/min_agent/journal.jsonl`, which is **rotated** —
`journal.jsonl.1` holds another 10,587 events. `JsonlJournal.read_all()` spans both and is
the reader to use. Corrected figures:

```
lifecycle transitions: 92 across 45 strategies      (this plan said 34 across 17)
admission events:     208 across 142 strategies      (said 84 proposals, 24 admitted)
strategies evaluated: 127                           (said 107)
last promotion of any kind: 2026-09-30               (said none, ever)
```

Three claims in this document were false and are corrected in place: that no strategy had
ever been promoted, that the pause's victims all lacked evidence, and that there was no
ACTIVE strategy. The change itself stands, and stands on a cleaner measurement than the one
it was planned from — **17 of the 18 strategies the pause ever hit had fewer than 10
decisions**, against a gate of 10, and probation already consumes every cycle while the one
ACTIVE strategy has gone unselected for all 146 cycles since its promotion.

The lesson is recorded in STATUS.md and belongs to this document too: a number measured from
the journal must say which file it came from, because "one file" is the path of least
resistance and it silently halves the evidence.
