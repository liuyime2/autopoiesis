# Journal what changed, not what was already recorded

Date: 2026-10-03
Status: **DONE - implemented, measured, verified live, gated**
Baseline commit for rollback: `3aafc18`
Scope: `src/autopoiesis/daemon.py`, `tests/autopoiesis/test_daemon.py`, plus the figures three
documents quote.

---

## 0. How this was found, including being wrong first

The previous plan's next item was to check whether anything distinguishes "not screened
recently" from "screened, unchanged", so that the 9661 redundant offline screens could be
deduplicated.

That question has a clean answer, and it was worth asking - but checking it required
measuring the journal, and the measurement overturned the premise. Two claims in the
previous plan were wrong:

- "most of why the journal is 51MB" - the journal is **114MiB**, and the offline screens
  are **5%** of it.
- The waste was assumed to be the screen duplicates.

Measured by event type, over both journal generations:

```
     bytes    share   count  event
 61,969,809    51%      356  COUNTERFACTUAL_EVALUATED
 31,584,938    26%     1909  PNL_EVIDENCE_RECORDED
 13,074,755    10%      862  STRATEGY_EVALUATION_RECORDED
  6,981,417     5%     9927  OFFLINE_VALIDATION_COMPLETED
  3,080,861     2%      484  BROKER_EVIDENCE_INGESTED
```

356 counterfactual events occupying **51%** of the journal is 174KB per event, which is
not a plausible size for one decision's counterfactual verdict. Decomposing it:

```
total rows written           : 210,473
distinct cycle_ids ever      : 720
rows that re-record a cycle  : 209,753 (99%)
rows per event: min 467, max 720, mean 591
```

The journal holds 1,179 cycles. The counterfactual ledger describes 720 of them. And every
one of the 356 events carries essentially the whole ledger - 467 to 720 rows each - so
**209,753 of 210,473 rows are re-records of a decision already in the journal.**

## 1. The defect

`_record_counterfactuals` recomputes the counterfactual report over the whole journal, then
journals `report.rows` - all of them - on every maintenance pass.

The growth was self-feeding. Each pass appended ~186KB, and `_screen_all_strategies`
already re-parsed every counterfactual event on every pass to answer a question about a
fixed 720 decisions. More rows meant a slower read, which meant the same rows were
re-derived and re-written for longer. The journal-headroom warning appearing during this
work - `80% of the 67MB cap used; rotation imminent` - is that loop arriving.

## 2. Why the rows cannot simply be dropped

`payload.rows` is not a write-only dump. `offline_validation.collect_decisions` reads it,
and that is deliberate: the screen reads journalled rows "so this cannot disagree with what
was recorded and cannot manufacture evidence". Removing the payload would leave the screen
unable to be audited or recomputed, which is the property the whole replay-determinism gate
exists to protect.

So the rows stay. What changes is that a row is written when it is **new or its verdict
changed**, not on every pass.

`collect_decisions` accumulates `latest[cycle_id]` across *all* events with last-wins, and
so does `test_replaying_the_live_journal_reproduces_the_recorded_verdicts`. Both consume
the union across events, so splitting the ledger into per-pass increments cannot change
what either of them sees. That property is pinned by a test rather than argued for here.

### Why "changed", not "unseen"

A `PENDING` row is not a claim about the outcome - its horizon has not elapsed. Suppressing
it on sight would strand the decision as PENDING permanently and the screen would never
learn what happened. So a row is re-journalled when its verdict moves off PENDING, which is
the case that carries information. The replay gate already encodes this asymmetry: a
PENDING prior is explicitly not a determinism violation when the outcome later arrives.

## 3. The change

Two edits in `daemon.py`, both deletions of work:

1. `_record_counterfactuals` filters `report.rows` to rows whose `cycle_id` is absent from
   the journal or whose verdict differs, and returns without journalling when none are.
   The event message now carries `new_or_changed=N/M` so a reader can see the ratio.
2. `_maintenance` reads `COUNTERFACTUAL_EVALUATED` **once** and passes it to both
   `_record_counterfactuals` and `_screen_all_strategies`. It was parsed twice per pass.

`_recorded_counterfactual_verdicts` takes the events as an argument rather than reading
them itself. That started as a method with its own `read_events` and its own broad
`except`, which existed only so the tests could call it bare - it added a second read path
and a 63rd permitted `BLE001`. `_record_counterfactuals` has exactly one production caller
and it always has the events, so the argument is required and the fallback is gone. The
permitted-exception count in `ruff.toml` stays at 62 because the code no longer needs the
exception, not because the comment was updated.

## 4. Verification

Live, on the running daemon, not only in tests. The daemon was restarted onto the new code
at `15:27:00Z` (fingerprint `6fa49ad3abb99132`) and a complete maintenance pass ran
afterwards - broker evidence ingested, PnL evidence recorded, reflection generated,
curriculum proposed, admission reviewed, knowledge proposed and admitted.

```
COUNTERFACTUAL_EVALUATED rows written since restart: 0
```

The old code wrote 720 on that same pass. The last full-ledger event is timestamped
`15:19:44Z`, before the restart.

Tests added to `tests/autopoiesis/test_daemon.py`:

| Test | Pins |
|---|---|
| `test_a_repeat_pass_with_nothing_new_journals_nothing` | the defect: a second pass writes no event and leaves the union unchanged |
| `test_a_pending_row_is_rejournalled_once_it_resolves` | the complement: a resolved verdict *is* a change and is journalled |
| `test_the_screen_still_sees_every_decision_when_events_are_incremental` | the load-bearing property: every journalled decision still reaches the screen |

`make verify`: 49 classes, 0 failed, 1295 test executions across 61 files, 834 collected.
`make lint` clean; `make type` clean on 41 files.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| The screen's input is unchanged | `collect_decisions` returning a different decision set or verdict after the split |
| No information is lost | a verdict the journal held before that it does not hold after |
| Nothing is stranded | a decision that stays PENDING after its horizon elapsed |
| The journal stops growing | counterfactual bytes still rising at ~186KB per pass |

The first three are checkable against the existing journal and are covered by the tests.
The fourth is the one only time can answer, and the run above is the first observation of it.

## 6. Still open

1. **48 redundant offline screens per maintenance pass.** The prerequisite question is now
   answered - nothing downstream distinguishes "not screened recently" from "screened,
   unchanged" - so the dedup is available. It is worth about 700 bytes per event against
   this change's ~186KB, so it is second, not first. Doing it also needs one thing this
   change deliberately did not do: decide how the screen records that a pass ran and found
   nothing, since "no new decisions" is a fact worth having and not writing anything loses
   it.
2. **The probation budget.** A `FIXED_SIZE` needs 11-13 selected cycles to reach the
   10-informative gate against `min_probation_cycles = 3`, and probation has absolute
   priority over ACTIVE in the selector, so raising it diverts cycles from strategies that
   currently trade. The trade-off has to be measured before the constant changes.
3. **`PNL_EVIDENCE_RECORDED` at 26%** - 1909 events, 16.5KB each. Not yet decomposed. By
   the method used here it is the next largest thing in the journal, and it may well be
   the same shape as what was just fixed.
