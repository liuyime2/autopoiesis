# The multiple-testing gate demands 52 bets and the fold count is hardcoded at 3

Date: 2026-10-04
Status: **DONE - the gate opens and returns real verdicts on 24,270 bars; required_trades unchanged at 52 for 27 trials**
Baseline commit for rollback: `3407ebc`
Scope: `src/autopoiesis/research/walk_forward.py` and its tests. No change to
`required_trades`, `BASE_REQUIRED_TRADES`, the screen, or any production module.

---

## 0. Correcting the previous finding first

Two entries in this record said the research gate needs roughly **8,800 bars** — about 10x
the replay cache — before it could be satisfied. **That was wrong**, and it was wrong in a way
that pointed at the wrong remedy. Measured on the real 847-bar cache, feeding the
walk-forward different amounts of data:

```
bars fed   folds   oos trades   required
      100      3             3         52
      200      3             3         52
      400      3             3         52
      847      3             3         52
```

`out_of_sample_trades` does not move with the dataset at all. Gathering more history would
not have fixed it, so "collect more bars" was the wrong answer and had to be withdrawn before
anything was built on it.

## 1. The actual invariant

`run_backtest` opens a position only when flat:

```python
if decision.action == "BUY" and position == 0:      # open
elif decision.action == "SELL" and position > 0:     # close, count one win or loss
else:                                                # mark to market
```

An always-BUY rule therefore makes **one contiguous holding period per test segment**, and the
open position is force-closed at the segment's last bar, so each segment yields exactly one
counted trade. That makes `out_of_sample_trades == n_folds` an identity, not an approximation
— confirmed at every fold count tried:

```
bars  n_folds   required  test_size  oos trades  verdict
 847        3         52        275           3  INSUFFICIENT
 847       10         52         82          10  INSUFFICIENT
 847       27         52         30          27  INSUFFICIENT
 847       52         52         15          52  OVERFIT
 847       60         52         13          60  OVERFIT
```

**So the gate is satisfiable only when `n_folds >= required_trades`, and `n_folds` is
hardcoded at 3.** The module demands 52 independent out-of-sample bets and provides 3. Every
one of the 26 recorded trials hit that contradiction, which is why all 26 are `INSUFFICIENT`
and why none has ever been anything else.

Note the last two rows: at `n_folds == required_trades` the gate stops vetoing and returns a
**real verdict — `OVERFIT`**. The stage was not failing to find edge; it was structurally
prevented from reporting the answer it had.

## 2. Why this is not lowering the bar

The multiple-testing control is sound and stays exactly as it is. `required_trades` still rises
as `sqrt(trials)`, so searching harder still demands proportionally more evidence.

What changes is that the evidence now gets produced. A gate that provably cannot be passed is
not a control, it is a veto, and this repository already states that principle where the
promotion gate lives: *"The gate must be satisfiable. A gate that nothing can pass is a
freeze, and would silently stop the lifecycle from ever advancing."* The same sentence
applies here, and this gate has been the freeze the whole time — 26 trials, 0 verdicts.

## 3. The change

1. `n_folds` defaults to `None`, which means **derive it from `required_trades`**:
   `max(3, required)`. The number of independent out-of-sample bets a run can produce is the
   fold count, so the fold count is what the control has to be told about.
2. An **explicitly** passed `n_folds` below `required_trades` is respected — a caller asking
   for a cheap run gets one — but a note is recorded saying the evidence gate cannot be
   satisfied at that fold count, so the reason is on the record rather than inferred from a
   permanent `INSUFFICIENT`.

Silent `max()` is the alternative and is wrong: it would override an explicit caller without
telling them, which is the failure mode this repository has already been bitten by twice today
with windowed counts substituted for cumulative ones.

## 4. What this does and does not do

It makes the stage able to return a verdict. On the current cache that verdict is `OVERFIT`,
which is a real answer — this strategy does not generalise.

It does **not** make the search find an edge, and it does not fix segment quality: at 52 folds
on 847 bars each segment is 15 bars, so each bet is short. That is a genuine weakness and the
reason more data still matters — 52 segments of ~100 bars needs about 5,220 bars, roughly 7x
the cache — but it is a quality question *after* the count, not the reason the gate never
opened.

| Prediction | Falsifier |
|---|---|
| A run now returns something other than INSUFFICIENT | Default-`n_folds` run still INSUFFICIENT at 27 trials |
| The control still bites | A run at 27 trials passing on 3 folds |
| The bar is unchanged | `required_trades` differing from `ceil(10*sqrt(trials))` |
| Short segments are visible | `test_bars` per fold not reported, so the weakness stays hidden |

## 5. Tests

- Default `n_folds` derives from `required_trades`, so `oos_trades >= required_trades` and the
  count gate cannot fire on a default run at 27 trials.
- `required_trades` is unchanged — asserted against the formula, not against a value the new
  code also produces, so a future edit cannot quietly lower the bar.
- An explicit `n_folds` below the requirement still runs, and records a note saying the gate
  cannot be satisfied there.
- `oos_trades == n_folds` for an always-BUY rule at several fold counts — pinning the
  identity this fix is built on, so a future change to the backtest's re-entry behaviour is
  caught here rather than discovered as a permanently frozen gate.

## 6. Verification

- `ruff`, `mypy`, `test_research_backtest.py`, then the full `make verify`.
- The behavioural claim — a default run returning a verdict other than `INSUFFICIENT` — is
  checked by the first test and demonstrated on the real cache.
---

## Result (recorded late; the work was done in `d60b7c6` and the status line was never closed)

`n_folds=None` now derives the fold count from `required_trades`, so the stage provisions the
evidence it counts instead of demanding it and never getting it. Re-derived from the code as it
stands:

```
BASE_REQUIRED_TRADES = 10
  required_trades( 1) = ceil(10 * sqrt( 1)) = 10
  required_trades(10) = ceil(10 * sqrt(10)) = 32
  required_trades(27) = ceil(10 * sqrt(27)) = 52
  required_trades(40) = ceil(10 * sqrt(40)) = 64
```

**The bar is unchanged** — 52 at 27 trials, exactly as the plan titled itself. Nothing was lowered;
the fold count moved.

The predicted outcome has since been demonstrated on real data, which the plan could not do at the
time: the gate opens and returns verdicts rather than `INSUFFICIENT`. On 24,270 real bars with 59
cumulative trials and `required_trades: 77`, three candidates clear the evidence bar and all three
return `OVERFIT`.

That is the honest end of this plan. The unsatisfiability is gone and the answer it was blocking
is a real one: three rules clear 77 out-of-sample trades and none generalises.

**Why the status line said PLAN for two days.** The plan was written, implemented, committed and
then never revisited - the work moved on to the next defect. A plan document's status is only as
current as the last time somebody read it, which is the same failure as every stale measurement in
this project: something true when written, untrue later, and nobody looking. The sweep in this
session is what caught it, by comparing every `Status:` line against the commits rather than
against memory.
