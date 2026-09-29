# Fact-level system audit — QuantGroup `min_agent`

Audited 2026-09-29 against the working tree at `ae7e78a`+ and the live runtime
under `runtime/min_agent/`. Every claim below was checked against code or against
the real journal, not inferred from names.

**Rule applied throughout:** existence ≠ effectiveness. A module, function, config
key, test or document is *not* counted as working unless it demonstrably changes
production behaviour. Claims that could not be verified are marked
**UNVERIFIED** rather than assumed.

---

## 1. The single real end-to-end flow

One process. `cli.py:main` → `_run_daemon` → `AgentDaemon.run()` (`daemon.py:95`).

```
run():
  _acquire_lock(); SIGTERM/SIGINT handlers
  _startup_reconcile()                      # orders in flight vs broker
  loop:
    _reset_daily_count_if_needed()
    _maintenance()                          # every 900s
      _resolve_pending_fills()              # journal SUBMITTED -> broker poll
      _ingest_broker_evidence()             # every 3600s
      _record_pnl_evidence(evidence)        # -> PnL_EVIDENCE_RECORDED
      _verify_profit_target(evidence)       # -> PROFIT_TARGET_CHECKED
      _reflect(evidence)                    # -> REFLECTION_GENERATED
      _curriculum_proposal()                # -> CURRICULUM_PROPOSED
      _manage_strategy_lifecycle()          # -> STRATEGY_LIFECYCLE_UPDATED
    if market closed: sleep, continue
    for symbol in config.symbols:           # currently ('SPY',)
      _run_symbol(symbol) -> TradingLoop.run_once()
        data_gateway.snapshot(symbol)       # real broker positions/orders/account
        decision_engine.decide_snapshot()   # HybridDecisionEngine -> OllamaDecisionEngine
        guardian.review(decision, snapshot) # 14 gates, immutable
        executor.execute(...)               # AlpacaPaperExecutor
        journal.append(CycleRecord)
    sleep(interval 300s)
```

**Execution truth is the broker.** `data_gateway.py` raises rather than degrading
to an empty position book, and `fill_reconciler` re-polls orders to a terminal
fill state. The journal is the record; the broker is the truth; `doctor` compares
them.

---

## 2. Component classification

### Actively used (verified changing production behaviour)

