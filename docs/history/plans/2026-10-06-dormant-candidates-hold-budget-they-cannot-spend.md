# Six candidates hold probation budget they can never spend

Date: 2026-10-06
Status: **DONE - backlog 14 -> 8 at SPY 769.72; 78 cycles of unreachable budget released**
Baseline commit for rollback: `a37fa87`
Scope: `src/autopoiesis/strategy_admission.py`, `src/autopoiesis/strategy_engine.py`, and their
tests. No change to the cap value, `PROBATION_CYCLES`, any Guardian check, or any risk limit.

---

## 0. The question

A full review of the library asked why the probation queue is full of candidates that cannot
trade. The admission gate already refuses a `TREND_FOLLOW` whose trigger band brackets the market
(`26c341d`), and the selector filters non-actionable candidates before serving them. So the queue
should hold only usable candidates.

It does not, and the reason is that those two mechanisms run against **different** populations.

## 1. The measurement

Six candidates sit in `PROBATION`, are dormant at SPY 769.72, and were admitted on **2026-10-02** —
one day *before* the band check `26c341d` landed on 2026-10-03 10:36. So they are pre-fix backlog:

```
trend-follow-20260714-001  created 2026-10-02  ref=764.16  thr=0.025   DORMANT
trend-follow-20260721-001  created 2026-10-02  ref=770.615 thr=0.018   DORMANT
trend-follow-20260722-001  created 2026-10-02  ref=769.41  thr=0.015   DORMANT
trend-follow-20260723-001  created 2026-10-02  ref=769.30  thr=0.015   DORMANT
trend-follow-20260724-001  created 2026-10-02  ref=769.58  thr=0.012   DORMANT
trend-follow-20260726-001  created 2026-10-02  ref=769.58  thr=0.030   DORMANT
```

Every one is refused by `_reference_price_rejection_reason` if re-admitted today, with the reason
spelled out — e.g. `"reference_price 769.41 puts the trigger band [757.87, 780.95] around the real
SPY price 769.72, so the strategy can only ever HOLD."`

The model itself diagnosed this, unprompted, in a proposal rationale from 2026-10-05:

> *"All existing TREND_FOLLOW strategies anchor their reference at or near the current 769.58
> price, yielding bands that bracket the market and can only ever HOLD. This spec places the
> reference at 790.00 with a 2% band (lower edge 774.20), so the entire trigger zone sits above
> last_price."*

So **the fix is working** — every proposal since has anchored outside the band, and 5 of the 12
newest candidates are actionable. The problem is what the six *already-admitted* ones cost.

Each holds a `PROBATION_CYCLES = 13` budget it can never spend, because the selector filters
dormant candidates before serving them:

```
dormant PROBATION candidates: 6
PROBATION backlog counts ALL probation: 14
```

`probation_backlog()` counts every candidate `_needs_probation` says is short of budget —
regardless of whether it can act. So **6 candidates are a standing claim on 78 cycles of budget
that will never be spent**, and since the backlog feeds the capacity cap, they also contribute to
refusing new candidates:

```
 24  17 candidate(s) are in probation and still short of the 13-c[ycle budget]
  4  13 candidate(s) are in probation and still short of the 13-c[ycle budget]
```

Neither the lifecycle rules nor the progression check notice: all six carry an **empty**
`lifecycle_reason`, so nothing anywhere records that they are stuck.

## 2. Why not simply retire them

Retiring is the wrong instrument and the reason matters. They are not *failing* — they have not
been *able to run*. `TREND_FOLLOW` is explicitly reversible by design: *"It is price-dependent and
reversible: when the market leaves the band the strategy becomes selectable again. Nothing is
retired here."*

Retiring them would contradict that, destroy candidates that a future regime could use, and — since
their references are within a few percent of spot — make them actionable again by a market move,
at which point the correct action would be to serve them, not to have thrown them away.

## 3. The fix

**Count only candidates that can be served** in `probation_backlog()`, the same way
`StrategySelector` decides who to serve. The two must agree by construction rather than by two
implementations agreeing.

The subtlety: `probation_backlog()` takes `results` but **no price**, and `_needs_probation` is
price-independent. The selector already has `self.market_prices`-free access to `last_price` as a
parameter. So the admission gate needs the price to make the same decision.

`StrategyAdmission` already holds `market_prices` and is handed `set_market_prices` each cycle, so
the predicate it needs is the selector's own `_declared_actions(strategy, price)`. Reusing that
predicate is the point: `selector._declared_actions` is already *"the same predicate the admission
gate uses"*, so calling it from `probation_backlog` makes the two agree rather than introducing a
third rule.

