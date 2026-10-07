# A candidate that spent its budget without trading is stranded in PROBATION forever

Date: 2026-10-06
Status: **DONE - the stranded candidate returns to probation with its budget restarted**
Baseline commit for rollback: `2add047`
Scope: `src/min_agent/strategy_engine.py` and its tests. No change to the Guardian, any risk
limit, `PROBATION_CYCLES`, `min_promotion_scored_decisions`, or the promotion evidence bar.

---

## 0. The question

Fixing the capability count (`_uncovered_capabilities` no longer counting PAUSED strategies) was
necessary but not sufficient. On the live session the sole SELL route is still never served:

```
09:30:00  tiny-fixed-size-001        BUY   refused  this buy would leave 14006.16 SPY held
09:37-10:30  (9 cycles)  trend-follow-*  HOLD
```

and with the real reflection metrics, `select` at the cycle where the SELL route should win:

```
served=4 那一轮应选谁（用真实 metrics）:
  -> trend-follow-20260724-001 (PROBATION)
```

The strategy is now correctly identified as the single SELL route, and is still not served.

## 1. The stranding

```
fixed-size-sell-20260724-001
  lifecycle               PROBATION
  cumulative_cycles       15      (probation budget is 13)
  submitted_orders        0
  trade_attempts          8
  score                   0.9
  pnl_evidence            missing_fill_price_and_broker_activity

lifecycle 判定结果: 无判定（return None）
```

Four facts compose into a deadlock:

1. `cumulative_cycles=15 >= 13`, so `_needs_probation` is `False` — it **leaves the probation
   queue**. The selector serves that queue first, so it is never a candidate.
2. `submitted_orders = 0`, so `StrategyLifecycleManager.review` hits
   `if result.submitted_orders <= 0: return None` (`strategy_engine.py:325`) — no promotion, no
   pause, no retirement. **It is not eligible to be ACTIVE.**
3. Its lifecycle stays `PROBATION`, and the selector's `eligible` filter is
   `lifecycle not in {"PAUSED", "RETIRED"}` — so it *would* pass that filter, but it is not in
   the probation queue it serves from.
4. Its 8 trade attempts produced no submitted order because every one was a shadow intent while
   `MIN_AGENT_SHADOW=1`, and the eight pre-shadow attempts were refused by the position cap.

So the strategy is **budget-spent, evidence-less, and unreviewable**. It cannot be served, cannot
be promoted, and cannot be retired. `review` returning `None` is the correct answer to "should this
strategy change state?" given no evidence — the defect is that nothing ever *gathers* that
evidence, because serving it is the only way to.

**The trap is self-sealing.** Its 15 cycles were all spent while it was dormant or its SELL was
shadowed/capped. Restoring service would let it trade, but it is already out of the queue, so
service cannot resume. And it holds the only SELL route, so the 17-share position can never be
reduced by the system.

## 2. Scope of the measurement

```
PROBATION 且累计>=13 但 submitted_orders=0: 1
     fixed-size-sell-20260724-001     cum=15 submitted=0
```

Exactly one strategy, and it happens to be the one that matters. The other 18 PROBATION candidates
have `cumulative_cycles` between 0 and 7 and are in the queue normally.

## 3. The fix

A candidate that has spent its probation budget without producing a verifiable outcome must be
sent back to probation rather than left in limbo. In `StrategyLifecycleManager.review`, where the
budget-spent branch currently falls through when `submitted_orders <= 0`:

```python
if result.submitted_orders <= 0:
    return None
```

becomes a return to PROBATION with a reason, so the queue picks it up again:

```python
if result.submitted_orders <= 0:
    return StrategyLifecycleDecision(
        strategy,
        "PROBATION",
        (
            f"served its full {result.cumulative_cycles}-cycle probation budget "
            f"without submitting an order, so it has no outcome to judge and goes "
            f"back to probation to be served again"
        ),
    )
```

Why this and not the alternatives:

- **Not "promote it anyway."** The code explicitly refuses that, and correctly: a strategy that
  never opened a lot has no evidence of any kind, and promoting it on "it did not crash" is what
  the fallback used to do. Standing constraint: no automatic weakening of evidence bars.
- **Not "retire it."** It has not failed. It has not been tried. Retiring on a non-event is the
  same error as promoting on one.
- **Not "exclude spent-budget candidates from the queue count."** That is the `b86990b` shape again
  and it would hide the stranding rather than fix it.

`return None` remains correct for every other case; only this one changes.

## 4. What this will do on the live library

