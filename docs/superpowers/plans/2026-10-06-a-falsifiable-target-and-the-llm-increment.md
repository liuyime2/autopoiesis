# Define a falsifiable target, and make the LLM increment measurable

Date: 2026-10-06
Status: **DONE - built and measured, with two of this plan's own claims retracted in section 7**
Baseline commit for rollback: `b97a6b2`
Scope: `tools/benchmark.py` (new), `LICENSE`, and the nine files carrying personal paths. No
change to any risk limit, the Guardian, the strategy library, or the broker.

---

## 0. Why this comes first, and what it costs to skip it

An external review put five problems. Three are engineering debt and two are about verification,
and the order recommended is right: a target nobody can falsify is what allowed the debt.

The review's numbers, re-measured rather than quoted:

```
pnl attribution   +757.95 across 47 closed lots
                  model +193.56   baseline +564.39   (net +817.39 = realized +757.95 + unrealized +59.44)
                  AFTER COST     +799.48 after an assumed 0.05% round-trip cost (17.91)
pnl vs holding    4 of 4 strategies behind the market:
                  tiny-fixed-size-001  +3.94% vs market +5.41%  (-1.47)
                  trend-follow-buy-001 +3.85% vs +5.41%  (-1.56)
                  fixed-size-buy-001    +1.92% vs +5.41%  (-3.48)
                  fixed-size-probe-0001 +1.11% vs +5.41%  (-4.29)
```

(The market leg and the decision counts move as the daemon runs; §7 re-measures them. The
shape of the finding does not: the model share is now +193.56 of +757.95, not +29.94 of +594
— but every strategy that made money lost to holding.)

**And one number the review did not have, which is worse than the one it did:**

```
model registry   970 of 1284 decision(s) (76%) cannot be attributed to a model
                 1284 decisions: qwen3.8:27b 314; 970 predate provenance and are unattributable
```

So the question the review asks — "how much does the LLM contribute?" — **cannot be answered
from the record at all**, for three quarters of the history. A program that cannot measure its own
central claim has no target, and a target it cannot falsify is indistinguishable from having none.

## 1. The target

One sentence, falsifiable, and stated in the units the review asked for:

> Within a stated risk budget and after costs, the system beats SPY buy-and-hold over a rolling
> 60-trading-day window. Reported as a single number with its own denominator, its window, and
> the number of days it has been true for. Separately: the increment attributable to LLM
> decisions versus the deterministic baseline, over the same window and the same fills.

Every clause is load-bearing and each maps to something measurable today:

| Clause | Measured by | Exists today |
| --- | --- | --- |
| stated risk budget | `max_daily_loss`, `max_position_value`, `max_total_exposure`, `max_trades_per_day` | yes |
| after costs | `assumed_round_trip_cost_pct`, and observed broker fees | yes, both reported |
| beats SPY buy-and-hold | `pnl vs holding` | yes — currently **failing on all four** |
| rolling 60 trading days | a windowed comparison | **no** — `pnl vs holding` is whole-of-record |
| LLM increment vs baseline | `pnl attribution` model/baseline split | partially — see §2 |

**Deliberately not in the sentence:** "positive return". Beating buy-and-hold is the claim; a
positive return in a rising market is not evidence of anything, and every strategy here already
has one.

**What the number is today:** 4 of 4 strategies behind the market, so **the target is currently
failed, and failed by 1.56 to 4.39 percentage points.** Writing that down is the point of this
plan — a target that is currently failing is still a target, and it is the only kind that can
change what the program does.

## 2. The gap that makes the LLM clause unmeasurable

`pnl attribution` splits model from baseline over the whole record. Two things stop it answering
the question:

1. **76% of decisions are unattributable.** They predate provenance capture. Back-filling them
   would be inventing data; leaving them is honest but means the split describes the labelled
   quarter only.
2. **The split is not a counterfactual.** "Model contributed +193.56" means the model made the
   decisions on the lots that closed with a profit. It does not mean the baseline would have lost
   that money on the same bars — the decision changed which lot existed, so no paired comparison
   was run.