| Component | Evidence |
|---|---|
| `models.py` | every record type; 920 cycles parse from the live journal |
| `journal.py` | 19.6 MB append-only, 23 test files reference it |
| `data_gateway.py` | real positions/orders; 6 positions, 0 open orders read live |
| `loop.py` | the only cycle path |
| `guardian.py` | 14 gates; has actually rejected 36 orders in the live record |
| `executor.py` | 24 SUBMITTED orders, 24 broker-confirmed fills |
| `evaluator.py` | produced the 3-strategy per-strategy PnL, `+564.39` total |
| `fill_reconciler.py` | converted the 52-share SELL to a confirmed fill at 766.57 |
| `broker_evidence.py` | `status: SUCCESS`, portfolio history verified |
| `strategy_engine.py` | 27 strategies, 11 lifecycle decisions journaled |
| `llm_decision.py` | 67 `source=llm` decisions in the live record |
| `curriculum.py` | 222 proposals; the model closed its own SELL capability gap |
| `strategy_admission.py` | 5 accepted, rest rejected with reasons |
| `reflection_memory.py` | drives the lifecycle manager's input |
| `config.py` / `cli.py` / `daemon.py` / `health.py` / `atomicio.py` | the loop itself |
| `trade_counter.py` / `scheduler.py` / `order_reconciler.py` | wired into `cli.py` |
| `knowledge_admission.py` / `knowledge_library.py` | write path fires (see #3 for the read path) |

### Partially wired

| Component | What works | What does not |
|---|---|---|
| `policy_engine.py` | selector + fallback decision path | `relevant_lessons()` filter is **dead** — see #3 |
| `knowledge_*` | artifacts written and admitted | 11 artifacts, all near-identical; carry ~zero information |
| `doctor.py` | 25 checks incl. the 4 new governance checks | no `make verify`; not invoked by any gate |

### Implemented but unused / dead

| Item | Evidence | Classification |
|---|---|---|
| **`src/voyager_quant/`** (8 files) | **zero** references outside itself; `main.py:93` does `exec(code, ...)` on LLM-generated Python | **OBSOLETE + dangerous** — delete |
| `tools/legacy/*` (12 files) | 10 of 12 referenced by nothing; all import, `full_ingest` is **broken** (`append_event()` kwarg mismatch, would write the live journal) | **OBSOLETE** — delete |
| `daemon.py:92-93` `_last_reflection_window_hash` / `_last_curriculum_window_hash` | declared, never read or assigned. The "skip when the window hasn't changed" behaviour the comment describes **does not exist** | **DEAD STATE** — delete |
| `strategy_engine.py:70,148` `min_active_submitted_orders` / `min_probation_submitted_orders` | assigned, never read; tests pass them as if enforced | **DEAD PARAMETER** — delete |
| `cli.py:493` + `daemon.py:502` `_latest_evidence_batch` | two independent copies of the same function | **DUPLICATED** — merge |
| `doctor.py:571` | third inline copy of the same read | **DUPLICATED** — merge |
| `minictrl` / `min-agent.service.in` model pin | were divergent from `config.py`; now test-guarded | **REPAIRED** |

### Broken

| Item | Evidence |
|---|---|
| `tools/legacy/full_ingest.py` | `TypeError: JsonlJournal.append_event() got an unexpected keyword argument`. `tools/README.md` claims it appends to the production journal — **the README is wrong** |
| `policy_engine.relevant_lessons` | never matches (see #3); silently falls through to a fixed set |

### Missing (nothing exists at all)

Grepped for each; **no source, no test, no doc**:

- **Counterfactual evaluation.** `counterfactual`, `good_hold`, `missed_alpha`,
  `avoided_loss`, `false_trade`, `hypothetical` → 0 hits. There is no way to ask
  whether a HOLD was correct.
- **Slippage / commission.** `slippage`, `commission` → 0 hits. Live fees across
  all 24 fills sum to **0.0**, so the cost code path has never executed with a
  non-zero value in production.
- **Experiment / trial registry.** `experiment`, `trial`, `walk_forward`, `oos`,
  `out_of_sample`, `backtest` → 0 hits outside the word "trial" in a comment.
- **Champion–challenger.** 0 hits.
- **Strategy lineage.** 0 hits.
- **Semantic duplicate detection.** Only *id* duplication (`strategy_admission.py:39`)
  and *parameter* duplication for TREND_FOLLOW (`:146`). Two FIXED_SIZE strategies
  differing only in `quantity` both pass.
- **Point-in-time / leakage guard.** No check that a decision used only data
  available when it was made.
- **`make verify`.** No Makefile exists. The objective names 13 check classes;
  they are scattered across pytest, `tools/audit_defects.py`, and `doctor`.

---

## 3. The three findings that matter most

### 3.1 The knowledge base's relevance filter is dead logic

`policy_engine.py:78-84` keeps an artifact when
`strategy_id in artifact.source_refs or strategy_id in artifact.tags`.

Checked against the live library:

```
source_refs that are strategy_ids : NONE
tags that are strategy_ids        : NONE
distinct tags : ['all-hold', 'exploration', 'lifecycle', 'probation']
```

`source_refs` hold cycle UUIDs and `tags` hold generic words. The condition can
**never** be true, so `relevant` is always empty and the fallback fires every
time: *every decision sees the same first 5 artifacts regardless of strategy.* The
11 accepted artifacts are all variants of "No-exploration window detected", so
what actually reaches the model is five near-identical sentences.

This is the "configured but not effective" case the objective warns about.

### 3.2 The strategy zoo is accreting, and the duplication check is too narrow

27 strategies: 2 ACTIVE, 10 PROBATION, 10 PAUSED, 4 RETIRED, 1 BASELINE.
13 selectable, of which **9 are FIXED_SIZE/BUY** — near-duplicates differing only
in `quantity` and `confidence`. `capability_coverage` only counts an action as a
gap when one strategy supplies it, so once SELL was covered the engine saw no
further gap and the curriculum kept proposing BUY variants.

Nothing deduplicates semantically or behaviourally.

### 3.3 Cost accounting has never been exercised

`fees` is threaded correctly through the lot ledger and prorated across split
fills, but every broker-reported fee in the live record is `0.0`. So the cost
branch is *implemented and unproven*, and any claim about net-of-cost PnL today
is a claim about a code path that has never run with real data.

---

## 4. Where the loop actually breaks

The production chain is intact through to `fill → PnL attribution` (proven:
+564.39 across 3 strategies). It breaks in three places, in order of severity:

1. **After PnL attribution.** The objective's chain continues
   `→ counterfactual evaluation → reflection → hypothesis → candidate →
   offline validation → shadow → probation → promote`. **None of
   counterfactual evaluation, offline validation, or shadow trading exists.**
   So the loop currently stops at a number and restarts, which is why 864 of 920
   cycles are HOLD with no way to learn whether they were right.

2. **Reflection carries no hypothesis.** `ReflectionRecord` is a metrics blob
   (`models.py:650-662`): counts, scores, per-strategy metrics. There is no field
   for a diagnosis, a hypothesis, or a trial, and no LLM ever authors a
   `KnowledgeArtifact` — the only constructor in `src/` is `daemon.py:605`, with
   content drawn from five hard-coded strings.

3. **Lifecycle transitions are not evidence-gated on the counterfactual the
   objective requires.** The only PnL-driven transition is verified-realized-PnL
   sign; everything else keys on cycle counts and rejection rates.

---

## 5. Disposition: delete / merge / simplify / repair / keep

Applying `delete → merge → simplify → reuse → repair → add` strictly:

| Action | Target | Rationale |
|---|---|---|
| **DELETE** | `src/voyager_quant/` (8 files) | zero references; contains `exec()` on model-generated code; a live capability that nothing uses and that violates the no-code-execution constraint |
| **DELETE** | `tools/legacy/` (12 files) | 10/12 unreferenced; `full_ingest` broken and aimed at the live journal; all superseded by `minictrl`/`doctor` |
| **DELETE** | `daemon.py` two window-hash fields | dead state whose comment describes behaviour that does not exist |
| **DELETE** | two dead `min_*_submitted_orders` params | assigned, never read; tests imply they are enforced |
| **MERGE** | three copies of `_latest_evidence_batch` | one function, one place |
| **MERGE** | `_run_daemon` + `minictrl` entry points | more than one runtime entry point is how the model pin diverged before |
| **REPAIR** | `relevant_lessons` | make the filter actually match, or delete it and say so |
| **REPAIR** | `tools/README.md` claim about `full_ingest` | factually wrong |
| **SIMPLIFY** | the 9 near-duplicate BUY strategies | a dedupe rule at admission, then retire the duplicates by evidence |
| **REUSE** | the existing `PnLEvidence`/`ClosedLotAttribution` structure | it is the one genuinely good substrate; counterfactual work should extend it, not rebuild it |
| **KEEP** | `guardian.py` unchanged | immutable, 14 gates, machine-checked bypass prevention |
| **ADD (last)** | counterfactual ledger, then `make verify` | per the stated ordering: close the loop, then correctness, then research, then autonomy, then capital |

---

## 6. Honest statement of what is proven vs designed

**Proven by real broker evidence:**
- 24 submitted orders, 24 broker-confirmed fills, `fill_quantity_ratio` 1.0
- 23 closed lots; per-strategy realized PnL `tiny-fixed-size-001 +362.66`,
  `fixed-size-buy-001 +120.37`, `trend-follow-buy-001 +81.36`
- The self-evolution chain ran unattended: curriculum proposed a SELL strategy →
  admission accepted it → selector served it → LLM emitted SELL 52 → Guardian
  approved → broker filled at 766.57 → lots closed → PnL attributed

**Designed but not proven:**
- That any of it is *profitable after costs* — fees have never been non-zero, so
  "net" is unmeasured
- That the HOLDs are correct — no counterfactual exists
- That the 27 strategies are distinct — only id/parameter duplication is checked
- That the model reasons better than the fallback — 67 LLM decisions vs 2 fallback
  are not a comparison

**Not started:** counterfactual evaluation, experiment registry, offline
validation, walk-forward, shadow trading, champion–challenger, regime awareness.

---

## 7. The verification gate (`make verify`)

Added 2026-09-28. Before this, the checks were scattered across `pytest`,
`tools/audit_defects.py` and `doctor`, and nothing ran all of them, so "the tests
pass" was never the same claim as "the system is verified". `make verify` runs
every class and counts them:

| class | what it proves |
| --- | --- |
| `software-supply-chain` | no credential is committed; no tracked env file |
| `syntax-import` | every module byte-compiles and every module imports |
| `unit-integration` | the general test surface |
| `data-integrity` | runtime state parses; journal and registry agree |
| `point-in-time-no-leakage` | nothing is scored with a price from the future |
| `pnl-accounting` | account-level FIFO, opener/closer attribution, cost |
| `lifecycle-invariants` | admission, dedupe, promote/pause/retire, knowledge value |
| `guardian-bypass-prevention` | the risk envelope is machine-checked, not assumed |
| `replay-determinism` | the same inputs reproduce the same outputs |
| `crash-recovery` | torn writes, restarts, journal-first ordering |
| `broker-reconciliation` | orders, fills, and evidence against the broker |
| `shadow-live-consistency` | live mode is hard-blocked; probation is enforced |
| `decision-outcome-counterfactual` | holds are scored, and refusals are not counted as scores |

Two supporting classes guard the gate itself: `test-coverage-map` fails if any test
file is not assigned to a class, and `class-coverage` fails if a declared class has
no tests behind it. Without them, adding a test quietly moves the system from
"verified" to "not checked".

### The gate can fail

`make verify-self-test` breaks the gate on purpose and asserts it reports failure:
a failing test, a test importing a name that does not exist, a class with no tests,
an unclassified test file, and a class with every test file removed. A verification
command that cannot fail is believed rather than run.

The self-test caught a flaw in itself: it added the canary to the class map in its
first step, so the later "unclassified file" assertion could never fire and reported
a false pass. Each check now puts the system into the state it claims to test.

Current: **17 classes, 0 failed, 447 distinct tests, 91/91 defect audit,
doctor RESULT OK.**

## 8. Findings from building the gate

### 8.1 Six admitted strategies have no file and no recorded retirement

`tools/check_runtime_integrity.py` found that six strategies were journalled
`STRATEGY_ADMISSION_REVIEWED accepted=true` but have no file in the strategy
registry and no `RETIRED` lifecycle event naming them. The journal and the registry
are two sources of truth about the same thing, and for these six they disagree.

`runtime/` is gitignored, so there is no commit history from which to reconstruct
when or why the files were removed. The cause is recorded as **UNKNOWN** in
`docs/superpowers/known_state_findings.json` rather than guessed, and no synthetic
retirement event was written — inventing one would put a false claim into the audit
trail that the whole system is meant to rest on.

This cannot recur: `strategy_admission.admit()` writes the spec atomically, under a
file lock, *before* returning `accepted=True`, and the daemon journals the event only
after `admit()` returns. A journalled acceptance therefore implies a durable file.

### 8.2 Shadow trading does not exist

The objective names shadow/probation as a lifecycle stage. Probation is real: a
strategy needs `min_probation_cycles=3` before it is tradable. **Shadow is not** —
the word appears in docstrings and a plan, and nowhere in code. The
`shadow-live-consistency` class therefore asserts the two things that do exist (live
mode is hard-blocked at config load, probation is enforced) and prints that shadow
mode is not implemented, rather than passing a check that cannot yet be written.

### 8.3 A checker that cries wolf is worse than no checker

The first three versions of the integrity checker reported failures that did not
exist, each from inventing a schema instead of reading it:

* it required `entry_rules`/`exit_rules`/`risk_rules`, which `StrategySpec` has
  never had — the schema is `kind` + `parameters`;
* it treated a journal *event* carrying a `cycle_id` foreign key as a duplicate
  cycle, reporting 228 phantom double-writes;
* it rejected `BASELINE`/`PROBATION` as unknown lifecycles and required
  `parameters` to be non-empty, when a `HOLD_BASELINE` legitimately has none.

It also crashed with `ValueError` when the runtime tree sat outside the repo. Every
rule is now named against the model field it derives from, and
`tests/min_agent/test_runtime_integrity.py` plants each violation to prove the
checker still fails — including a *new* admitted-without-file strategy, so the
exception list cannot become a blanket disable.

## 9. The two loop stages that did not exist

Auditing all sixteen named stages against the code found exactly two missing, and
one of them was missing in a way that would have been expensive to add wrongly.

### 9.1 Offline validation — missing, and the obvious implementation would have been fake

The chain named `hypothesis -> candidate -> offline validation -> shadow/probation
-> live validation` had no middle. A candidate went from admission straight into
PROBATION and then traded real (paper) orders, with the Guardian as the only gate.

The natural implementation is to replay the strategy's own rule against historical
prices. That would have been theatre, and the reason is worth recording: **in this
system a strategy is not an executable rule.** `parameters["action"]` is never
checked on the execution path, and TREND_FOLLOW's `reference_price` and
`threshold_pct` are read only by validation and admission, never at trade time. The
selected strategy is handed to the model as context and the model decides. A
rule-replay would therefore have graded a rule the live system never runs and
produced a confident number about the wrong object.

What is real is the decision the strategy actually produced. Every action has
already been scored by the counterfactual ledger against the first real broker
quote at or after the horizon, so `offline_validation.py` screens each strategy on
those journalled verdicts. It cannot be fitted, there is no rule to overfit, and
every number traces to a journalled row.

Three properties keep it honest:

* **It can only reject.** Promotion to ACTIVE still requires prospective live
  cycles. A test enumerates the verdict space to prove there is no code path that
  promotes, because a screen that can promote would short-circuit live evidence.
* **It cannot manufacture evidence.** Too few informative decisions returns
  `INCONCLUSIVE_INSUFFICIENT_EVIDENCE`, not a pass. With the live journal most
  candidates are exactly that, which is the truthful answer and the reason this is
  a screen rather than a decision.
* **NEUTRAL is silence, not failure.** The first cut counted NEUTRAL in the
  denominator and rejected `tiny-fixed-size-001` on 15 neutral decisions — the
  largest PnL contributor on record at +362.66. A move that did not clear the cost
  taught us nothing either way. Pinned by two tests.

Against the live journal: 4 screened through, 2 rejected, 10 inconclusive. The two
rejected (`trend-follow-20260611-003` at 0/10, `-006` at 4/10) were independently
**already PAUSED** by the lifecycle manager on operational metrics, so the screen
reached the same verdict by a different route. `trend-follow-20260611-001` screens
*well* yet is PAUSED on operational metrics; both are true and the screen is
deliberately not permitted to promote it back.

Screening at admission time alone would have been structurally incapable of
rejecting anything — a new candidate has no history and always returns
INCONCLUSIVE. The screen therefore also runs across the whole library each
maintenance pass, which is the only place a verdict can have teeth.

### 9.2 Experiment registry — implemented as a view, deliberately not as a store

Every link of the chain is already journalled: `CURRICULUM_PROPOSED`,
`STRATEGY_ADMISSION_REVIEWED`, `OFFLINE_VALIDATION_COMPLETED`,
`STRATEGY_LIFECYCLE_UPDATED`, `STRATEGY_EVALUATION_RECORDED`. So no registry needed
to exist, and building a persisted one would have created a *second* source of truth
about the same thing — precisely the drift this refactor has been removing
everywhere else. `experiment_registry.py` derives the per-strategy chain and writes
nothing; a test asserts the module exposes no save/write/append.

A broken chain is reported rather than omitted. Current: 35 strategies, 23 admitted,
15 with an incomplete chain, all but one of them historical — strategies that predate
the offline screen. The one live exception is `trend-follow-20260612-006`, a
brand-new PROBATION that has not yet completed an evaluation window, which `doctor`
surfaces as a warning rather than a pass.

### 9.3 Three wiring bugs the tests caught before production saw them

The screen was "wired" on the first attempt in a way that would never have run:

* it called `strategy_library.get`, which does not exist. The `AttributeError` was
  swallowed by the caller's broad `except`, so every run journalled `FAILED` and no
  strategy was ever paused — wired in appearance, dead in practice;
* it called `event.get("payload")` on `JournalEvent` **models** returned by
  `read_events`, not dicts, so the same broad except hid another total failure;
* `_apply_offline_rejection` trusted its caller to check the verdict, so calling it
  directly paused a first-cycle candidate that had been told `INCONCLUSIVE`. The
  guard now lives in the method, because a method that corrupts state when misused
  should be safe when misused.

And the registry view reported "0 admitted, 27 unscreened" at first, because it was
handed cycle records where the chain lives in events; and it reported ACTIVE
strategies as having no evaluation evidence because the per-strategy data is in
`payload["strategy_metrics"]`, not the event's own `strategy_id` field.

## 10. Loop stage status after this work

| stage | status |
| --- | --- |
| observe | active — `data_gateway`, real broker quotes |
| understand | active — capability coverage feeds the curriculum prompt |
| generate decision | active — qwen3.8, schema-constrained |
| risk check | active — `guardian`, machine-checked, immutable |
| execute → broker fill | active — 24 orders submitted, 24 fills broker-confirmed |
| reconcile | active — order and fill reconcilers |
| PnL attribution | active — account-level FIFO, +564.39 across 3 strategies |
| counterfactual evaluation | active — hold quality 0.687 over 350 scored, after cost |
| reflection | active — 663 events, output loaded back into the curriculum prompt |
| hypothesis → candidate | active — curriculum, admission with behavioural dedupe |
| **offline validation** | **active — added; decision-level screen, reject-only** |
| probation | active — 3 cycles before a strategy is tradable |
| shadow | **merged into probation, deliberately — see 10.1** |
| live validation | active — probation trades real paper orders |
| promote/scale/pause/retire | active — journal-first lifecycle, PnL-ranked |
| experiment registry | active — derived view, no second store |

One thing remains genuinely unproven, and it is not a code defect: the final
after-cost, prospective, risk-adjusted live net PnL. Shadow trading is accounted for
in 10.1.

## 10.1 Shadow trading: merged into probation rather than added

Shadow trading is named in the chain and does not exist as code — the word appears
in docstrings only. The obvious move is to implement it. The better move is to
notice it is already there under another name.

Evidence from the live journal: `trend-follow-sell-002` is in `PROBATION` right now
and has already submitted a real broker order through the full path — snapshot,
Guardian, executor, reconciler, broker fill. `tiny-fixed-size-001` (13 orders),
`fixed-size-buy-001` (6) and `trend-follow-buy-001` (4) all traded and all passed
through probation on the way to their current lifecycle.

So probation already *is* the shadow stage in a paper-only system: a candidate
exercises the complete real execution path against the real broker before it is
trusted with a larger size, and live mode is hard-blocked at config load so there is
no real money anywhere in the system to protect with a simulation.

A separate shadow executor would therefore be a **second execution truth** — a
parallel path that submits nothing while pretending to be the one that does, and
which would drift from the real path and then be trusted. The objective asks for one
unified execution truth, and `delete → merge → simplify → repair → add` is an
ordering, not a checklist to satisfy literally. Merging the stage is the correct
application of it; adding a module that duplicates the executor would have been
adding a surface, which is the thing the objective warns against.

What is genuinely missing is not shadow execution but **shadow's purpose**: proof
that a candidate should be trusted at a larger size. That is live prospective
evidence, and it is exactly what the final success criterion requires and what
cannot yet be claimed.

## 11. Closing the remaining drift: config, entry points, and documents

The objective asks that the run entry point be unified and that configuration,
functions, tests and documentation existing never be mistaken for the capability
being real. Three checks close what was still open.

### 11.1 Configuration that cannot affect anything

`AgentConfig` has 34 fields. A new test proves every one is either read in
production code or populated from the environment, and proves the rule itself can
reject a dead field — the same discipline `make verify-self-test` applies to the
gate. It checks the inverse drift too: every `MIN_AGENT_*` variable set in the
shipped runtime env must be read by the loader, because a setting an operator
believes is in force and is not is worse than an absent one.

Current state: no dead fields, no unread operator knobs. An earlier version of that
test contained `assert ... or True`, which always passed while looking like an
assertion — the exact "looks like a check but checks nothing" shape this audit has
been removing, caught here in the check that was supposed to prevent it.

### 11.2 Run entry points

There is one: `minictrl` → `python -m min_agent.cli`. The systemd unit and the
watchdog both go through the same module. `auto_reviewer.py` can write to the
repository and was a genuine second control path while scheduled; it is now in
neither crontab nor a systemd timer and is not running, and it has been changed to
warn rather than overwrite lifecycle decisions that were made from real evidence.
It remains an operator-run tool referenced by the governance tests, not a live
path. Deleted paths (`voyager_quant`, `skill_library`, `tools/legacy`) are
referenced nowhere in code; the only remaining mentions are in historical
documents.

### 11.3 A plan document that contradicted the tree

`docs/superpowers/plans/2026-09-28-quantgroup-recovery-and-refactor.md` still
carried the header "Awaiting approval — no source file has been modified yet" 51
commits in, and instructed the reader to **restore `skill_library/` from its `.bak`
file** — a directory deleted on purpose after being found to hold only
near-duplicate variants of a single sentence. An operator following that plan as
written would have undone the work.

It is now marked SUPERSEDED with a verified table of what each item turned out to
be, and a new `docs-not-stale` gate fails on any plan document that asserts an
unacted state without that marker. The gate proves it can fail: the self-test
plants an unmarked stale plan and asserts FAIL, then the same text marked
SUPERSEDED and asserts PASS.

## 12. Component classification, refreshed

| classification | members |
| --- | --- |
| actively used | `data_gateway`, `loop`, `guardian`, `executor`, `fill_reconciler`, `order_reconciler`, `evaluator`, `broker_evidence`, `reflection_memory`, `curriculum`, `strategy_admission`, `knowledge_admission`, `knowledge_library`, `policy_engine`, `llm_decision`, `strategy_engine`, `scheduler`, `health`, `doctor`, `journal`, `atomicio`, `config`, `models`, `counterfactual`, `offline_validation`, `experiment_registry`, `daemon`, `cli` |
| partially wired | `offline_validation` (screens and can pause, but promotion stays manual and live — by design); `max_account_value` (implemented, deliberately off) |
| implemented but unused | `auto_reviewer.py` (operator tool, unscheduled); `tools/alpaca_smoke.py` (manual smoke test) |
| obsolete | `docs/superpowers/plans/2026-09-28-…` (superseded, retained as history) |
| duplicated | none remaining — the three evidence readers were merged into `latest_evidence_batch()`; the experiment registry is a view rather than a second store |
| broken | none open; six wiring defects found and fixed this session (`strategy_library.get`, `JournalEvent` vs dict, missing verdict guard, `INFORMATIVE` ordering, `relative_to` crash, NEUTRAL-as-failure) |
| missing | nothing in the chain. Shadow trading is **merged into probation** (10.1) rather than added, and prospective live evidence is time, not code |

## 13. Production event log: what the noise was

A scan of all 5037 journalled events for non-success statuses, to check the loop was
not quietly failing. Four distinct signals, three of which are correct behaviour and
one of which was a real defect that is already fixed:

| count | event | reading |
| --- | --- | --- |
| 1193 | `PNL_EVIDENCE_FAILED` / "profit target not satisfied" | **mislabeled, historical, fixed** — see below |
| 44 | `CURRICULUM_FAILED` / Ollama read timeout | mostly historical: 31 on 2026-06-11 under the old model. Recent rate is ~1/day against ~24 proposals/day, and each self-recovers on the next attempt |
| 24 | `CURRICULUM_PROPOSED` / SKIPPED | honest — an explicitly labelled `deterministic fallback (not LLM output)`, so a degraded hypothesis stage is never passed off as model output |
| 35 | `PROFIT_TARGET_CHECKED` / SKIPPED | correct — written only on a status *transition*, so a routine "not satisfied" does not flood the log |
| 3 | `ORDER_RECONCILIATION_REVIEWED` / FAILED | transient broker network errors, self-recovering |

### 13.1 The 1193 "FAILED" records that meant "not yet"

Every one of the 1193 `PNL_EVIDENCE_FAILED` events carries the message "profit
target not satisfied" and none occurs after **2026-06-23**. The profit target is a
promotion criterion, not PnL evidence: the old code conflated the two and wrote a
`FAILED` event each time the target was unmet. A signal channel where 1193 records
say FAILED to mean "no" is a broken signal — a real failure would have been invisible
among them.

The current code is correct: `_record_pnl_evidence` reports only the four
`PNL_EVIDENCE_*` evidence states, and `_verify_profit_target` writes a distinct
`PROFIT_TARGET_CHECKED` event, and only when the status actually transitions.

The historical records are **left as they are**. Rewriting the journal to remove
misleading entries would falsify the audit trail, which is the one thing the whole
system rests on. The correction belongs here, in the record, not in the data.

---

# Part 2 — the full objective, re-read

The first part of this audit worked from a truncated objective. The complete text
carries a **Phase 0–8 ordering** with the rule that a phase may only be entered once
the previous phase's exit criteria are met, and it names requirements that had not
been looked for at all. Re-auditing against the full text changed the picture.

## 14. Correction: shadow trading is a required phase, not a merge

Section 10.1 argued that shadow trading was "merged into probation" because
probation already exercises the real execution path and a second executor would be
a second execution truth. That reasoning was sound on its own terms and it was
**wrong against this contract**: Phase 5 (shadow trading) and Phase 6
(evidence-gated probation) are named as separate stages. The argument has been
withdrawn as a decision about scope. Shadow is recorded as **missing**, and Phase 5
is outstanding work.

The underlying observation still stands and is worth keeping: a shadow executor
must not become a divergent copy of the real one. When it is built, it should reuse
the loop and swap only the execution sink, so the decision path being validated is
the same path production uses.

## 15. Phase 0–8 status against the full objective

| phase | exit criteria | status |
| --- | --- | --- |
| 0 | syntax/import/tests/service startup/restart/recovery/reconciliation PASS | **met** — and crash recovery is now proven rather than configured (16) |
| 1 | real trading and PnL attribution | **met** — 24 orders, 24 fills, 23 closed lots, +564.39 |
| 2 | counterfactual and decision-quality evaluation | **met** — hold quality 0.687 over 350 scored, after cost |
| 3 | strategy/model/experiment registry | **met in part** — strategy registry and derived experiment view exist; champion–challenger and lineage do not |
| 4 | strict offline backtest, walk-forward OOS, cost, leakage, stress, overfitting | **now implemented** (17) |
| 5 | shadow trading | **missing** — not implemented; see §14 |
| 6 | evidence-gated probation | **partly** — probation exists and offline validation now gates it, but it is not shadow-gated |
| 7 | prospective live evidence drives promote/pause/retire | **incomplete** — lifecycle is PnL-driven and running; prospective duration not yet accumulated |
| 8 | regime-aware allocation, model retraining, strategy evolution | **not started**, correctly — the objective forbids entering it early |

## 16. Crash recovery, proven rather than configured

`Restart=always` in the unit file is a claim, not evidence. It was tested by
`SIGKILL`ing the daemon — no cleanup, no chance to shut down:

```
07:34:55  Main process exited, code=killed, status=9/KILL
07:35:25  Scheduled restart job, restart counter is at 1
07:35:25  Started min-agent.service
```

`NRestarts=0 → 1`, new `MainPID`, pidfile rewritten, no manual step. State survived
it: 920 cycles and 5782 events re-read cleanly afterwards, the journal re-parsed
with zero corrupt lines, and the restarted daemon resumed journalling within a
minute. A daemon that comes back alive but has forgotten everything is not a
recovered daemon.

## 17. Phase 4: offline validation, now real

`src/min_agent/research/` implements the backtest the objective's Phase 4 requires.
It is a separate package, and a `make verify` check **fails if any production module
imports it** — the separation between production and research is machine-checked
rather than conventional, because a convention erodes exactly when a backtest starts
looking like a useful signal.

An earlier session concluded a backtest would be "theatre" and declined to build
one. Both halves of that are recorded in the module, because it was half right:

* **Right:** production does not enforce `parameters["action"]` or TREND_FOLLOW's
  `threshold_pct`. A passing backtest says nothing about what production will do —
  which is why walk-forward, shadow and probation exist, and why a green backtest
  can never promote anything.
* **Wrong:** "not enforced in production" is not "not specified". The rule is
  fully determined by the declared parameters and is executable. The research layer
  is exactly where it should be measured.

### 17.1 The controls, and three of them caught me

* **Leakage.** The guard asks the rule the same question twice — once with the whole
  series, once with only the bars up to now — and counts any disagreement. The first
  version only noticed the loop index going backwards, which no rule in the module
  could do; it could not have detected the one leak that matters. A test plants a
  rule that reads one bar ahead and asserts the guard fires.
* **Cost.** Charged on entry *and* exit. The paper broker reported zero commission
  on all 24 fills, so a gross backtest flatters every strategy by exactly what it
  would really have paid.
* **De-duplication of observations.** 920 journaled records are 467 distinct
  observations. Keyed on timestamp the sample looks twice its real size and measures
  the 5-minute polling interval.
* **Overfitting control — the first version did nothing.** It computed
  `threshold / trials`, and the threshold was `0.0`, so `0.0 / 40` is `0.0`: trying
  forty variants cost nothing and the control was decoration. The real risk of
  trying N specs is not a too-small return but that the winner was selected on
  noise, so the trial count now raises the number of independent out-of-sample
  trades required, on a square-root schedule. With 27 trials that is 10 trades.
* **Stress — one jitter seed is not a stress test.** The single seed used first made
  a long position *better* (+28.97 against a +26.14 baseline), because an early
  negative shock lowers the entry. The worst of five seeds is kept, and the seeds
  range from +8.74 to +39.61, which is exactly why one seed was worthless.
* **Verdict ordering.** "Overfitted" was returned for strategies with one trade per
  fold. That is a claim about generalisation, which needs enough trades to
  generalise from, so thin data is now reported as thin before any confident label
  is attached.

### 17.2 The honest result: no strategy survives

Run over all 27 registered strategies, on 467 real bars:

```
verdict tally: {'INSUFFICIENT': 27}
required OOS trades for 27 trials: 10
best achieved: 3 out-of-sample trades
```

**Not one strategy in the registry has enough out-of-sample evidence to be called
anything.** That is the first truthful research verdict this system has produced,
and it is the correct answer to the data available rather than a disappointing one.
Three independent lines of evidence already agree: three strategies earned verified
PnL, hold quality is 0.687, and 27 of 27 strategies are statistically unproven.

## 18. A dedupe blind spot, and a strategy that lies about itself

`behavioural_signature` returned `None` for every TREND_FOLLOW, on the reasoning that
its side is derived from the price at decision time and so cannot be compared without
a snapshot. The consequence was that **no TREND_FOLLOW was ever compared to any
other** — twelve sat in the registry and the zoo check was blind to all of them.

The side being dynamic does not make two strategies uncomparable, because the rule is
fully determined by the declared parameters. TREND_FOLLOW now returns a real
signature, and 14 redundant copies became visible, including three exact duplicates:
`trend-follow-20260611-001/-002/-003` share reference 100.0, threshold 0.02 and
quantity 1, and `-004/-005` share 150.0 and 0.01.

The same pass surfaced a semantic duplicate the objective explicitly asks for.
`trend-follow-sell-002` is named **"Trend Following Sell Strategy"** but its `kind` is
`FIXED_SIZE` with no threshold or reference price — it does not trend-follow at all,
and is behaviourally identical to `fixed-size-sell-001` apart from a `confidence`
value the signature correctly ignores. A strategy whose name claims one thing and
whose rule does another is the failure mode "automatic detection of
semantic/behavioral duplicate" exists to catch.

## 19. Phase 5: shadow trading

Withdrawn in §14, now built. `MIN_AGENT_SHADOW=1` replaces the final call to the
broker and nothing else. Real market data, the real model, the real Guardian, the
real journal — only the submission is replaced. That is the whole design: a shadow
mode with its own simplified decision path would be grading a different system from
the one that will trade, and could pass while the real path failed.

It is a config flag, not a fourth `mode`, because `mode` already carries a hard
safety meaning — anything but `paper` raises at config load. Overloading it with
"paper but pretend" would blur the one line that must stay sharp.

### 19.1 The safety property is negative, and it is the point

A shadow order is **not** a fill. If one ever reached the PnL ledger as an executed
trade, the account would book profit from money that was never risked and an
unvalidated path would show up as the best one on record — the most dangerous bug
this system could have, because the loop would lie while looking healthy.

Three things prevent it, and a test proves the outcome rather than the intention:

1. `status="SHADOWED"` is a distinct value, not a reuse of `SKIPPED`. Anything that
   branches on the field and does not know it will not count it as executed — the
   safe default.
2. `order_id` is always `None`, so no reconciliation pass can match it to a broker
   order. A real fill always has one.
3. A journal of four shadow orders yields
   `account_realized_or_reported_pnl is None`, `account_return_pct is None`,
   `strategy_realized_pnl == {}`, `closed_lot_count == 0`, `linked_fill_count == 0`.

A `make verify` check now enforces this across every production module: **no
module may group `SHADOWED` with an executed status.** It is a gate rather than a
test because it is a cross-cutting invariant, and a new `if` on `execution.status`
is exactly when it would be reintroduced. The check is proven to fire by planting a
grouping, and it was itself wrong on first run — it failed on the `Literal` that
*declares* the statuses, which is how a gate earns a reputation for noise.

### 19.2 Verified against the live journal

A shadow intent written to the real journal produced: `status=SHADOWED`,
`order_id=None`, one `SHADOW_ORDER_INTENT` event, **cycle count unchanged at
920/920** — a shadow intent is not a cycle — and account realized PnL `None`. A
`shadow-smoke` intent from this check is in the journal and is left there, labelled
as it is; deleting it would falsify the record.

### 19.3 Three mistakes, all the same shape as before

* `append_event` takes a `JournalEvent`, not keyword arguments. Passing kwargs raised
  `TypeError`, which a broad `except` ate, so **every shadow intent was silently
  dropped** and the stage journalled nothing. That is the third time in this work
  that a `JournalEvent`/dict confusion turned a wired path into a dead one.
* The event type was missing from `JournalEventType`, raising `ValidationError` —
  swallowed by the same `except`.
* The sink-selection function was anchored on `def _build_loop(`, which does not
  exist in `cli.py`, so both call sites were rewritten and the definition never
  landed. The startup test caught it as an unbound name.

The `except` itself is correct and stays — a journal fault must never escalate into a
real order — but it now records the failure in `ShadowExecutor.journal_failures`
instead of discarding it, because a swallowed exception is how the first two mistakes
stayed invisible. A test asserts a healthy journal leaves it empty, so the counter
cannot rot into a permanent no-op.

The `shadow-live-consistency` check also reported "shadow mode NOT implemented" for
the rest of this session, after shadow had been built, because the string in it was
never updated. It now verifies that shadow is reachable from configuration, off by
default, and that the switch is parsed strictly — `MIN_AGENT_SHADOW=flase` raises
rather than silently enabling a flag that decides whether real orders reach a broker.

## 20. Phase 6: evidence-gated probation

The promotion path contained this:

```python
return StrategyLifecycleDecision(strategy, "ACTIVE",
                                 "probation completed with acceptable operational metrics")
```

"Acceptable operational metrics" means the strategy did not error and was not
rejected much. So a strategy that existed, **never submitted an order, never opened a
lot and therefore had no PnL of any kind** was promoted to ACTIVE on the grounds that
it had not crashed. The objective requires every promotion to rest on verifiable
evidence, not on the absence of trouble, and this was the clearest violation of that
in the system.

### 20.1 What promotion now requires

A strategy leaves PROBATION for ACTIVE only on one of:

1. **Broker-verified realized PnL from a closed lot** — unchanged, and still the
   strongest evidence available. The gate supplements PnL, it does not replace it.
2. **Decision-quality evidence** — at least 10 scored counterfactual verdicts for
   decisions that strategy actually produced, with a non-rejecting verdict. This is
   the path a long-only strategy takes, since it may never close a lot, so the gate
   is not an infinite probation.

And it must have **submitted at least one order**. Zero orders means no evidence of
anything, however clean the operational record.

Absent evidence reads as *not yet*, never as *assumed fine*. That is the direction
that matters, and it is the direction the previous code got wrong by defaulting to
promotion.

### 20.2 A hole found in my own gate

The first version of the gate checked the verdict before the evidence volume, so a
`PASS_SCREENED` verdict carrying three scored decisions promoted a strategy. The
offline screen does not issue such a verdict — it requires ten — so the two were
believed to agree, and a test caught it. A gate that depends on an undocumented
upstream invariant is one refactor away from promoting on three data points, so the
volume is now checked first and a test pins it.

The gate also states *why* it is refusing. A gate that can only say no leaves the
journal unable to explain why a strategy is stuck, which is how a strategy sits in
probation forever with no account of it.

### 20.3 What the tightened gate does to the live registry

Both PROBATION strategies are now blocked, each with the reason recorded:

```
trend-follow-20260612-006   PROBATION  0 orders  only 0 scored decision(s), below the 10 needed to judge
trend-follow-sell-002      PROBATION  1 order   only 0 scored decision(s), below the 10 needed to judge
```

This is the correct and expected consequence. Phase 4 found all 27 strategies
statistically unproven; Phase 6 now makes the lifecycle agree with that instead of
promoting on operational tidiness. **The gate tightens rather than widens**, which
is the only defensible direction given the evidence on record.

The gate is satisfiable, and a daemon test proves it end to end: with no evidence
journalled the gate refuses, and once an `OFFLINE_VALIDATION_COMPLETED` event exists
the same strategy is promoted. A gate nothing can pass would be a freeze, and would
silently stop the lifecycle from ever advancing.

### 20.4 Two mistakes in the tests themselves

* A string replace intended for one test matched three and rewrote all of them,
  corrupting assertions about broker-verified negative PnL. The file was restored
  from git and the edit redone by locating the function's exact line range.
* The rewritten tests asserted `review(...) == []`, conflating "not promoted" with
  "no decision at all". Other guards — notably the degenerate-exploration check —
  may legitimately fire, so a test that conflates them passes for the wrong reason
  and hides which guard did the work. They now assert `new_lifecycle != "ACTIVE"`,
  which is the property the contract actually cares about.

## 21. Lineage, search effort, champion–challenger

Three requirements from the objective, none of which the system recorded.

**Lineage did not exist.** `StrategySpec` has no lineage field at all — the fields are
`strategy_id`, `name`, `kind`, `symbols`, `parameters`, `max_position_value`,
`enabled`, `lifecycle`, `created_at`, `rationale` — so nothing linked a strategy back
to the failure it was proposed to fix. The link does exist in the journal: the
curriculum event carries the exploration summary that motivated the candidate and the
`task_id` it came from, so the view can be derived rather than stored.

**Search effort left no trace.** The live journal holds 69 proposals across 63 task
ids, and `task-20260611-002` produced **three** variants. That is a search. Keeping
the best of three and reporting it as though it were the only candidate is selection
bias, and it is invisible unless the trial count is recorded. `search_effort` now
reports proposals, distinct tasks, how many tasks searched, and the largest number of
variants any single task produced.

**No champion existed.** The registry held two ACTIVE strategies and no way to say
which was the benchmark. The champion is now designated from **broker-verified
strategy-level PnL only** — currently `fixed-size-buy-001` at +120.37, the highest of
the two ACTIVE strategies carrying verified positive PnL. Naming one from cycles, or
from an in-sample backtest, or from the model's opinion would be choosing a benchmark
on the same evidence the system already distrusts, so with nothing verified the
answer is `None` and the report says why.

### 21.1 What it found on the live journal

```
champion: fixed-size-buy-001 (+120.37 verified, highest of 2 ACTIVE)
candidates: 33 from 31 tasks; 1 task searched, max 3 variants
33 of 33 candidates cannot state what observation motivated them
```

**Not one candidate in the system's history recorded the observation that produced
it.** The proposal schema requires a free-text `rationale` and nothing else, so a
candidate can be admitted while stating none of the four things the objective requires
— what observed failure it addresses, how it differs from what exists, what data it
used, and how many searches it went through. That gap is reported rather than
backfilled: inventing a justification after the fact would be worse than recording
that none was given.

### 21.2 Four mistakes, three of them the same shape as before

* The doctor check function was **defined and never called** — the anchor it was
  inserted against did not match. That is the "implemented but unused" category this
  audit exists to eliminate, created in the commit hunting for it. A new
  `make verify` check now asserts every `_check_*` is called from the governance entry
  point, so the shape cannot recur silently.
* That reachability check was itself wrong twice. First its regex was double-escaped
  and matched a literal backslash; then it subtracted name *sets*, which cannot work
  because every called name is also a defined name, so the sets are identical and the
  subtraction empties to nothing — it reported all 17 checks as unreachable. It now
  distinguishes call sites by position rather than by name.
* `ReflectionMemory` was **used but never imported** in `doctor.py`. The `NameError`
  was swallowed by the check's own `except Exception`, leaving the results empty and
  the report claiming `champion=NONE` while a champion was on file. A check that
  reports "NONE" when the answer exists is worse than no check.
* The champion reason read "the only such strategy on record" when two strategies
  qualified. A health report that overstates its own case is the same defect as one
  that understates it; the wording now counts the qualifiers and names the runner-up,
  with a test.

## 22. Phase status after this work

| phase | status |
| --- | --- |
| 0 startup/restart/recovery/reconciliation | **met**, recovery proven by SIGKILL |
| 1 real trading and PnL attribution | **met** — 24 orders, 23 closed lots, +564.39 |
| 2 counterfactual and decision quality | **met** — hold quality 0.687 after cost |
| 3 strategy/model/experiment registry | **met in part** — champion and lineage now exist; model registry does not |
| 4 backtest/walk-forward/cost/leakage/stress/overfitting | **met** — 27 of 27 strategies INSUFFICIENT |
| 5 shadow trading | **met** — real loop, shadow sink, zero PnL leakage |
| 6 evidence-gated probation | **met** — both PROBATION strategies correctly blocked |
| 7 prospective promote/pause/retire | **incomplete** — lifecycle runs on evidence, prospective duration not yet accumulated |
| 8 regime-aware allocation, retraining, evolution | **not started**, correctly |

## 23. Model prediction vs actual outcome

The objective requires model predictions to be aligned against actual results. The
system records a `confidence` on every decision and **never once compares it to what
happened**. Confidence is used as a gate — `min_confidence` decides whether an order
may be sent — and never as a claim to be tested. So a model that is confidently wrong
is indistinguishable from one that is right, and the threshold is untested.

The pairing needs no new data: every decision carries a confidence, and the
counterfactual ledger already scores every decision against the first real broker
quote at or after the horizon. `calibration.py` joins the two.

### 23.1 What it found: the model's value is unmeasured

```
model calibration: 67 llm decision(s), 0 scored, 67 still awaiting an outcome
```

All 67 LLM decisions fall between 11:20 and 15:59 on 2026-09-28 — a single session —
and the 24-hour horizon needs a quote from 2026-09-29 11:20 onward, which does not
exist because the market has been closed. Mean LLM confidence is 0.276.

So the honest reading is not "the model is bad". It is **"we do not know yet"**, and
the report says exactly that: `INSUFFICIENT`, with a note that the value is
unmeasured rather than good. A pass here would mean the model had been shown to earn
its threshold, which the evidence does not support.

### 23.2 Scoring rules, and the one that could have flattered the model

Only a hold that avoided a loss counts as correct. A `MISSED_ALPHA` — a hold that
should have been a trade — is **incorrect**, and a `FALSE_TRADE` is incorrect. Scoring
a missed rally as "correct caution" would give any model that never trades a perfect
curve for free, which is the easiest way to manufacture good calibration. `NEUTRAL`
is excluded rather than counted either way, because a move that did not clear the
cost is silence.

### 23.3 The metric, and why the obvious one was wrong

The first comparison was "accuracy of gate-clearing decisions vs the base rate". With
`min_confidence` at 0.5, a 0.6 and a 0.9 both clear the gate, so the comparison
measured almost every decision and **could not tell a model that separates good from
bad from one that does not** — a test failed for the right reason with the wrong
metric.

The sharper question is whether the **most confident populated bucket** beats the
base rate. That is what "does confidence identify the good decisions" actually asks,
and it is now the comparison, with a test that pins the gate comparison's weakness so
it is not reintroduced.

Brier score is reported alongside: 0 is perfect, 0.25 is what always claiming 0.5
scores. Confidence is bucketed 0.2 wide so 0.95 and 1.0 land together — splitting them
produced a one-member bucket that read as a 100% accurate stratum.

### 23.4 Why it is a doctor warning, not a pass

`OK` requires the verdict to start with `SIGNAL`, which requires the top bucket to
beat the base rate on at least 30 scored decisions. Until the market supplies
outcomes, the check is a warning — and a warning here is the truthful state, not a
defect in the report.

## 24. Objective deliverable ⑥: what actually caused the PnL

The objective asks which of signal, model, strategy, allocation, execution, cost or
regime produced the profit or loss. The answer is not the one the headline number
suggests, and it was reachable from data the system already held.

**The +564.39 is not the model's money. All 23 closed lots were opened by the
`baseline` decision source, on 2026-06-11, 06-12 and 06-18. The LLM path produced its
first decision on 2026-09-28 — two and a half months after the last profitable lot —
and opened none of them. The model's contribution to realized PnL is 0.00.**

Both halves of that join were always recorded: the lot names the order that opened it
(`buy_order_id`) and the cycle names the decision source that issued that order. Nothing
joined them, so `strategy_realized_pnl` was read as evidence that the decision engine
worked, when it is evidence about the baseline rule.

### 24.1 The two numbers in one payload are not a reconciliation

| number | what it is |
| --- | --- |
| `strategy_realized_pnl` **+564.39** | the agent's **closed** lots, SPY only, opened 2026-06-11 to 06-18 |
| `account_realized_or_reported_pnl` **−406.54** | the broker's **cumulative** `profit_loss` for the whole account, all time, BIL and TLT included |

Different scopes, different periods. The 970.93 difference is **not** a loss, and a
reader seeing both in the same payload would reasonably conclude the agent lost against
its own accounting. `attribution.py` states the scopes and says explicitly that they
must not be reconciled.

### 24.2 Cause by cause, from the record

| cause | verdict | evidence |
| --- | --- | --- |
| model | **NOT THE CAUSE** | 0.00 of +564.39 came from an llm or fallback decision |
| signal | attributable | all 23 lots came from the `baseline` source, so the PnL belongs to that rule |
| strategy | attributable | 3 strategies carry it; largest is `tiny-fixed-size-001` at +362.66 |
| cost | **not a factor here** | the paper broker charged 0.00 across 23 lots, so this is gross and overstates what a live venue would pay |
| allocation | partly attributable | 36 orders were refused; the PnL is what got through, not what was wanted |
| execution | not a factor here | fill ratio 1.00 — submitted quantity filled in full |
| regime | **cannot attribute** | no regime context is computed or recorded |

All seven causes are always reported. A row absent when the data is missing is a
silent gap, and a reader cannot tell "not a factor" from "nobody looked".

### 24.3 Severity, and refusing to mask it

The doctor check could have been made green by downgrading the model finding, and that
would have been masking. The split is drawn on evidence instead:

* model opened **no lots** → the figure belongs entirely to the baseline rule and the
  model has neither helped nor hurt. A fact waiting on market hours: **WARN**.
* model opened lots and contributed **≤ 0** while the total is positive → a model
  trading and failing to beat the baseline: **FAIL**.

A test pins both branches. Writing that test found a further gap: the first version
tested `model_pnl == 0.0`, so a model that opened a lot and **lost** 20.00 while the
account was up reported `OK`. The rule is now `<= 0.0`, which is the same defect as the
original one caught one level down.

This is the single most important open finding in the project: **the system's headline
profit is not evidence that the model works.**

## 25. Regime context, and what it actually shows

`attribution.py` reported `regime: CANNOT ATTRIBUTE` because nothing computed or
recorded a regime. `regime.py` closes the **measurement** half of that. It
deliberately does not allocate by regime — allocation is Phase 8, and starting it
before the earlier phases finish would be entering a phase out of order.

### 25.1 What the data supports, and what it does not

Only one `last_price` per cycle is available: no OHLC, no volume, no VIX. So the
regime is derived from the realized price series, and the limitation is stated rather
than buried — these are last-trade prints sampled on a 5-minute cycle, not exchange
bars. Volatility from them is noisier and slightly inflated by whatever happened
inside each interval, and a gap between samples reads as a move that may have occurred
anywhere within it.

### 25.2 The result on the live journal is mostly "cannot tell"

```
467 bars
UNKNOWN           391 bars (84%)
TRENDING_DOWN      76 bars (16%)
current regime: UNKNOWN - the last 78 bars span a gap over 30 minutes
contiguous windows: 78 of 467
```

Only **78 of 467 bars** have a contiguous window. The journal is far sparser than a
5-minute cycle should produce, and the current regime cannot be labelled at all.

This is why a window spanning a gap is **refused** rather than classified: the live
journal contains a 97-day outage, and a "recent volatility" computed across it would
describe two different months as one market.

### 25.3 And the PnL cannot be separated into regime

```
PnL by the regime at the moment each lot was opened:
   UNKNOWN   23 lots   +564.39
```

**Every one of the 23 profitable lots was opened in a window that cannot be
labelled.** So the regime row is no longer `CANNOT ATTRIBUTE` because the code was
missing — it is `CANNOT ATTRIBUTE` because it was *measured* and came back unusable.
That is a different and much better position: the loop now knows it cannot answer
this, rather than not knowing that it could not.

The unmeasurable bucket is reported rather than dropped. A bucket that silently
vanishes is how a conclusion gets drawn from the measurable minority.

### 25.4 Two properties enforced rather than assumed

**Point-in-time.** A label at bar *i* uses bars up to *i* only. The test that matters
is that appending future bars cannot change an earlier label — a regime label attached
to a past decision is exactly the kind of context that leaks, and it would leak
silently.

**Volatility can override direction.** A steep decline is `UNSTABLE`, not
`TRENDING_DOWN`. Filing a violent decline as a calm downtrend would understate the
risk of trading through it. A test caught my own fixture making this mistake: a
-2.0-per-bar series was expected to be `TRENDING_DOWN`, and the classifier was right
to call it `UNSTABLE` while the test was wrong.

The same test also showed the classifier is describing *the window it is given*: with
the default 78-bar window a series that rose then fell is `TRENDING_UP`, because the
rise dominates. That is correct behaviour, and the test now isolates the decline with
a 30-bar window rather than expecting the classifier to see through its own window.
