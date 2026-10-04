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

**Zero strategies have ever been promoted.** Not one, in 1179 cycles. Every candidate the
loop admits ends PAUSED, and 24 of the 34 pauses share one reason: *"probation produced no
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
consequence: **12 of the 17 paused strategies produced zero trades**, at 5 to 9 cycles each,
while the daemon ran on for 65 to 1179 more cycles in the same window.

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

Two measurements change that arithmetic:

- **There is no ACTIVE strategy.** All 17 tracked strategies are PAUSED. The incumbent the
  cost was measured against does not exist, so the cost of a deeper probation is currently
  paid by nothing.
- **The total spend is small.** The 12 strategies that hit this pause ran 5-9 cycles each;
  taking them to 13 adds roughly 60 cycles across the entire 1179-cycle history, about 5%.

So the change buys the only capability that has ever been missing — a candidate that can
reach its evidence gate — for about 5% of throughput and no incumbent, against a measured
return of **zero promotions ever**.

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
strategies paused for "probation produced no exploration evidence": 12
  their max decisions ever: 5 5 5 5 6 6 7 7 7 7 8 9    <- every one below the gate of 10
strategies that reached >=10 scored decisions: 12
  of those, paused for no-exploration: 0                 <- none
```

Every strategy the rule ever paused had fewer decisions than the evidence gate requires, and
no strategy with enough evidence was ever paused by it. The rule could not have done anything
else: it fired on exactly the population it was structurally incapable of judging, and never
on the population it was written for.

**Not closed by this change.** It removes the reason promotion was unreachable; it does not
show a promotion. And the same sweep surfaced the next question rather than answering it: of
the 12 strategies that did reach 10+ scored decisions, **9 were never judged at all** — the
screen ran, the evidence was sufficient, and no lifecycle verdict was ever recorded. That is
a separate defect with a separate baseline, and it is where the next measurement goes.