Concretely:

```python
def probation_backlog(self, results=None) -> int:
    by_id = {...}
    price = next(iter(self.market_prices.values()), None)
    return sum(
        1
        for strategy in self.strategy_library.list()
        if self.probation_selector._needs_probation(strategy, by_id.get(strategy.strategy_id))
        and (price is None or self.probation_selector._declared_actions(strategy, price))
    )
```

`price is None` keeps the current behaviour when no price is known — the quality argument already
used elsewhere in this class, *"the checks are skipped rather than guessed"*, and it avoids
refusing admission on missing data.

## 4. What this does and does not do

**Does:** stop six unservable candidates from consuming the capacity budget, which lets admission
reopen without waiting for cycles that cannot arrive. The budget is spent on candidates that can
actually use it.

**Does not:** change the cap, the budget, the Guardian, or the selector. The dormant six stay in
`PROBATION` and become servable the moment the market leaves their band — which is the reversible
behaviour the design already documents.

**Also does not** silently hide them: `probation_backlog` is used for the capacity decision, and
the reason string should say what it counted, or an operator comparing 14 candidates against a
backlog of 8 will think the count is wrong. So the capacity message names both figures.

## 5. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| Dormant candidates consume backlog | `probation_backlog()` returning 6 with a price set and 14 without |
| The predicate is shared, not duplicated | `_declared_actions` being called anywhere else in the gate |
| Nothing is destroyed | the six strategies disappearing from the library |
| Missing price degrades safely | `probation_backlog()` returning 0 when `market_prices` is empty |

## 6. Verification

- Test: with `market_prices={"SPY": <a price inside the band>}`, the backlog excludes those
  candidates; with the price outside, it includes them; with no price, it matches today's count.
- Test: the six on-disk dormant candidates are excluded at 769.72 and still present in the library.
- Test: the capacity message names both the served and the total candidate counts.
- `make verify` - 52/52 classes, 0 failed.

## 7. Explicitly not in scope

- Retiring, pausing, or deleting anything.
- Changing `max_probation_queue` or `PROBATION_CYCLES`.
- Widening the rule space, disabling shadow mode, or anything touching the account.
---

## Result

`probation_backlog()` now counts only candidates that can be served, using the selector's own
`_declared_actions`; `dormant_probation()` reports the difference; the capacity message names both.

```
backlog with NO market price (today's behaviour): 14
backlog WITH price 769.72                      : 8
dormant (short of budget, cannot act)         : 6
  -> 6 of the 14 released, freeing 78 cycles of budget
```

Gate: **52 classes, 0 failed, 1510 test executions across 64 files**, 948 pytest, ruff and mypy
clean. Four new tests, including the reversibility property:

```
test_a_candidate_that_cannot_act_does_not_hold_the_capacity_budget
test_a_dormant_candidate_returns_to_the_queue_when_the_market_leaves_its_band
test_without_a_market_price_the_backlog_counts_every_candidate
test_the_capacity_refusal_names_both_the_queued_and_the_dormant_counts
```

The second one matters most. Nothing is retired here, because dormancy is reversible by design —
`TREND_FOLLOW` "is price-dependent and reversible: when the market leaves the band the strategy
becomes selectable again." At 100.00 the banded candidate is dormant and unqueued; at 105.00 it is
queued again, and `library.exists("banded")` throughout. A temporary condition must not cost a
candidate its queue position permanently.

### One gate failure worth noting

After the change, `daemon-source-matches-worktree` went red — the running daemon was on
superseded code. That check is the reason this class of change is safe to make while a live
system is trading: it caught the deployment gap rather than leaving it to be discovered by a
mismatch in behaviour. `make restart` brought the fingerprint to `faa9e19bd135c8d6` and the gate
returned to 52/52.

### What this does not do

It does not admit anything by itself. The live cap is 13 and the backlog is now 8, so admission
has headroom it did not have — but a candidate must still pass the baseline rule, the duplicate
check, the reference-price check and the progression check, and on the live record 174 of 242
proposals are refused on those grounds anyway. The measured refusal mix was 58 duplicate
TREND_FOLLOW parameters, 51 behavioural twins, 26 duplicate ids, and 28 capacity.

It also does not change the *nature* of the supply problem. The gate refuses duplicate parameters
and behavioural twins, and it refuses a band around spot, so what remains admissible is close to
"a TREND_FOLLOW anchored outside the current band" — a space of maybe 6-8 distinct candidates
across the plausible band placements. That is a ceiling on how good the pipeline can get, and it
is the same finding as the long-only backtest: the design space, not the plumbing.