The honest form of the increment is therefore narrower than the review asks for and must say so:
*of the P&L the system actually produced, how much sits on decisions that can be attributed to
the model, and what is the share of decisions that can be attributed at all.* Both numbers, always,
together. A split without its coverage is the kind of figure this project has retracted twice.

## 3. What gets built

One script, `tools/benchmark.py`, answering the sentence in §1 against the record that exists.
As built it prints:

```
record            2026-06-09 .. 2026-10-06   16 trading day(s), 1284 cycle(s)
target window     60 trading days - NOT MET (16 of 60)
comparison        whole of record (16 trading day(s)); no windowed comparison exists, and this
                  does not fake one
risk budget       max_position_value $5,000  max_daily_loss $500  max_trades_per_day 10
cost              assumed 0.05% round trip; observed broker fees below

vs SPY buy-and-hold
  strategy                     return    vs SPY
  tiny-fixed-size-001          +3.94%     -1.47
  trend-follow-buy-001         +3.85%     -1.56
  fixed-size-buy-001           +1.92%     -3.48
  fixed-size-probe-0001        +1.11%     -4.29
  buy-and-hold (SPY)           +5.41%       ---   <- the benchmark to beat

LLM increment vs the deterministic baseline
  +757.95 across 47 closed lot(s), of which the model contributed +193.56 and the baseline +564.39
  AFTER COST: +799.48 after an assumed 0.05% round-trip cost (17.91); observed broker fees 0.00
  ... the seven cause verdicts, the regime split, and the unmatched-sell clause

attribution coverage   970 of 1284 decision(s) (76%) cannot be attributed to a model

VERDICT           FAIL: 4 of 4 strategies are behind buy-and-hold, worst tiny-fixed-size-001
                  by 1.47 points, over the 16 trading days on record; and the 60-day window
                  is not covered, so over 60 days the target is neither met nor falsified yet
```

`make benchmark` runs it. Exit codes are the contract: `0` target met, `1` target failed,
`2` nothing to measure. It needs no credentials and no network — `doctor` runs with
`skip_broker=True` and every number is read out of what is already on disk.

## 4. The second half, which needs no plan: personal paths

Nine tracked files carried `liuyime2`, `/localscratch`, `/home/...`, or the git author name:

```
LICENSE                                                  1   Copyright 2026 liuyime2
docs/evidence/fresh-clone.log                            41
docs/superpowers/SYSTEM_AUDIT.md                         9
STATUS.md                                                6
docs/superpowers/plans/2026-09-28-quantgroup-recovery... 4
minictrl                                                 3   conda discovery paths
tools/verify.py                                          3   two are the gate's own host_markers list
tools/ollama.service.in                                  2
docs/superpowers/specs/2026-09-28-min-agent-runbook.md   1
```

Two categories, handled differently:

- **Real coupling** (`minictrl` conda discovery, `ollama.service.in` weight path, the runbook's
  `CONDA_BIN`, LICENSE) — replaced with discovery or an env var, so a fresh clone works on another
  host rather than carrying this one's layout. `minictrl` now also reads `OLLAMA_BIN` and
  `OLLAMA_MODELS` out of the operator's env file and *refuses* to write a unit without them,
  rather than writing one that fails 40 minutes later when systemd next starts it.
- **Deliberate references** (`tools/verify.py` lists `/home/`, `/localscratch/`, `/Users/` as
  `host_markers` it *scans for*) — those stay. A privacy scan that could not name what it looks
  for would be worse than one that names this host's paths.

`runtime/` is already gitignored with zero tracked files, so the review's "runtime/ 里的痕迹" is
not a tracked-file problem. `fresh-clone.log` is the real instance — and it is a *transcript*,
which §7 retracts the plan's proposal to rewrite.

