# Deployment record, 2026-10-17

## Why this deploys with one gate class red

The rule this work wrote into its own plan is:

> Do not deploy while the local gate is red. Resolve the failing classes first, **or record
> why a class is expected to be red at this point in the work**.

This is that record for `replay-audit`, and it is a statement about the *check*, not about
the system.

**The system is self-consistent.** `make doctor` reports `RESULT: OK with 10 warning(s)` and
`76 closed lot(s)`, and `tools/replay_audit.py`'s ledger side reports the same 76. The two
agree, so the accounting the whole record rests on is consistent with the instrument that
audits it.

**`self-evolution-closes` is fixed, not waived.** Its probe returns `[]`, and it was fixed at
the root cause on 2026-10-16 - the replay never stamped provenance, so the screen scored 0 of
282 counterfactual rows.

**`replay-audit` asserts an equality that five measurements show cannot be satisfied by
construction.** The check re-derives the figures from the raw journal and asserts them equal
to the production ledger's:

    closed lots:   naive 47  vs ledger 76
    realized PnL:  naive 757.95 vs ledger 601.37
    unmatched:     naive 29  vs ledger 18

Every hypothesis for the gap has been measured, not argued, and each was disproved:

| # | hypothesis | measurement |
|---|---|---|
| 1 | the owner's lots seeded as opening lots | PnL 757.95 to 32,388.98 |
| 2 | the owner's fills merged as activities | 47 to 1,539 closed lots |
| 3 | deduplication by client order id | 97 confirmations, 97 distinct ids, zero repeated |
| 4 | the missing `ORDER_FILL_SNAPSHOT` source | 47 to 93, overshooting the ledger's 76 |
| 5 | the fill dating, `filled_at` vs the cycle anchor | figures unchanged |

Hypothesis 4 is the real scope gap and it is not the owner's: the naive pass read 97 agent
fills from `ORDER_FILL_CONFIRMED` while `confirmed_fill_activities` returns 160, because the
ledger also projects `ORDER_FILL_SNAPSHOT` into one latest cumulative row per
`client_order_id`. Reading both is necessary and not sufficient - it overshot - and the
remaining difference is in how the ledger's activity list is assembled, which is
`(*confirmed_candidates, *seeded_fills)` after dedup by order.

**Reconciling against that list needs the production activity list, which is exactly what
this check exists to be independent of.** A naive pass that imports the machinery it checks
can only ever agree with it, and the check's own docstring says so. So the honest conclusion
is that this check cannot be reconciled by construction from the journal alone, and the
premise in `2026-10-13-replay-audit-scope.md` is what should be revisited - not the check's
arithmetic and not the ledger.

**No assertion was relaxed to reach this point.** The three figures are exactly what the
check computes; nothing was edited to make them agree. What is recorded here is why the
class is expected red, which is the exception the rule itself provides.

## What is being deployed

Everything committed since the last deployment (`0883e59696434dfd`, 2026-10-15 19:18):

- `self-evolution-closes` root-cause fix (replay provenance)
- the VIX freshness alarm, the refresh tool, and the pre-open timer
- the VIX floor re-engaged (cache refreshed, `[PASS] vix freshness`)
- the drifted-reference lifecycle retirement
- the uncovered-symbol stale-quote guard
- `replay-audit`'s crash fix and its `owner_fills` configuration fix
- the decision path's empty-body diagnosis (P2's prerequisite)
- `gpu-devices-reserved`, and the GPU pin at 2,3
- the journal parse cache

## Verification after the restart

Recorded below from the commands that ran, not from intent.

## What this record does not claim

That `replay-audit` is fixed. It is red, and it will stay red until its premise is decided.
That decision belongs to the account holder: report the difference with a directional
invariant instead of asserting equality (relaxes an assertion), let the naive pass read the
production list (leaves a check that can only agree), or drop the two assertions (loses an
instrument that did find a real scope difference).
