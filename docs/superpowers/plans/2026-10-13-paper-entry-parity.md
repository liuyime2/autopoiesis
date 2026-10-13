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

- [x] Labelled synthetic regressions before edits: --once delegates bounded
  daemon; shared builder retains effective max exposure/trade/confidence/universe/
  manage-account settings; active daemon lock forbids a competing single-cycle
  writer; real-scheduler-closed fixture submits no order. Use existing test files
  and daemon fixtures, not a new parallel writer. Record red commands/counts.
- [x] Implement the above deletion/extraction and Make wrapper; avoid unrelated
  refactoring. Existing daemon assembly callbacks/cost-basis/providers are retained.
- [x] Run Make CLI/startup/shadow/daemon focused tests and lint/type. Preserve
  successful daemon wiring checks and meaningful shadow/short guarantees. Update
  old standalone-once fixtures to test the now-shared runtime contract.
- [x] Specification review, then quality review; root status/diff/log and coherent
  commit. No live broker action, service changes or runtime writes in this unit.

## Evidence

Measured red/green results are appended after execution. The builder is a reusable
construction seam, not a second agent or a guarantee of profitable decisions.

### Implementation evidence (2026-10-13)

All commands below ran in `.worktrees/rsi-audit-repairs`. Entry fixtures use
synthetic credentials, a local broker fixture, isolated temporary journal/library/
heartbeat/PID paths, and an explicit HTTP guard. Their results establish code
behavior only, not real model quality, paper fills or profitability. The closed
scheduler fixture runs the real scheduler/startup reconciler/daemon but isolates
maintenance; daemon maintenance remains covered by the existing focused suite.

- Fixture isolation correction: the initial delegate regression omitted a factory
  stub, so the old entry attempted a broker read with synthetic credentials and
  returned data unavailable. No submission/model execution occurred. This initial
  result is not accepted as proof. The factory stub was added, then an explicit
  `requests.Session.request` failure guard was added to all new entry regressions.
- Corrected red: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py -k "once_delegates or shared_builder or once_rejects or once_closed"'`
  produced **6 failed, 22 deselected in 0.72s** with the factory stub installed.
- Expanded guarded red: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_cli_startup.py -k "once_delegates or shared_builder or once_rejects or once_closed or operator_help or daemon_startup_path"'`
  produced **8 failed, 1 passed, 50 deselected in 0.99s** before production edits.
  Failures were missing shared builder, missing bounded delegation/PID/closed
  lifecycle behavior, and inaccurate Make help.
- First implementation run exposed a fixture schema error: `source="synthetic"`
  is intentionally rejected by `DataSnapshot`. The fixture retains its explicit
  synthetic docstring/comment and uses the accepted `alpaca` source discriminator,
  matching existing Guardian fixtures. No production validation was weakened.
- Green entry/startup regressions: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_cli_startup.py -k "once_delegates or shared_builder or once_rejects or once_closed or daemon_startup_path"'`
  produced **8 passed, 51 deselected in 0.48s**.
- Make operator contract: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py -k operator_help'`
  produced **1 passed, 28 deselected in 0.53s**. It copies the Makefile and dry-runs
  the installer; it does not install or change services.
- Focused regression suite: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_cli_startup.py tests/autopoiesis/test_shadow.py tests/autopoiesis/test_daemon.py tests/autopoiesis/test_daemon_heartbeat.py tests/autopoiesis/test_broker_client.py'`
  produced **142 passed in 12.27s**.
  Final rerun after the direct-return lint fix: **142 passed in 11.95s**.
- `make lint-files ENVFILE=/dev/null FILES='src/autopoiesis/cli.py tests/autopoiesis/test_cli.py tests/autopoiesis/test_cli_startup.py'`
  initially found `RET504` on the extracted assignment/return. The builder now
  returns `AgentDaemon(...)` directly, with every constructor argument/provider/
  callback retained. Rerun: **All checks passed!**
- `make type ENVFILE=/dev/null`: **Success: no issues found in 49 source files**.
- `git diff --check`: exit 0. No commit, full gate, service mutation or operational
  paper cycle was run in this unit. Specification and quality review remain pending.

The shared assembly count is now one. `--once` returns the bounded daemon exit
status and uses its journal/heartbeat instead of printing a standalone cycle.
README and CLI help document this behavior, the exclusive-writer constraint,
closed-market evidence limit, and the read-only smoke/construction boundary.

Independent specification review approved the six-file diff and preserved complete
constructor/provider/callback assembly. Subsequent independent quality review
approved and reran `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_cli_startup.py'`: **59 passed in 8.79s**;
diff check passed. Root inspected status/diff/log before the coherent commit.
These are synthetic wiring/lifecycle checks, not an actual closed or open operational
single-cycle run. The final implementer focused rerun after the direct-return fix
was **142 passed in 11.95s**.
