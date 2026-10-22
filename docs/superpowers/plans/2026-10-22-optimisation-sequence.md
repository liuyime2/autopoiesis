# 2026-10-22 — the optimisation sequence, in leverage order

The first-principles review of 2026-10-22 produced three measured findings and an
ordered list of six actions. This is the plan for that list. Every item states the
problem, the measured baseline, the success criterion and how it will be verified
before any code is touched (AGENTS.md 1).

The ordering is by **leverage on the objective** (a working, honest, eventually
profitable agent), not by technical difficulty.

| # | Action | Kind | Can finish tonight |
|---|---|---|---|
| 1 | Stop deciding symbols nothing can act on | code | yes |
| 2 | Stop re-parsing 2.4 days of journal per cycle | code | yes |
| 3 | Make the coverage gap visible and steerable | code + docs | yes (diagnosis; the strategies themselves need admission) |
| 4 | R3: freeze a falsifiable hypothesis with a prior | research contract | yes (the contract; the run needs the data to exist) |
| 5 | R4: controlled RSI runner | code + elapsed rounds | no |
| 6 | R5/R6: promotion, assets, release | elapsed time / product | no |

---

## 1. Round priority must mean "a decision here can change something"

**Problem.** `_prioritise_round` treats a *configured* symbol as priority, so a
symbol with no serving strategy and no position is decided every round. Its own
docstring says the opposite ("Everything else — a pick the model made, or a
configured symbol with no serving strategy and no position — still appears, and
still gets decided, but on a rotation"). The code and its stated contract disagree,
and the code is the defective half.

**Baseline (measured 2026-10-22, live session).**

- 118 cycles, of which **61 (52%)** were `UNKNOWN_PROVENANCE`; **51 of those** were
  XLE 16, XLF 16, XLB 13 — configured symbols with no serving strategy at all.
- Serving strategies cover only **BIL 5, TLT 4, SPY 4, QQQ 1**.
- Per-symbol decision interval: **~12 minutes** (116 cycles / 240 minutes, six
  symbols per round), against the review's **≤10 minutes** requirement.

**Success criterion.** The round consists of `served ∪ held` every round plus the
rotating discovery slot; a configured-but-unserved symbol appears on rotation, not
every round. The round shrinks from ~6 to ~4 symbols; combined with item 2 the
per-symbol interval must be **≤10 minutes**.

**Verification.** A unit test that pins the new contract *with a configured symbol
that nothing serves* (the old test never set `config`, so the `configured` clause
was dead in tests and only live in production — which is how it survived). Then the
live round composition on 2026-10-23: no `no_strategy` decision for a symbol that
is not held and has no serving strategy more than once per rotation.

---

## 2. The in-flight guard must not read all history to answer a 30-minute question

**Problem.** `AgentLoop._identical_order_already_in_flight` calls
`journal.read_events()` with no bound. That parses the whole journal. The daemon
appends to the journal every cycle, so the parse cache's signature changes every
cycle and the cache **never** hits.

**Baseline (measured 2026-10-22).**

- `CycleRecord.timings` over 114 cycles: `cycle_ms` **75,074**, `decision_ms`
  **52,619** (70%), `snapshot_ms` 302, `execution_ms` 39, **remainder 22,114 ms
  (29%)**.
- Cold `read_events()` on the live journal: **15.78 s / 26,673 events**; the same
  call against an unchanged signature: **0.025 s**.
- `IN_FLIGHT_WINDOW_MINUTES = 30`; journal span **2.4 days / 56.1 MB**.

**Success criterion.** The guard reads at most the tail covering its window
(30 minutes ≈ 0.5 MB; read 2 MB = 4× margin) and costs **<1 s** instead of 15.8 s;
`cycle_ms` falls by ≈15 s; the guard's *decisions* are unchanged (same submissions
seen, same duplicates refused).

**Verification.** A journal test that a windowed read returns exactly the events a
full read filtered by the same bound returns (equality, not a spot check), on a
journal that spans more than the window; a test that a torn last line and a
rotation are handled; the `timings` after the next live session.

**Design (chosen for the smallest change that keeps one source of truth).**
`JsonlJournal.read_events(..., since=...)` gains a tail scan: read backwards from
EOF in 1 MiB blocks, decode complete lines, stop once a record is older than
`since`, then parse only those lines. It shares the row decoder with `_scan`, so a
windowed read and a full read of the same event cannot disagree. No cache is
involved: the call is already cheap enough that a cache would be a second place to
be wrong.

---

## 3. Coverage gap: configured symbols with no serving strategy

**Problem.** XLB, XLE and XLF are in the configured universe and none has ever had
a serving strategy; BIL has five serving strategies and 16 of 18 decisions are
NEUTRAL, so it is structurally unscorable. Together they consume the majority of
the model's decisions and can produce neither evidence nor orders.

**Baseline.** Library census 2026-10-22: 116 specs — PAUSED 59, RETIRED 43,
PROBATION 12, ACTIVE 1, BASELINE 1. Serving by symbol: BIL 5, QQQ 1, TLT 4, SPY 4.

**What this item can honestly deliver.** A strategy cannot be hand-written into the
library (AGENTS 14 / the admission gate). What is deliverable is:

1. a command that reports, per configured symbol, whether a decision there can
   produce evidence or an order, and what the last proposal was;
2. the gap recorded in the docs with the number, so "we watch XLE" is no longer
   mistaken for "XLE is covered";
3. after item 1, the cost of the gap stops being paid every round.

The strategies themselves are item 4's job: they must enter through admission.

**Verification.** `make universe-coverage` on this host prints the measured census
and exits non-zero when a configured symbol has no serving strategy *and* no
position *and* no recent proposal — a loud gap, not a silent one.

---

## 4. R3 — freeze the hypothesis before running it

**Problem.** 165 trials, **0 PASS**: INSUFFICIENT 108, OVERFIT 46,
INCONCLUSIVE_NO_FOLDS 11. The binding constraint is statistical power, and 65% of
verdicts are the honest "cannot tell". A search without a prior and without a
frozen holdout cannot convert that into knowledge.

**Deliverable.** A pre-registration that is *frozen by content hash* before the
first run: hypothesis family, economic prior, data source and content hash, cost
model, development window vs **sealed** holdout, candidate budget, minimum
independent sample, test statistic, and stopping rule — plus the rule that the
holdout is opened at most once and that a negative result closes the round.

**Success criterion.** The contract can be validated offline
(`make research-prereg`): the frozen hash matches, the holdout is disjoint from
development, the candidate budget is not exceeded, and the cost model is explicit.

---

## 5–6. R4, R5, R6

Not startable tonight, and saying so is the honest answer: R4 needs R3's contract
plus rounds of elapsed time, R5 needs 60 consistent-policy trading days, R6 needs a
stable evidence contract. They are carried forward with that reason rather than
dropped.
