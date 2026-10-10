# The research stage has no driver, so exploration is manual by construction

Date: 2026-10-04
Status: **DONE - the driver runs unattended on real bars and recorded 48 trials; nothing passes**
Baseline commit for rollback: `112731f`
Scope: `src/autopoiesis/research/driver.py`, its tests, and a scheduler hook. No change to
`required_trades`, the admission cap, the probation budget, the screen, the Guardian, the
production journal, or the production/research separation.

---

## 0. The question

The objective asks for a system that explores strategies 完全自动. Measured on the live record,
the candidate *generation* side is autonomous and the candidate *evaluation* side does not exist:

```
18467 journal events, 18 distinct event_type values
  382  CURRICULUM_PROPOSED          <- LLM proposes candidates, autonomously, for months
   51  CURRICULUM_FAILED
  215  STRATEGY_ADMISSION_REVIEWED
    0  anything from the research stage
```

So the loop generates candidates and promotes them on live paper evidence, but it **never runs a
backtest or a walk-forward evaluation as part of operating**. `record_trial()` has no production
caller anywhere in the repository:

```
$ grep -rn "record_trial" src/ tools/ --include=*.py | grep -v tests/
(no matches)
```

The 26 rows in `runtime/autopoiesis/research_trials.jsonl` were written by hand-running probes.
Every verdict this project has ever reported - including the `OVERFIT` in `112731f` - was produced
by a person typing a script, not by the system working.

## 1. Why this is the gap and not something else

The obvious candidates were ruled out by measurement first:

- **Data depth.** Ruled out in `112731f`: 20448 real bars are on disk, 3.4x what the gate needs.
  The gate opens and returns `OVERFIT` on real data.
- **The rule designs.** Ruled out in `112731f`: `TREND_FOLLOW` round-trips at 3.92 trades per fold
  over 1571-bar segments. The 4-day cache, not the design space, was the reason nothing could be
  evaluated. No rule needs adding.
- **The gate arithmetic.** Ruled out in `d60b7c6`: it provisions the evidence it counts rather
  than lowering the bar, and returns a real verdict.

What remains is that nothing *calls* the research stage. The evaluation half of
`research/__init__.py`'s own pipeline - "diagnosis -> hypothesis -> candidate generation ->
backtest -> walk-forward OOS -> robustness -> multiple-testing control -> shadow -> probation ->
promotion" - stops after backtest, because the first six steps are library functions with no
caller and the last three are reached only through the LLM curriculum path instead.

## 2. The constraint that decides the shape

`tools/verify.py: check_production_research_separation` fails the gate if **any** production module
under `src/autopoiesis/*.py` mentions `autopoiesis.research`. So a driver invoked by the daemon would
break a gate that exists for a stated reason: a backtest that can reach production state can grade
itself.

The separation is kept by making the driver a **separate entry point in its own process**, which is
also the only shape that can honestly be called autonomous: it runs on a schedule, needs no human
and no LLM, and cannot reach the production journal even by accident because it never opens it.

Rejected alternative: have the daemon invoke research. Rejected because it fails the separation
gate, and because a component that both searches and trades can eventually grade itself.

## 3. What the driver does

One function, `run_search()`, in `src/autopoiesis/research/driver.py`:

1. Load real bars from the replay cache. **Never a generator, never synthetic** - the same rule
   `replay.py` already states. An empty or missing cache is an error, not a zero-result run.
2. Enumerate candidates over the rule space that exists: `TREND_FOLLOW` across the threshold grid
   `walk_forward` already supports, plus `FIXED_SIZE`. No new rule kinds.
3. For each candidate, run `run_walk_forward` and take its verdict, then `record_trial()` the
   verdict **with the numbers behind it** - the figures the verdict was computed from, so a
   reviewer can recompute it.
4. Return `summarise(read_trials())` merged with this run's rows.

The driver writes only to `research_trials.jsonl`. It reads bars through the existing read-only
`autopoiesis.replay.load_bars` rather than re-implementing a parser - a research module reading
real data is harmless, because the separation that matters runs the other way. What it must never
reach is anything that *writes* production state, so the test asserts that `driver.py` imports
none of `journal`, `guardian`, `executor`, `strategy_engine` or `strategy_admission` - asserted
from the source rather than trusted.

### Multiple-testing honesty

`summarise()` already reports `trials_run`, which is the denominator `required_trades()` divides
by. A driver that searched 12 thresholds must not then evaluate the winner against the 52 required
for a single trial, or the search's own selection bias is hidden - which is the exact failure
`trials.py`'s docstring exists to prevent. So the driver computes `required_trades` from the
**total** trial count, the same way `walk_forward` does, and a search that grows raises its own bar.

