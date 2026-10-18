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

## P5: `codex/rsi-audit-repairs` merged into `main`, 2026-10-18

P5 asks for the branch to be merged into `main` once the gate and the three CI versions are
green, and for `main`'s CI run number and result to be written here. This is that section.

Measured before pushing:

- `main` was `49063c8` (2026-10-12) and is an **ancestor** of the branch tip, so this is a
  fast-forward, not a three-way merge: no conflict was possible and no history was rewritten.
- The branch tip is `162cbcf`, 125 commits ahead of `main`, 190 files changed, +21,137/-1,826.
- Local full gate immediately before the push: `classes: 56  passed: 56  failed: 0`,
  `OK - all check classes pass`.
- CI on `162cbcf` before the merge: run **#324** (push) and run **#326** (pull_request), each
  with `gate (3.10)`, `gate (3.11)`, `gate (3.12)` and `release` all `success`.

The merge itself:

```
git push origin HEAD:main
   49063c8..162cbcf  HEAD -> main
```

Pull request **#10** (`codex/rsi-audit-repairs` -> `main`) is recorded as **merged** by
GitHub: `merged_at 2026-10-18T12:31:32Z`, `merge_commit_sha 162cbcf`.

CI on `main` - the run number this section exists for:

| Workflow | Run | Sha | Result | Jobs |
| --- | --- | --- | --- | --- |
| `ci` | **#327** | `162cbcf` | **success** | gate (3.10) 2.2m, gate (3.11) 2.7m, gate (3.12) 2.9m, release 0.5m |
| `pages` | **#27** | `162cbcf` | **success** | build 0.5m, deploy 0.3m |

`main` was red before this. `ci` run **#118** on `49063c8` (2026-10-13) concluded
`failure`, and it had stayed that way for five days because nothing was merged into it.
The branch's work is the first thing to make `main` green since 2026-10-13.

Deliberately not on `main`: commit `7bfac1a`, the 2026-10-18 review appendix to
`docs/PLAN_2026-10-14.md`, exists only on this machine's local `master`. It is the account
holder's own commit on a line that diverged before the branch, it is documentation-only, and
P5 asks for the branch merge. Carrying it in would have put a commit on `main` that the gate
run above did not cover, so it stays where it is; the branch that owns it can push it and it
will fast-forward.

One measurement worth keeping, because it is the reason the 2026-10-18 review asked for the
gate to be split: the `ci` `gate` jobs took 2.2 to 2.9 minutes each, while the local full
`make verify` took about 23 minutes on this host. They are not the same cost, and the plan's
"gate green" was satisfied by the local full one.

## Fourth deployment, 2026-10-18 10:23 EDT - quote freshness

Commits `298a096` and `3277c91`, fingerprint `53ba1c5e6d468151`, deployed with the market closed.

### The baseline, measured before the change

Over the 2,495 cycle records in `journal.jsonl` and its two archives, the Guardian refused for a
stale price quote:

| Day | Refusals naming a stale price quote |
| --- | --- |
| 2026-10-12 | 7 |
| 2026-10-13 | 12 |
| 2026-10-14 | 13 |
| 2026-10-15 | 47 |
| 2026-10-16 | 3 |

82 in total, almost all of them BIL, and 47 of them inside one hour on 2026-10-15 with the reported
age climbing from 1,172s to 4,834s across that hour - for SPY as much as for the thin symbols. SPY's
last print is never eighty minutes old during a session, so the print *timestamp* had stopped
advancing and the gate was refusing on the wrong fact.

**A correction to this record's own earlier claim.** At one point a figure of "44 refusals a day"
was reported. It was never measured, and it is wrong: the measured figure is 82 in total across five
days. This section replaces that number rather than leaving both in the record, because a number
that was never run is worse than no number - it is a claim wearing evidence's clothes.

### What changed

The snapshot carries three facts where two fields used to hold one: the broker's clock (`timestamp`),
when the quote was observed (`quote_time`), and when the price printed (`price_time`). Only the first
two gate. The account holder's decision of 2026-10-18 was to keep pricing decisions on the last trade
rather than move them to the quote mid.

The fallback is the safety-relevant part: `quote_time = quoted_at or price_time`. Without it a quote
outage would leave the field empty, and empty means "nothing to check" in the Guardian - so the gate
would have stopped gating exactly when the data got worse. Two tests pin that composition so it
cannot be dropped later as redundant.

Nothing is hidden. `price_time` is on the snapshot, and the decision context reports
`last_price_source`, `last_trade_age_seconds` and `quote_age_seconds`, because the model is the one
reader that would otherwise be unable to see how old the price it is deciding on is. Bid and ask are
recorded as evidence of the quote and are not used to price anything.

### Verified against the live broker

A real snapshot with the market closed (Sunday 2026-10-18):

| Symbol | last trade | bid | ask | trade age | quote age |
| --- | --- | --- | --- | --- | --- |
| SPY | 763.31 | 763.50 | 764.60 | 146,214s | 146,403s |
| BIL | 91.56 | 91.55 | 91.57 | 149,099s | 145,762s |
| TLT | 80.01 | 80.00 | 80.07 | 148,198s | 146,788s |

The two feeds are demonstrably different facts, which is the whole point: SPY's quote is *older* than
its trade, while BIL's is 3,337s - fifty-six minutes - *newer*. A gap in BIL's direction is what the
refusals were made of.

### Gate

Before the deploy: `classes: 56  passed: 54  failed: 2`, both reds being the daemon running
superseded code, which is what a deploy fixes. On the first attempt three classes were red;
`config-comment-figures-true` (88 documented against 89 measured) was fixed rather than waived.

After the deploy the two were re-checked individually rather than by a full 56-class re-run:
`daemon-source-matches-worktree` PASS (daemon and worktree both at `53ba1c5e6d468151`),
`make doctor` `RESULT: OK with 11 warning(s)`, `config-comment-figures-true` PASS. All four
fingerprints agree: daemon, disk, `market-preflight.service`, `market-open-verify.service`.

The next session is the first with it live, and the acceptance is that the "stale price quote"
refusals stop appearing for symbols whose bid and ask are current.
