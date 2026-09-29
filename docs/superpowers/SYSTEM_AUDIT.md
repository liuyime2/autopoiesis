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
