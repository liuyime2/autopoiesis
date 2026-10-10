# Journal a measurement when it changes, not on a cadence

Date: 2026-10-03
Status: **DONE for the offline screen; PnL evidence deliberately deferred**
Baseline commit for rollback: `26ffc1c`
Scope: `src/autopoiesis/daemon.py`, `tests/autopoiesis/test_daemon.py`, plus the figures three
documents quote.

Second and third application of the principle fixed in
[`2026-10-03-counterfactual-ledger-increments.md`](2026-10-03-counterfactual-ledger-increments.md).

---

## 0. Where the journal stands after the counterfactual fix

Before this change, per day, from the journal:

```
2026-10-02  total  24,627,348 (23.49 MiB)
   14,429,643  58%  COUNTERFACTUAL_EVALUATED     <- fixed last turn, now 0
    5,135,087  20%  PNL_EVIDENCE_RECORDED
    2,410,174   9%  STRATEGY_EVALUATION_RECORDED
    2,046,377   8%  OFFLINE_VALIDATION_COMPLETED
```

With the counterfactual line at zero, the journal runs at roughly 7MiB/day instead of
23MiB. What is left is two lines, and this turn takes the one that is cheap and safe.

## 1. The offline screen: same defect, and it was free to fix

Measured: **9661 of 9832** `OFFLINE_VALIDATION_COMPLETED` events repeat the previous
verdict for the same strategy unchanged, and a maintenance pass wrote **48 of them every
fifteen minutes**.

Nothing downstream distinguishes "not screened recently" from "screened, unchanged", and
this was checked rather than assumed:

| Consumer | What it does with the events |
|---|---|
| `_offline_evidence_by_strategy` | `latest[strategy_id] = ...` - overwrite, last wins |
| `lineage` | `origins[strategy_id].offline_verdict = ...` - overwrite |
| `experiment_registry` | `experiment.offline_verdict = ...` - overwrite |
| `summarize` | counts one verdict per *strategy*, not per event |
| `_apply_offline_rejection` | returns early when the strategy is already PAUSED/RETIRED |
| `replay_audit` | prints the count; thresholds nothing on it |

It was also free in the only sense that matters: `_offline_evidence_by_strategy` already
parsed every validation event on **every** pass. So reading them once in `_maintenance` and
sharing that read with both the screen and the lifecycle **removes** a full journal scan per
pass rather than adding one. The same read is what tells the screen whether a verdict moved.

## 2. What is compared, and why not just the verdict

The whole payload, not `verdict`. This is not tidiness - it is the dangerous case:

`validate` counts `scored` as the decisions whose counterfactual reached an informative
verdict, and `_promotion_evidence_gate` refuses promotion below ten of them. A strategy
climbing from 3 to 7 informative decisions **keeps the verdict name
`INCONCLUSIVE_INSUFFICIENT_EVIDENCE` the whole way**. A verdict-only comparison would
suppress exactly the events that record a strategy approaching the promotion bar, and
strand it there permanently.

`test_a_changed_verdict_is_screened_again` holds that state: `scored` moves 3 -> 7, the
verdict string is asserted *unchanged*, and a second event must appear.

## 3. PnL evidence: measured, and deliberately not changed

Decomposed before deciding:

- 1911 events, and **381 distinct payloads once `generated_at`, `pnl.window_start` and
  `pnl.window_end` are removed** - so 1530 events (80%) record no change but the clock.
- That is 26.5MB held by events belonging to a repeated group, about 4MB/day.
- The payload is genuinely read: `doctor._check_pnl_attribution` takes `events[-1]` and
  passes `payload.pnl` to `attribution.attribute`.

It is the same shape, and it was still left alone, for a reason worth recording:

**There is no cheap way to ask what the last PnL payload was.** `read_events` accepts a
`limit`, but `_scan` iterates every line in every generation regardless and only bounds a
deque - `read_events(..., limit=1)` is a full 114MiB scan. `_record_pnl_evidence` already
calls `read_all()`, so the journal is fully scanned every pass; adding a second full scan
purely to decide whether to skip a 16.5KB write is a bad trade.

The alternatives each cost something real:

- **An in-memory cache of the last payload**, like `_verify_profit_target` already uses for
  the profit-target status. Affordable, and its failure mode is safe - a stale or missing
  cache causes an *extra* event, never a lost change, because the journal is append-only.
  But it introduces a second copy of a fact, which is what the single-source-of-truth rule
  exists to prevent, and `cli.py` also writes `PNL_EVIDENCE_RECORDED`, so a CLI-driven run
  could leave the daemon's cache behind.
- **A cheap tail read in `journal.py`.** A real capability, and the honest fix - but its
  only current caller would be this one line, so it is infrastructure added ahead of
  evidence that it earns its keep.

Recorded rather than guessed at. When the PnL line becomes the thing that limits something,
the tail read is the change, and it will have a second caller by then.

## 4. Verification

Live, on one maintenance pass of the running daemon, restarted onto `fd9f9279118da845`:

```
COUNTERFACTUAL_EVALUATED rows written : 0    (was 720)
OFFLINE_VALIDATION_COMPLETED written  : 1    (was 48)
```

The one screen event is `trend-follow-20260802-001`, a first-cycle candidate with
`decisions: 0` - its first screen on record. So the single write is a genuinely new
candidate, and the 56 strategies whose verdicts did not move wrote nothing. A result of 0
would have been the suspicious one; 1 with a new candidate in it is the shape the rule
intends.

Tests in `tests/autopoiesis/test_daemon.py`:

| Test | Pins |
|---|---|
| `test_an_unchanged_verdict_is_not_screened_again` | the defect: a second pass over the same journal writes nothing and leaves the payloads identical |
| `test_a_changed_verdict_is_screened_again` | the dangerous case: `scored` 3 -> 7 with the verdict name unchanged still writes |

Both fixtures use BUY decisions that lose money, so they score `FALSE_TRADE` rather than
`NEUTRAL` - `validate` excludes NEUTRAL from `scored`, so a fixture of neutrals would leave
the field the promotion gate reads unmoved and the test would pass while exercising nothing.
Prices vary per cycle because `_price_series` collapses consecutive identical quotes; three
cycles all pricing SPY at 101.0 are one observation, not three. Both were caught by the
tests failing, not by reading them.

`make verify`: 49 classes, 0 failed, 1301 test executions across 61 files, 836 collected.
`make lint` clean; `make type` clean on 41 files.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| A consumer cannot tell the two cases apart | any of the six in the table above depending on event count or recency |
| No promotion is lost | a strategy at 9 informative decisions that stops being screened |
| No scan was added | the per-pass journal read count going up rather than down |

The second is the one that matters and it is the reason `scored` is compared rather than
`verdict`.

## 6. Still open, in the order the measurements put them

1. **`PNL_EVIDENCE_RECORDED`, ~4MB/day, 80% redundant.** Blocked on a cheap tail read, for
   the reason in section 3. `STRATEGY_EVALUATION_RECORDED` (~2MB/day) is the next largest and
   is not yet decomposed - by the method used twice here it may be the same shape again.
2. **The probation budget.** A `FIXED_SIZE` needs 11-13 selected cycles to reach the
   10-informative gate against `min_probation_cycles = 3`, and probation has absolute
   priority over ACTIVE in the selector, so raising it diverts cycles from strategies that
   currently trade. The trade-off has to be measured before the constant changes.
3. **The 25 dormant strategies already admitted.** The admission gate now refuses new ones;
   these still hold library slots.
