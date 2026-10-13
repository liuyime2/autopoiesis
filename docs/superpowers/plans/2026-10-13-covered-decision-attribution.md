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

- [ ] Add labelled synthetic regressions in existing test files: actionable LLM
  with empty library, outside-universe snapshot, selector failure, and a fallback
  policy that would choose a different strategy. Verify no actionable decision,
  no model invocation and no policy reselection. Reproduce failures through Make.
- [ ] Minimum production change in src/autopoiesis/llm_decision.py: check frozen
  selection after context preparation and return explicit abstention; replace
  no-selected fallback with explicit abstention. Update the misleading comment
  that a degraded no-strategy context may still trade. Do not add a new layer.
- [ ] Run relevant existing LLM/provenance/learning tests via Make and scoped lint.
  Repair synthetic fixtures only when they assumed uncovered trading; retain
  explicit tests for successful covered LLM and normal fallback. Record counts.
- [ ] Independent specification and quality reviews. Commit one coherent unit
  after root reviews actual diff/status/log; reconcile parent readiness plan.

This unit prevents unsupported trading. It does not discover alpha, establish
LLM benefit or guarantee any order at the next open.

## Evidence

Baseline is the inspected production branch; red/green command results are
recorded after real test execution, not filled in advance.
