# The incumbent cadence is evaluated once per reflection window, not once per cycle

Date: 2026-10-05
Status: **DONE - the cadence advances per cycle; the incumbent is served 1 in 5, unclustered**
Baseline commit for rollback: `e3ce2c9`
Scope: `src/min_agent/strategy_engine.py`, `src/min_agent/llm_decision.py`,
`src/min_agent/policy_engine.py`, `src/min_agent/daemon.py`, and their tests. No change to
`INCUMBENT_SHARE`, the admission cap, the probation budget, any risk limit, or the Guardian.

---

## 0. The question

`3407ebc` added `INCUMBENT_SHARE = 5` so a promoted strategy would actually trade, having measured
that the one ACTIVE strategy was selected 0 times in the 146 cycles after its promotion. The
comment above the rule states the intent precisely:

> Counted on `cumulative_cycles`, which is the journal's own monotone count of service served, so
> the cadence is stateless: nothing to persist, nothing to reset, and it cannot drift from the
> record.

Observed on the first live session after the open, the incumbent was selected **0 times in 8
cycles**, and the reason is that the count is not what the comment says it is.

## 1. The measurement

```
reflection generated_at: 2026-10-05T14:05:22Z
strategies in the record: 13   with non-zero cumulative_cycles: 13
TOTAL served = 146
  146 % 5 = 1  ->  incumbent served this window: False
```

`select()` computes:

```python
served = sum(r.cumulative_cycles for r in results or [])
if self.incumbent_share > 0 and served % self.incumbent_share == 0 and last_price is not None:
```

and `results` reaches it like this:

```
llm_decision._results()  -> reflection_memory.load() -> strategy_results(record)
policy_engine.decide_snapshot() -> the same two calls
```

So `cumulative_cycles` is read out of **reflection.json**, which is rewritten only on the
reflection interval (`MIN_AGENT_REFLECTION_INTERVAL_SECONDS=1800`, 30 minutes). The sum is
therefore **frozen for the whole window**, and `served % 5 == 0` is a constant condition across
the ~6 cycles it covers.

The consequence is not a slightly-wrong ratio. It is that the rule collapses from a cadence into
a **batch gate**: on every cycle in a window the incumbent is served, or on no cycle in that
window. With the sum advancing ~6 per window, the residue steps 1, 2, 3, 4, 0, 1..., so roughly
one window in five serves the incumbent — and that window serves it on **every** cycle. The
average share lands near the intended 20% while the distribution is nothing like it: hours of
total starvation, then a burst.

That is the same failure the windowed-count probation bug had (`e02a8ff`), reached through a
different door. A rule that reads a periodic snapshot of a monotone counter is a rule driven by
the snapshot's period, not by the traffic it is meant to govern.

## 2. The fix

The comment's intent was right and is worth keeping: a journal-derived count survives restarts,
so nothing has to be persisted and the cadence cannot drift. The defect is only that the count is
read from a **stale copy** instead of the record.

`select()` therefore takes the served count as an explicit argument rather than mining it out of
a reflection snapshot:

- `select(..., served: int | None = None)`. When `None`, the current behaviour is preserved
  exactly, so every existing caller and test keeps its meaning.
- The daemon owns the monotone count, so it passes `self.cycle_count` at the two call sites. That
  count advances once per cycle, which is the frequency the cadence is defined over.

`policy_engine.decide_snapshot` and the LLM context path in `llm_decision.py` both need the value
threaded through. That is the whole change: no new state, no persisted counter, no new file.

Rejected alternative: an in-memory counter on the selector, incremented per `select()` call. It is
a smaller diff but it resets when the daemon restarts, which loses the restart-stability the
comment explicitly claims and which is the reason to read a journal-derived count at all.

## 3. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The cadence is per cycle | 20 consecutive `select()` calls with `served` stepping by 1 yielding 4 incumbents |
| Existing callers are unaffected | any test that called `select()` without `served` changing its result |
| The share is still ~1 in 5 | the live session serving the incumbent on roughly 20% of cycles |
| No risk boundary moved | any diff to `INCUMBENT_SHARE`, the caps, or the Guardian |

## 4. Verification

- New tests in `tests/min_agent/test_strategy_engine.py`: with `served` stepping 1..20, exactly 4
  selections are the incumbent, and they are not clustered. This is the property the current
  implementation cannot produce, so it is the test that would have caught the defect.
- A test that `served=None` reproduces the old behaviour, so the default path is pinned.
- `make verify` - 52/52 classes, 0 failed.
- `make restart`, then observe the live session: the incumbent should appear on roughly 1 cycle
  in 5 rather than in 30-minute batches.

## 5. Note on the other live finding

`MIN_AGENT_SHADOW=1` is set, and the shadow flag replaces the final broker call with a journalled
intent. Both of today's `SHADOW_ORDER_INTENT` events carry `shadowed: true`, so no order reached
the broker. That is the flag working as documented and it is not part of this plan; it is
recorded in `STATUS.md` because it means approved decisions cannot yet produce fills.

---

## Result

`select()` takes `served` explicitly; the daemon publishes its own cycle count through
`on_cycle_count` and `cli.py` wires it to the policy engine's `served_provider`, so both the LLM
context path and the policy-engine fallback measure the cadence against cycles. `served=None`
still derives the old way, so every existing caller keeps its meaning.

Gate: **52 classes, 0 failed, 1481 test executions across 63 files**, 932 pytest, ruff and mypy
clean. Three new tests, of which the first is the one that could not have passed before:

```
selects the incumbent 4 times in 20 cycles, exactly 5 apart
the frozen 146 snapshot starves it, and served=5 rescues it
served=None reproduces the previous derivation
```

At the exact values the live run was stuck on: `146 % 5 == 1` starved it, and the count now
steps, so the incumbent is served on 145, 150, 155 rather than in 30-minute batches.

### A defect I introduced and the tests caught

Threading the count through `llm_decision.py` as a bare `self.policy_engine.served()` call broke
two provenance tests. `_context` catches every exception and degrades to a decision with no
selected strategy, so calling a method that a duck-typed policy engine does not have raised
`AttributeError` *inside* that guard and silently dropped the selected strategy - the mandate
stopped reaching the model and `strategy_id` came back `None`. The tests that exist to assert
the mandate is shown and attributed are exactly what noticed. Reached through `getattr` with a
`None` fallback now, so a policy engine without the accessor degrades to the old derivation
rather than to no strategy at all.

This is the second time in this project that a broad `except` turned a missing method into a
silent behaviour change rather than a loud failure. The lesson is that a degradation guard is
only safe if what it hides is inspected: both of these produced a *plausible* result - a
decision without a strategy, a cadence that never fired - and neither raised anything a human
would have noticed without a test asserting the specific thing that vanished.