## 4. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The research stage has no production caller | `grep -rn record_trial src/ tools/` returning a non-test caller |
| The driver records trials autonomously | `research_trials.jsonl` unchanged after a real run |
| The separation holds | the driver, or anything it imports, importing a production module |
| The gate still passes | `make verify` below 52/52 classes |
| The search is honest | a run whose winner is judged against a `required_trades` computed from fewer trials than it ran |

## 5. Verification

- New test: the driver over real cached bars records one trial per candidate with its verdict and
  figures, and writes nothing outside the trials file. Named `test_research_driver.py`.
- New test: the separation - `driver.py` imports no production module that writes state.
- `python3 tools/verify.py` - 52/52 classes, and the `production-research-separation` class must
  still read `no production import of research`.
- `pytest tests/autopoiesis/test_research_driver.py -q` - the driver test.
- A **real run** on the 20448-bar cache, reported with its actual verdicts. Whatever they are is
  the result; an `OVERFIT` search is a correct search.

## 6. Explicitly not in scope

- Lowering `required_trades`, the admission cap, the probation budget, or any risk limit.
- Adding a strategy kind. `112731f` measured that the existing ones can round-trip.
- Feeding verdicts into production admission. That is a second decision, and it should follow a
  measurement of whether these verdicts are informative - not precede it. If every trial says
  `OVERFIT`, wiring the search into promotion would only add a faster route to the same refusal.
- Changing the production journal, the Guardian, or the shadow path.
---

## Result

Implemented in `src/autopoiesis/research/driver.py` (213 lines) with
`tests/autopoiesis/test_research_driver.py` (11 tests). Gate: **52 classes, 0 failed, 1475 test
executions across 63 files**.

The real run, unattended, on the 20448-bar cache:

```
bars: 20448 from SPY_5Min_2026-05-01_2026-10-03.json (alpaca historical bars; not synthetic)
candidates: 37 (cumulative: 37 already in the ledger)  required_trades: 61  repeats: 11
  search-trend-follow-0p0005    OVERFIT      oos  +0.043% over  141 trades
  search-trend-follow-0p0010    OVERFIT      oos  +0.038% over  102 trades
  search-trend-follow-0p0020    OVERFIT      oos  +0.027% over   67 trades
  search-trend-follow-0p0050    INSUFFICIENT oos  -0.009% over   34 trades
  ... 8 further thresholds, all INSUFFICIENT
  search-fixed-size-buy-hold    OVERFIT      oos  +0.094% over   61 trades
trials_run: 11  passed: 0
```

**Nothing passes.** `OVERFIT` here means the out-of-sample leg is far worse than the in-sample
leg, which is why three candidates with a *positive* out-of-sample return still fail. The
first honest negative from the research gate on real data at proper depth.

### Two defects the driver had on first run

Both were found by writing the falsifier down before the code, and both are the same accounting
class of error as the windowed-probation bug this project fixed earlier.

**The bar reset per run.** `trials` was computed from this search's own candidates, so a ledger
holding 37 trials got the bar for 11 (`required_trades` 34), and a daily driver would buy a cheap
verdict every day. Now `trials` is every distinct trial on record, plus the genuinely new ones.

**Repeats inflated the count.** Recording idempotently was not enough: `already + len(space)`
still counted the repeats, so a second identical run moved `required_trades` from 15 to 20 on a
static dataset with no new candidate tried. `tools/verify.py` caught it first, refusing 11
duplicate `strategy_id`s. The fix was idempotence in the driver - identity is *rule plus dataset*,
because those two determine the result - rather than a looser gate. A test asserting the second
identical run leaves both the ledger and the bar untouched pins it.

## Not achieved, and what follows

Exploration is now autonomous and evidenced. It is **not** successful, and the honest position is
that the rules this project can express have no demonstrable edge on 20448 real bars. Two things
remain open, in order:

1. **Nothing passes the research gate.** Closing that is a search-space question, not a plumbing
   one, and the first measurement of it is in `STATUS.md`: three kinds, two of which trade, both
   long-only. Adding a rule is not authorised and not yet justified.
2. **Verdicts do not reach the admission gate.** Deliberately out of scope here. If every trial
   says `OVERFIT`, wiring research into promotion would only add a faster route to the same
   refusal - so the measurement worth doing first is whether these verdicts ever discriminate
   between candidates, which 37 trials all landing on two verdict codes does not yet show.
