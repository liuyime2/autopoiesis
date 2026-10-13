# Paper entry parity implementation plan

> **For agentic workers:** Use Superpowers subagent-driven-development with specification then quality review. Execute tests and scripts through Make.

**Goal:** Manual single-cycle paper operation uses the daemon's strategy, risk, scheduler, reconciliation and exclusive-writer checks.

**Architecture:** Delete the duplicate raw-LLM _run_once assembly. Reuse _run_daemon(config, max_cycles=1), which already exits without trading when its real scheduler reports closed. Extract the existing daemon assembly into a builder for the later read-only readiness probe; the builder does not run a daemon or executor.

**Tech Stack:** Existing CLI, AgentDaemon, existing CLI/startup/shadow tests. No new dependency.

## Measured baseline

At 209222b, _run_once constructs one separate TradingLoop using raw Ollama and
only three Guardian settings. It omits the daemon's strategy/provenance,
max_total_exposure, confidence/universe/short behavior, startup reconciliation and
PID lock. _run_daemon uses the full configuration and owns the single-writer lock.
Static inspection found exactly two loop assemblies. The actual Make smoke recipe
is read-only tools/alpaca_smoke.py, although its help incorrectly calls it a cycle.

## Success and minimum changes

1. src/autopoiesis/cli.py: `_run_once(config)` delegates to
   `_run_daemon(config, max_cycles=1)`. One-cycle mode has the same production
   pipeline/locks/risk controls; actual closed clock gives no trading cycle and
   bounded mode exits. Never run it alongside the active daemon to prove behavior.
2. Move the existing assembly beginning at `journal = JsonlJournal(...)` through
   `daemon = AgentDaemon(...)` unchanged into
   `_build_agent_daemon(config: AgentConfig, client) -> AgentDaemon`, returning the
   assembled object. Keep current validation and client factory in _run_daemon;
   call the builder then `daemon.run(max_cycles=max_cycles)`. Existing exposed
   operations and configuration retain their meanings.
3. Makefile help: accurately label smoke as read-only broker/snapshot verification;
   correct type/check help to their existing gating behavior. Add only an
   `install-service` target invoking `./autopoiesis install-service`, so deployment
   uses the required Make runner. No new installer implementation.
4. Document --once's scheduler/lock semantics and the read-only preview boundary.
   A successful closed single-cycle invocation proves scheduling refusal, not
   actual model/Guardian/filled-order behavior.

- [ ] Labelled synthetic regressions before edits: --once delegates bounded
  daemon; shared builder retains effective max exposure/trade/confidence/universe/
  manage-account settings; active daemon lock forbids a competing single-cycle
  writer; real-scheduler-closed fixture submits no order. Use existing test files
  and daemon fixtures, not a new parallel writer. Record red commands/counts.
- [ ] Implement the above deletion/extraction and Make wrapper; avoid unrelated
  refactoring. Existing daemon assembly callbacks/cost-basis/providers are retained.
- [ ] Run Make CLI/startup/shadow/daemon focused tests and lint/type. Preserve
  successful daemon wiring checks and meaningful shadow/short guarantees. Update
  old standalone-once fixtures to test the now-shared runtime contract.
- [ ] Specification review, then quality review; root status/diff/log and coherent
  commit. No live broker action, service changes or runtime writes in this unit.

## Evidence

Measured red/green results are appended after execution. The builder is a reusable
construction seam, not a second agent or a guarantee of profitable decisions.
