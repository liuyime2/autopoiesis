# The single-route SELL capability is served by nothing, because paused strategies count as routes

Date: 2026-10-06
Status: **PLAN - measured on the first live session after enabling execution**
Baseline commit for rollback: `564a946`
Scope: `src/autopoiesis/strategy_engine.py` and its tests. No change to the Guardian, any risk
limit, the strategy library, the lifecycle rules, or the admission gate.

---

## 0. The question

Execution was enabled at 21:19 EDT on 2026-10-05. The first live session opened at 09:30 EDT and
ran ten cycles. One BUY was attempted and correctly refused by the position cap. The other nine
were HOLDs. **The position did not move, and the reason is not that nothing could sell it.**

Three strategies can express a SELL at the current price, and exactly one of them is servable:

```
声明 SELL 的 5 个策略，按 lifecycle 分组
   fixed-size-sell-001              RETIRED    可被服务=False
   fixed-size-sell-005              PAUSED     可被服务=False
   fixed-size-sell-006              PAUSED     可被服务=False
   fixed-size-sell-20260724-001     PROBATION  可被服务=True   <-- 唯一
   trend-follow-sell-002            PAUSED     可被服务=False

可被服务且声明 SELL 的: 1
```

And it was selected zero times in ten cycles. The whole ten:

```
09:30:00  tiny-fixed-size-001        BUY   refused  this buy would leave 14006.16 SPY held
09:37:43  trend-follow-20260714-001  HOLD
09:44:46  trend-follow-20260727-001  HOLD
09:50:48  trend-follow-20260724-001  HOLD
09:57:33  trend-follow-20260724-001  HOLD
10:03:56  tiny-fixed-size-001        HOLD
10:09:42  trend-follow-20260724-001  HOLD
10:17:01  trend-follow-20260724-001  HOLD
10:23:43  trend-follow-20260724-001  HOLD
10:30:16  trend-follow-20260727-001  HOLD
```

## 1. The mechanism, and why it exists

`StrategySelector` has a rule for exactly this situation, added in `e7bb23f` with the comment:

> Curriculum builds a SELL strategy precisely because the library could not sell, and then a strict
> oldest-first queue defers exercising it behind five near-duplicate BUY strategies... **A
> capability the library is otherwise missing has no other route to being tried, so it is served
> first**, and only then does the queue return to oldest-first.

`_uncovered_capabilities` decides which actions are missing:

```python
counts: dict[str, int] = {"BUY": 0, "SELL": 0}
for strategy in strategies:
    for action in cls._declared_actions(strategy, last_price):
        counts[action] = counts.get(action, 0) + 1
return {action for action, count in counts.items() if count <= 1}
```

**`strategies` is the whole library, unfiltered by lifecycle.** Measured:

```
各动作的可表达策略数: {'BUY': 27, 'SELL': 5}
未被覆盖的能力: 无
_supplies_capability(fixed-size-sell-20260724-001) = False
```

Four of those five SELL routes are `PAUSED` or `RETIRED` — `fixed-size-sell-005` and
`fixed-size-sell-006` paused on 2026-10-02 for "error rate above lifecycle threshold",
`trend-follow-sell-002` on 2026-09-29, `fixed-size-sell-001` retired on 2026-09-30. The selector
cannot serve any of them; it filters `lifecycle not in {"PAUSED", "RETIRED"}` in `select`. They
are counted as *routes* for a capability the system cannot actually express.

So `SELL` reads as covered by five routes when it has exactly one, and the one route is never
preferred over the older HOLD-only candidates.

This is the same shape as the defect fixed in `b86990b`: **one predicate, applied to a different
population on each side.** There, `probation_backlog` counted candidates the selector would never
serve. Here, `_uncovered_capabilities` counts strategies the selector will never serve.

## 2. The fix

Filter by lifecycle inside `_uncovered_capabilities`, using the same predicate `select` uses:

```python
counts: dict[str, int] = {"BUY": 0, "SELL": 0}
for strategy in strategies:
    if not strategy.enabled or strategy.lifecycle in {"PAUSED", "RETIRED"}:
        continue
    for action in cls._declared_actions(strategy, last_price):
        counts[action] = counts.get(action, 0) + 1
```

Two things this deliberately does **not** do:

- **It does not widen what a capability means.** A capability is still "this library can emit this
  action, through a strategy that could actually be served". That is a truer definition of the
  rule's own intent than "some file on disk mentions the action".
- **It does not resurrect anything.** No lifecycle changes. `fixed-size-sell-005` and `-006` stay
  PAUSED, and if their pause is ever reversed they rejoin the count.

Note that `select` also applies `_needs_probation`, so a servable strategy that has finished its
budget still leaves the queue. Should the count filter on that too? **No** — a strategy past its
probation budget is legitimately on its way to ACTIVE and remains a real route. The defect is
specifically about lifecycles that cannot be served at all.

## 3. The consequence, stated before the fix

With `SELL` correctly read as a single route, `fixed-size-sell-20260724-001` is served first, one
share per cycle. The arithmetic already done for this position:

```
17 股 × $774.74 = $13,170.58   上限 $5,000   ->  须卖至 6 股, 即 11 笔
max_trades_per_day = 10, 今日已用 0   ->  本次会话最多 10 笔
```

So the position returns inside the cap on the second session — the same path computed before
execution was enabled, now actually reachable. Without this fix the position does not move at all,
because the system cannot buy (cap) and will not serve the only thing it could sell.

## 4. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| Paused strategies inflate the route count | `SELL` counting 1 with the fix and 5 without |
| The single route then wins priority | `fixed-size-sell-20260724-001` still not preferred when it is the only SELL |
| Nothing is resurrected | any PAUSED or RETIRED strategy being served or reclassified |
| No risk or lifecycle rule moved | any diff to the Guardian, the limits, or `_needs_probation` |

## 5. Verification

- Test: with one PROBATION SELL and three PAUSED/RETIRED SELLs, `_uncovered_capabilities` returns
  `{"SELL"}` and `_supplies_capability` is true for the probation one.
- Test: a disabled strategy does not count as a route.
- Test: with two servable SELL routes, `SELL` is *not* uncovered — the <=1 threshold is preserved.
- Test: `select` actually returns the single-route strategy in preference to an older HOLD candidate.
- Test: the PAUSED strategies are untouched in the library afterwards.
- `make verify` — 52/52 classes, 0 failed.
- Live, on the open market: `fixed-size-sell-20260724-001` appears in the cycle log and
  `_agent_holding("SPY")` drops below 17.

## 6. Explicitly not in scope

- Changing the Guardian, any risk limit, the probation budget, or the lifecycle rules.
- Reversing any pause. `fixed-size-sell-005` and `-006` are paused for a measured reason and this
  change does not argue otherwise.
- Widening the rule space, which remains the separate three-layer change recorded in
  `2026-10-06-paper-audit-and-enable-execution.md`.