# Per-step contract

The objective requires that every step of the loop have an explicit **input**,
**output**, **responsibility boundary**, **failure behaviour**, **log**,
**provenance** and **automated test**. This is that table, derived from the code
rather than from intent: the signatures below are the real ones, the event types are
the ones the journal actually receives, and the test files are the ones that exist.

Read the **responsibility boundary** column first. It is the column that matters,
because most of the defects this audit found were not bugs in a stage — they were a
stage doing another stage's job.

---

## 1. The production loop

| # | stage | input | output | responsibility boundary | failure behaviour | log | provenance | test |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **observe** `AlpacaDataGateway.snapshot(symbol)` | broker API | `DataSnapshot` | reads market and account state. Decides nothing. | raises; the loop converts to a fail-closed HOLD | `CycleRecord.snapshot` | `snapshot.source` records the real origin | `test_data_gateway.py` |
| 2 | **understand** | `ReflectionRecord`, current strategies | capability coverage | computes which actions the library cannot currently express. Informs the curriculum; places no orders. | falls back to a labelled deterministic task | `CURRICULUM_PROPOSED` payload | `source: llm \| fallback` | `test_curriculum.py` |
| 3 | **generate decision** | snapshot + context + model | `TradeDecision` | may propose anything. Holds no risk authority. | invalid output → fail-closed HOLD | `CycleRecord.decision` | `decision_source`, `model`, `strategy_id` | `test_llm_decision.py` |
| 4 | **risk check** `Guardian.review(decision, snapshot, …)` | decision, snapshot, agent's own holding | `GuardianResult` | **the only gate that can refuse.** Immutable. May not be relaxed by any model, prompt or learning stage. | refuses; the reason is recorded and reaches the operator | `CycleRecord.guardian.reason` | guardian reason on every refusal | `test_guardian.py` (38 tests) |
| 5 | **execute** `AlpacaPaperExecutor.execute(decision, guardian, …)` | an **approved** decision | `ExecutionResult` | places the order and nothing else. Refuses an unapproved decision. | returns `REJECTED`/`ERROR` rather than raising | `ORDER_FILL_CONFIRMED` | `client_order_id` links fill → cycle | `test_executor.py` |
| 6 | **reconcile** `OrderReconciler` / `FillReconciler` | broker orders, fills | `ReconciliationReport` | matches what was sent to what the broker did. Never infers a fill. | records `FAILED`; never fabricates a match | `ORDER_RECONCILIATION_REVIEWED` | `order_id` / `client_order_id` | `test_order_reconciler.py`, `test_fill_reconciler.py` |
| 7 | **PnL attribution** `DeterministicEvaluator.evaluate(…)` | cycles + broker evidence | `EvaluationReport`, `PnLEvidence` | converts fills into per-strategy realized, unrealized and net. Invents no price. | reports `missing_*` evidence rather than a number | `PNL_EVIDENCE_RECORDED` / `…_FAILED` | every lot carries `buy_order_id`, `strategy_id` | `test_evaluator.py` |
| 8 | **counterfactual** `counterfactual.evaluate(records, …)` | journal cycles | per-decision verdicts | scores each decision against the **first later real quote**. Reads no future. | `PENDING` / `GAP` rather than a guess | `COUNTERFACTUAL_EVALUATED` | both prices and the horizon on every row | `test_counterfactual.py`, `test_point_in_time.py` |
| 9 | **reflect** `ReflectionMemory.reflect(…)` | cycles + evidence | `ReflectionRecord` | summarises. Changes no behaviour. | `REFLECTION_FAILED` | `REFLECTION_GENERATED` | `strategy_metrics` per strategy | `test_reflection_memory.py` |
| 10 | **hypothesis** `StructuredCurriculumAgent.propose(…)` | reflection + library | `CurriculumTask` | proposes a candidate. Cannot place an order. | `CURRICULUM_FAILED`, or a labelled fallback | `CURRICULUM_PROPOSED` | `task_id`, `recent_exploration_summary` | `test_curriculum.py` |
| 11 | **admission** `StrategyAdmission.admit(spec)` | a candidate | `accepted: bool` + reason | may only **refuse**, or write a new file. Never edits an existing strategy. | refuses with a specific reason | `STRATEGY_ADMISSION_REVIEWED` | accepted flag and reason | `test_strategy_admission.py` |
| 12 | **offline validation** `offline_validation.validate(…)` | scored decisions | verdict | may only **reject**. A test enumerates the verdict space to prove no promoting path. | `INSUFFICIENT` when thin | `OFFLINE_VALIDATION_COMPLETED` | scored count, ratio, verdict | `test_offline_validation.py` |
| 13 | **shadow** `ShadowExecutor.execute(…)` | an approved decision | `status=SHADOWED` | the **only** sink that reaches no broker. Reuses the loop above it unchanged. | journal failure is recorded, never escalated to a real order | `SHADOW_ORDER_INTENT` | `client_order_id` prefixes `shadow-` | `test_shadow.py` |
| 14 | **live validation** | probation cycles | lifecycle state | trades for real under the Guardian. Evidence-gated. | blocks promotion when evidence is absent | `STRATEGY_LIFECYCLE_UPDATED` | old/new lifecycle, reason, phase | `test_strategy_engine.py` |
| 15 | **promote / pause / retire** `StrategyLifecycleManager.review(…)` | strategies + per-strategy evidence | lifecycle decisions | journal-first: intent, then write, then outcome. | a crash leaves a visible half-state, not a silent change | `STRATEGY_LIFECYCLE_UPDATED` | `decided`/`applied` phases, source | `test_strategy_engine.py` |

