# Covered paper decisions implementation plan

> **For agentic workers:** Use Superpowers subagent-driven-development; freeze each unit for specification review then independent quality review. Run scripts/tests through Make.

**Goal:** No actionable Hybrid decision without a frozen selected strategy covering the actual snapshot symbol.

**Architecture:** Reuse HybridDecisionEngine's existing selected spec and captured provenance. Abstain explicitly when selection/context integrity fails; do not reselect through fallback. Preserve valid covered LLM decisions and rule fallback.

**Tech Stack:** Existing Python and existing test_llm_provenance.py/test_learning_evidence.py; no new dependency.

## Baseline, success and proof

Read-only audit at source 209222b: HybridDecisionEngine.decide_snapshot always calls
LLM after _context even if selected_strategy_id/provenance are None. An actionable
response can reach Guardian without strategy attribution. Actual operator symbols
SPY/BIL/TLT/XLB/XLE/XLF plus four attention slots; selectable strategies cover only
SPY/BIL/TLT, leaving three configured symbols uncovered. There are no ACTIVE
strategies; covered probation is not proof of alpha.

Success: when there is no matching frozen selected strategy/provenance, return
action HOLD/quantity 0 with an explicit selection reason and no LLM call. The
no-selection fallback must also return HOLD directly rather than call the policy
engine again and select a different strategy. Selection exceptions, missing
provenance and uncovered symbols are abstention, not an attributed trade. Valid
covered decisions retain exact frozen strategy provenance, normal model/rule
behavior and Guardian handling. Do not invent historical provenance or promote
legacy candidates.

- [x] Add labelled synthetic regressions in existing test files: actionable LLM
  with empty library, outside-universe snapshot, selector failure, and a fallback
  policy that would choose a different strategy. Verify no actionable decision,
  no model invocation and no policy reselection. Reproduce failures through Make.
- [x] Minimum production change in src/autopoiesis/llm_decision.py: check frozen
  selection after context preparation and return explicit abstention; replace
  no-selected fallback with explicit abstention. Update the misleading comment
  that a degraded no-strategy context may still trade. Do not add a new layer.
- [x] Run relevant existing LLM/provenance/learning tests via Make and scoped lint.
  Repair synthetic fixtures only when they assumed uncovered trading; retain
  explicit tests for successful covered LLM and normal fallback. Record counts.
- [x] Independent specification and quality reviews. Commit one coherent unit
  after root reviews actual diff/status/log; reconcile parent readiness plan.

This unit prevents unsupported trading. It does not discover alpha, establish
LLM benefit or guarantee any order at the next open.

## Evidence

Baseline is the inspected production branch; red/green command results are
recorded after real test execution, not filled in advance.

Implementation verification in the isolated `rsi-audit-repairs` worktree:

- Baseline `make test TEST_ARGS='tests/autopoiesis/test_llm_decision.py tests/autopoiesis/test_llm_provenance.py tests/autopoiesis/test_learning_evidence.py tests/autopoiesis/test_llm_fallback_check.py'`: **104 passed, 1 skipped**.
- Labelled synthetic regression red run `make test TEST_ARGS="tests/autopoiesis/test_learning_evidence.py -k 'synthetic_unattributed_selection or synthetic_no_selection_fallback or synthetic_covered_model'"`: **8 failed, 2 passed, 31 deselected**. Empty library, uncovered symbol, selector failure, absent provenance, wrong selected universe, wrong provenance id and invalid digest all reached the model; direct no-selection fallback reselected a BUY. The wrong selected universe already returned rule HOLD but still invoked the model, rather than abstaining before it.
- Same regression command after the guard/fallback change: **10 passed, 31 deselected**. An initial command used one underscored `-k` token by mistake and selected zero tests (exit 5); corrected command above supplies the actual red/green evidence.
- First four-file regression after guard: **1 failed, 113 passed, 1 skipped**. The sole old fixture asserted a broken selector could still invoke the LLM. Updated it to explicitly assert synthetic no-selection abstention, no model invocation and no reselection.
- Additional covered-rule provenance red run `make test TEST_ARGS='tests/autopoiesis/test_learning_evidence.py -k synthetic_risk_fitted_rule'`: **2 failed, 41 deselected** because position-limit fitting replaced the rule with an unattributed HOLD. Stamping the already-frozen selection on override and fallback outputs made the same command **2 passed, 41 deselected**. No limit or sizing rule changed.
- Final four-file regression using the baseline command: **116 passed, 1 skipped**. The skip is existing optional tokenizer availability. Covered model decisions and timeout rule fallback keep the original capture after a synthetic library mutation; no historical metadata is reconstructed.
- `make lint-files FILES='src/autopoiesis/llm_decision.py tests/autopoiesis/test_learning_evidence.py tests/autopoiesis/test_llm_provenance.py'`: **All checks passed**.

No-selection abstention uses `decision_source="policy_engine"`, `hold_reason="no_signal"`, and an explicit `selection_unavailable` rationale. It is not recorded as an attempted model-call failure. Independent specification review approved and reran the four-file suite: **116 passed, 1 skipped**. Subsequent independent quality review approved, with focused boundary regressions **12 passed, 31 deselected** and clean diff checks. Root inspected status/diff/log before the coherent commit. No deployment has occurred in this unit. These executions prove synthetic boundary behavior, not real profitability or a live-paper trading outcome.
