# A position cap the strategy cannot see is charged to it as its own fault

Date: 2026-10-05
Status: **DONE - the cap refusal is the system's state again; the incumbent is no longer retired for it**

Baseline commit for rollback: `906f104`
Scope: `src/min_agent/evaluator.py` and its tests. No change to the cap itself, the Guardian,
`INCUMBENT_SHARE`, the admission cap, or the probation budget.

---

## 0. The question

The cadence fix works: with the count advancing per cycle the incumbent is now selected on the
cycle where `served % 5 == 0`, which is the whole point of `3407ebc`. It produces a BUY, the
Guardian refuses it — correctly, the 17-share position is over the $5,000 cap — and nothing is
journalled.

So the question is what those refusals do to the strategy's own record. `evaluator.py` splits
Guardian refusals into the system's state and the strategy's fault, and the split decides whether
a rejection counts toward retirement:

```python
failure_rate = (result.errors + fault_rejections) / result.cycles
if failure_rate >= self.severe_failure_rate:   # 0.75
    return StrategyLifecycleDecision(strategy, "RETIRED", f"severe operational failure rate {failure_rate:.2f}")
```

## 1. The measurement

```
cap refusal classified as the strategy's own fault: True
severe_failure_rate = 0.75

   5 cycles, all refused on the cap -> failure_rate 1.00 -> RETIRED
  10 cycles, all refused on the cap -> failure_rate 1.00 -> RETIRED
  13 cycles, all refused on the cap -> failure_rate 1.00 -> RETIRED
```

The reason is `"this buy would leave 13854.96 SPY held against a max_position_value of
5000.00"`, and `is_system_rejection` returns `False` for it. The prefix list is explicit that
this is intentional:

> Deliberately *not* here: "position value exceeds hard limit", "decision confidence below
> minimum", "strategy position value exceeds hard limit". Those are the strategy asking for
> something its own specification makes impermissible, which is exactly what the fault counter is
> for.

That reasoning is right for a strategy whose *own* `max_position_value` its order breaches. It
does not hold here, and the distinction is the whole point:

- `tiny-fixed-size-001` declares `max_position_value=1000`. A single 1-share BUY is $769.72, well
  inside its own limit. The refusal is not "you asked for more than your specification permits".
- What breached the limit is a **17-share position that existed before this strategy was
  promoted**, accumulated while the rule still measured the order rather than the position. The
  strategy is being charged for a pre-existing account state it can neither see nor change, and
  its only correct response — decline to buy — is the very thing that would have avoided the
  refusal.

The same reasoning the prefix list already applies to `"the agent's own holding of ..."` applies
here: that prefix exists precisely because "the agent's own holding is derived from the journal
and the account's positions come from a broker snapshot; neither is a property of the strategy."
The position the cap is measured against is the account's, from a broker snapshot.

The consequence is that the library's **only** ACTIVE strategy accrues a failure rate of 1.00 and
is retired for it — leaving no ACTIVE strategy at all, which is the exact state `3407ebc` was
committed to end, reached by a different route.

## 2. The fix

One prefix in `SYSTEM_REJECTION_REASON_PREFIXES`:

```python
"this buy would leave ",
```

Matched on the stable literal the Guardian emits, consistent with how every other entry in the
list works, and a test pins the literal so a reword cannot silently reclassify it.

The wording matters and is why this is a prefix and not the whole message: the same template
covers a cap genuinely set by the strategy's own specification. The distinction there is already
available and is left alone — a strategy that asks for more than its own `max_position_value`
still has the order sized against *its* limit by the Guardian, and the aggregate cap check
(separately prefixed `"agent exposure "` / `"account total "`) already covers the account-wide
case. This prefix only reclassifies the position-bracketing refusal, which is measured against
account state in every case.

## 3. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The cap refusal is charged to the strategy | `is_system_rejection` already returning `True` for it |
| A strategy's own over-limit order is still its fault | any test that relied on the position-cap message counting as a fault |
| Retiring is the live consequence | the reflection showing `strategy_fault_rejections` staying 0 for the incumbent |
| No risk limit moved | any diff to `max_position_value`, the caps, or the Guardian |

## 4. Verification

- A test that the position-cap message is a system rejection, and that the existing
  fault-charged messages still are not. The second half is the one that stops this from
  becoming a blanket amnesty for risk refusals.
- `make verify` - 52/52 classes, 0 failed.
- `make restart`, then confirm over the next probation window that the incumbent's
  `strategy_fault_rejections` stays 0 while it is refused on the cap, and that it is still
  ACTIVE.

## 5. Explicitly not in scope

- Reducing the position, raising the cap, or liquidating anything. The account holds 17 SPY
  against a $5,000 limit and the honest reading is that this account is over its own risk
  configuration; the system refusing to add is the limit working. Whether to reduce the position
  is an operator decision about the account, not something to fix in code.
- Any change to the Guardian, `INCUMBENT_SHARE`, the admission cap, or the probation budget.

---

## Result

One prefix added to `SYSTEM_REJECTION_REASON_PREFIXES` in `src/min_agent/evaluator.py`:

```python
"this buy would leave ",
```

Gate: **52 classes, 0 failed, 1483 test executions across 63 files**, 934 pytest, ruff and mypy
clean. Two new tests:

- the position-cap message is a system rejection, added to the existing parametrised list so it
  sits beside the sibling prefix cases rather than in a test of its own;
- `test_being_refused_on_the_position_cap_does_not_retire_the_incumbent` — 13 cycles all refused
  on the cap, `failure_rate` would be 1.00 against a 0.75 threshold, and `review` returns no
  decision. That is the live consequence stated as an assertion, so a future reword of the
  Guardian message cannot quietly restore the retirement.

`test_a_genuine_strategy_fault_is_still_charged` is unchanged and still passes, so this is not a
blanket amnesty for risk refusals: a strategy asking for more than its own specification permits
is still charged.

## What this does not do

It does not make BUY capacity return. The account still holds 17 SPY against a $5,000 cap, the
Guardian still refuses every buy, and the incumbent is still selected once in five cycles and
refused once in five cycles — correctly, and now without accumulating a fault for it.

The position is an account-level fact and reducing it is an operator decision, not a code change.
The system's behaviour under it is now correct: it declines to add, and it does not punish the
strategy for declining.