## 2. The research layer — separate, and checked to stay separate

| stage | input | output | boundary | test |
| --- | --- | --- | --- | --- |
| **backtest** `research.backtest.run_backtest(…)` | real bars + a declared rule | `BacktestResult` | executes the **declared** rule, which production does not enforce | `test_research_backtest.py` |
| **walk-forward** `research.walk_forward.run_walk_forward(…)` | bars | `WalkForwardResult` | scores only the out-of-sample leg; trial count is an output | `test_research_backtest.py` |
| **trial ledger** `research.trials.record_trial(…)` | a trial result | an append-only line | a **separate sink**. Nothing in production reads it. | `test_research_trials.py` |

`make verify` fails if any production module imports `min_agent.research`. That check
has already caught a real violation: a `doctor` check was added that imported the
trial ledger, and the gate refused it.

## 3. What the boundaries are actually protecting

Every defect of consequence in this audit was a boundary failure rather than a bug:

| defect | boundary it crossed |
| --- | --- |
| 29 shares sold that the agent never bought | Guardian checked the **account's** holdings, not the agent's |
| Shadow orders could have been counted as fills | `SHADOWED` was not a distinct status |
| Offline validation could have promoted | no evidence gate on lifecycle promotion |
| PnL reported gross | no cost term in the live ledger |
| `strategy_realized_pnl` read as evidence for the model | the lot→order→source join was never made |
| A doctor check defined but never called | the check was wired in appearance only |

## 4. Honest gaps in the contract

* **Stage 2 ("understand") has no module of its own.** Capability coverage is computed
  inside the curriculum path rather than as a separate stage. It works and it is
  tested, but it is not a component that could be replaced independently.
* **Unrealized PnL is proven only by test**, not against a live position — the agent
  holds none. The machinery reports an open lot with no price as *unpriced* rather
  than valuing it at cost, which is the rule that makes it safe, but the live path has
  never had a positive open lot to mark.
* **Cost is an assumption, not an observation.** The paper broker charged 0.00 on all
  24 fills. The after-cost figure charges a configured 0.05% round trip and is
  reported separately from the observed figure.
* **33 of 33 candidates cannot state what observation motivated them.** The objective
  requires each to say so; the proposal schema requires only free text. Reported in
  `doctor` rather than enforced, because back-filling a justification after the fact
  would be worse than recording that none was given.
