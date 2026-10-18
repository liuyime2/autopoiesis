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

## Verification, from the commands that ran

Deployed 2026-10-17 07:54 EDT, market closed.

    daemon PID           2251645, NRestarts=0, ActiveState=active, error_count=0
    daemon fingerprint   be20d1a210109ce6
    on-disk fingerprint  be20d1a210109ce6
    market-preflight     be20d1a210109ce6
    market-open-verify   be20d1a210109ce6
    timers armed         7

All four agree. The readiness timers were reinstalled in the same operation, because they
carry the fingerprint they expect and would have failed Monday's checks with
`SOURCE_MISMATCH`.

Post-deployment events: 6, of which the only non-SUCCESS is
`PROFIT_TARGET_CHECKED: profit target unproven`, which is a true statement about the record
rather than a fault. Zero cycles ran, which is correct: the market is closed on a Saturday.

`gpu-devices-reserved` reports PASS with both pins inside 0-4.

## The gate is green, 2026-10-17 10:30 EDT

    classes: 56   passed: 56   failed: 0   test executions: 3254 across 114 test files
    OK - all check classes pass
    exit=0

`replay-audit` passes because the account holder directed the fix on 2026-10-17: the three
figures are reported and three invariants asserted in place of the equalities that five
measurements showed cannot be satisfied by construction. `self-evolution-closes` passes at
the root cause. `gpu-devices-reserved` passes, after the class that never ran and counted
twice was fixed.

The deployment rule's exception is therefore no longer in use. The earlier record in this
file explained why `replay-audit` was expected red; that explanation is superseded, and the
condition it was written for - a red gate - no longer holds.

One correction to that earlier record: it said the gate had no red class left, which was
not measured and was not true. The class it was said about was crashing the gate with
NameError at the time. It is true now, and the command above is why.

The daemon runs the same source as the tree: fingerprint `be20d1a210109ce6`, daemon and disk
and both readiness units.

## Second deployment, 2026-10-17 18:03 EDT

Commit `6bb85f3`, fingerprint `066c7b255fa86806`, deployed with the market closed.

The gate was 55/56 immediately before, the only red being
`daemon-source-matches-worktree` - the daemon running superseded code, which is what a
deploy fixes. Two other classes had been red on the first attempt and were fixed rather
than waived: `tools-readme-accurate` (the new `refresh_dividends.py` was undocumented) and
`config-comment-figures-true` (87 documented against 88 measured, the new one being the
dividend cache read).

All four fingerprints agree after the restart.

## Third deployment, 2026-10-18 02:45 EDT

Commit `6675b14`, fingerprint `a6fe12b5d72497b8`, deployed with the market closed.

The P2 window fix. `DECISION_NUM_CTX` 4096 to 6144, from a measurement rather than a
principle: prompt 1,539 tokens, answer 1,245 of which 3,494 characters are the thinking
block, done_reason=stop, spare 1,312 of 4,096 - 32% on an answer whose length nothing
bounds. The failure rate went 2.0% to 5.6% to 13.7% across three days with that one cause.

All four fingerprints agree after the restart. Monday's open is the first session with it
live, and the acceptance is a parse-failure rate below 2%.

### Still unexplained, from the audit of 2026-10-17

The audit reports three daemon restarts on the evening of 2026-10-17 - 21:59, 22:24 and
22:48 - all after the 18:03 deployment, with no explanation in this record. This record
does not have one either: `NRestarts=0` is what systemd reports now, and the restarts were
not this session's. They are noted rather than explained, because inventing a cause for
them would be worse than leaving the gap visible.