`fixed-size-sell-20260724-001` returns to the queue. It is the single SELL route, so it is served
first by `_uncovered_capabilities`. With execution enabled it can now actually submit, and the
arithmetic already computed applies:

```
17 股 × $774.74 = $13,170.58   上限 $5,000   ->  须卖至 6 股, 即 11 笔
max_trades_per_day = 10, 今日已用 0   ->  本次会话最多 10 笔，第二次会话收尾
```

The strategy's `trade_attempts=8` will become real submissions, which is what supplies the PnL
evidence its promotion currently lacks.

## 5. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| A spent-budget candidate with no orders is stranded | `review` returning a decision for that shape |
| The stranding is real, not hypothetical | more than one strategy in that state on the live library |
| It returns to the queue | `_needs_probation` still false after the transition |
| Promotion is not weakened | a strategy with no orders reaching ACTIVE |
| Nothing else moved | any diff to the Guardian, `PROBATION_CYCLES`, or the evidence gate |

## 6. Verification

- Test: a PROBATION strategy with `cumulative_cycles >= budget` and `submitted_orders == 0` gets a
  decision to `PROBATION` carrying the reason, rather than `None`.
- Test: after that decision the candidate is back in `_needs_probation`'s population — that is,
  the queue can serve it again.
- Test: a spent-budget candidate **with** submitted orders keeps its current behaviour (promotion
  on verified PnL, or the existing gates).
- Test: a strategy with no orders still cannot reach ACTIVE — the bar is untouched.
- Test: on the live shape, `review` produces a decision and the PAUSED/RETIRED strategies are
  unaffected.
- `make verify` — 52/52 classes, 0 failed.
- Live: `fixed-size-sell-20260724-001` appears in the cycle log, a real SELL reaches the broker,
  and `_agent_holding("SPY")` drops below 17.

## 7. Explicitly not in scope

- Changing `PROBATION_CYCLES` or `min_promotion_scored_decisions`.
- Any Guardian or risk-limit change.
- Reversing any pause, and widening the rule space, which remains the three-layer change recorded
  in `2026-10-06-paper-audit-and-enable-execution.md`.
---

## Result

Two changes, and **the first version of this fix was incomplete** — which is worth recording
because the gap was only visible by running the real numbers.

**Returning to PROBATION is not enough on its own.** `cumulative_cycles` is journal-derived and
monotone, so a candidate with 15 cycles against a 13 budget read as budget-spent *after* the
transition too. Verified immediately:

```
判定     : PROBATION -> PROBATION
应用后 needs_probation = False     <- 修好了状态，但服务队列仍然跳过它
```

The candidate would have been transitioned back and still unserved — the fix producing exactly
the stranding it was meant to remove. Each return therefore grants one further budget through a
new `probation_restarts` field on `StrategySpec`, carried on `StrategyLifecycleDecision.update`
and written by the daemon alongside the lifecycle:

```
判定     : PROBATION -> PROBATION
携带状态 : {'probation_restarts': 1}
应用后 needs_probation = True      <- 这次是真的回到可服务状态
重试次数 = 1

  served=0 -> tiny-fixed-size-001 (ACTIVE)
  served=4 -> fixed-size-sell-20260724-001 (PROBATION)     <- 唯一 SELL 路线获得服务
  served=8 -> fixed-size-sell-20260724-001 (PROBATION)
```

Not unlimited: a second restart is needed before 26 cycles would qualify, and it grants one
further budget. The field counts retries, not an attempt limit — what to do with a strategy that
has had several chances remains with the screen and the promotion gate, which judge outcomes.

### One gate check corrected, and why

`self-evolution-closes` went red: *"a ruling carries no evidence"*. The check requires every
ruling to cite scored decisions and a correct-outcome ratio, which is right for a ruling that
retires a strategy on counterfactual evidence. The new `PROBATION -> PROBATION` transition is not
such a ruling — it changes no state, and it rests on cumulative cycles and submitted orders. The
check now skips same-state transitions. Narrowing it to transitions that change the lifecycle is
the original intent ("selection pressure acted on the scored evidence"), not a weakening.

Gate: **52 classes, 0 failed, 1519 test executions across 64 files**, 957 pytest, ruff and mypy
clean. Nine new tests across the two plans, including one asserting the daemon writes
`decision.update` — a lifecycle label without its state is the exact failure measured above.

### What remains to be observed

The position is still 17 shares because the fix landed at 11:20 EDT and the strategy needs the
next lifecycle pass to be transitioned, then a cycle to be served, then a fill to be reconciled.
The arithmetic is unchanged: 11 sells to reach 6 shares, at most 10 per session under
`max_trades_per_day`.