## 5. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The target is currently failed | any of the four strategies beating buy-and-hold in the window |
| The LLM split needs its coverage | the benchmark printing a model share without the attribution rate |
| Personal paths are removable | the benchmark or `make verify` failing on another host's layout |
| Host-marker scanning survives | the privacy check losing the ability to name what it looks for |
| No risk limit moved | any diff to the Guardian or the configured limits |

All five held. The first holds only over the record that exists — see §7.

## 6. Explicitly not in scope

- The DSL, the short side, a larger universe, or multiple timeframes. Those are the review's
  biggest lever and they are capability work that needs its own plan; this one makes the
  *measurement* exist so that plan can be judged against a number.
- The 29 unmatched sells. It is a real gap and it is an account-history problem; fixing it by
  back-filling attribution would be inventing data. Recorded, not papered over.
- Slimming `doctor.py` / `verify.py` / the document set. Agreed and real, and the wrong order: it
  is easier to judge what to delete once there is a benchmark to justify keeping.
- Making the attribution coverage 100%. That requires decisions that have not happened yet.
- **Building the windowed comparison.** §1 listed it as the one clause with no implementation,
  and building it is the obvious next step. It is not in this plan because §7 found that the
  journal holds 16 trading days: a 60-day window computed over a 16-day record is not a window,
  it is a division that hides the denominator. The window becomes buildable when the record is
  long enough for one to mean something, and until then the honest output is the one printed —
  the record's real window, and the target window reported as not met.

## 7. What building this found, including two claims of this plan that were wrong

**7a. The first draft of `benchmark.py` printed a window it was not measuring.** It took the
date range from the replay cache and printed it above returns computed over the whole journal:

```
window            2026-07-13 .. 2026-10-05  (60 trading days, target 60)   <- the cache
strategy            return   vs SPY
  tiny-fixed-size-001=+3.94% vs market +5.41% (-1.47)                        <- 16 days of cycles
```

Exactly 60. A number that lands on the target by coincidence, on a different window than the
figure beneath it, is the worst kind of decoration: it makes a 16-day record look like a
60-day test. Measured from the journal:

```
records        1284, 2026-06-09 .. 2026-10-06, 16 distinct trading days
closed lots    47, on 4 of them: 2026-09-28, 10-01, 10-02, 10-06
```

So the replay cache is deleted from the tool, the record's own window is printed, the target
window is reported as `NOT MET (16 of 60)`, and the verdict says both facts at once. The
`pnl vs holding` comparison is whole-of-record because that is all there is, and the tool says
so rather than computing a window over it.

**7b. `docs/evidence/fresh-clone.log` must not be rewritten.** §4 proposed rewriting its 41
paths. It is a transcript of what `pip` and `pytest` printed; substituting `$HOME/...` for
`/home/liuyime2/...` would make the evidence a fabrication, which is the one thing AGENTS.md
forbids outright. Two honest options exist and only one was taken:

- keep it, and accept that a committed log names the account that produced it, or
- re-run `docs/evidence/run-fresh-clone.sh`, which regenerates it against the committed tree.

The rewrite was reverted; the log is regenerated by running the script, never by editing.

**7c. The "five-minute demo on a fresh clone" is not true and is not claimed.** The benchmark
needs `runtime/`, which is gitignored because it is produced by trading. A fresh clone has no
journal, no evidence and no lots, so the tool prints that there is nothing to measure and exits
`2`. What it *is* good for is a reviewer with read access to a checkout: no credentials, no
network, one command, a number with its denominator.

**7d. Two smaller defects in the same file, found by running it.**

- `doctor` legitimately emits `pnl attribution` **twice** — unmatched sells and a model that
  subtracted from a positive total are independently reportable — and the first draft printed
  the whole PnL paragraph twice. It now prints the shared paragraph once and each check's own
  clause.
- The split on `"; "` was wrong for the `pnl vs holding` sentence: its rows are comma-separated
  *inside* one sentence, so the parse yielded the sentence and every filter on it dropped
  everything. That is why the first draft's table never rendered.