# Recover a daemon whose active service stopped making progress

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Have the existing watchdog detect and recover an active daemon with a stale heartbeat, while leaving a healthy daemon, a missing-startup heartbeat, and an orderly shutdown alone.

**Architecture:** Keep systemd as the process supervisor and `HealthMonitor` as the source of heartbeat age/PID/status truth. The watchdog checks liveness only after systemd reports the service active; it restarts an active service only when an existing heartbeat proves it stale/dead, and retains the existing reset-failed/start path for an inactive service. It does not inspect broker credentials, place orders, or change Guardian behavior.

**Tech Stack:** Python 3.10+, pytest, systemd user units, existing Makefile.

---

## Evaluation baseline

- **Problem:** `_watchdog()` in `src/autopoiesis/cli.py` returns success whenever `systemctl is-active autopoiesis.service` says active. A process can therefore be alive at the unit level but wedged with an old heartbeat; the timer reports “nothing to do” and never attempts recovery.
- **Measured evidence:** At 2026-10-10 19:23 EDT, systemd recorded two daemon stops timing out at its configured `TimeoutStopSec=30s`, followed by SIGKILL. The current daemon heartbeat age/PID is independently measurable through `HealthMonitor`. Existing watchdog only checks systemd active state (`cli.py::_watchdog`).
- **Success criterion:** A stale/dead heartbeat on an active service causes exactly one `systemctl restart`; a fresh live heartbeat causes no state-changing systemctl call; missing heartbeat during startup and status `STOPPING` do not cause restart; inactive/failed recovery still clears the start limit then starts once. Restart failure is written to watchdog output.
- **Verification:** Run the focused watchdog tests first through `make test PYTEST_ADDOPTS=...`, observe each new test fail before implementation, then pass them. Run `make test` and `make verify` serially in this worktree after the implementation. Validate the actual deployment only by running the read-only watchdog path or a controlled unit copy; do not restart the live daemon during this task.

### Follow-up evaluation: systemctl timeouts

- **Problem and measured baseline:** `_watchdog.systemctl()` passes `timeout=60` to `subprocess.run()` but does not catch `subprocess.TimeoutExpired`; a stalled `systemctl` call therefore escapes as an exception. If the initial `is-active` timeout were treated as an ordinary non-active result, execution could incorrectly continue into `reset-failed` and `start` while service state is unknown.
- **Success criterion:** Every systemctl timeout is reported with the operation and timeout duration. An initial `is-active` timeout issues no state-changing command; a `reset-failed` timeout does not continue into `start`. A timeout during `restart`, `start`, or post-action verification ends that watchdog run without further recovery actions and prints a concrete failure.
- **Verification:** Add deterministic timeout tests for initial state inspection, stale-daemon restart, inactive-daemon start, and post-restart verification. Run the focused tests through Makefile, `make lint`, and `git diff --check`; do not run the full gate or touch the deployed service/runtime data in this follow-up.

### Follow-up evaluation: unknown systemd states

- **Problem and measured baseline:** `_watchdog()` enters `reset-failed` → `start` for every result that is not exactly return-code 0 with output `active`. That includes in-progress states, empty output, unrecognized status text, and query errors; the recovery branch does not validate the returned unit state before mutating it.
- **Success criterion:** Only a nonzero `is-active` result whose normalized output is exactly `inactive` or `failed` may enter `reset-failed` → `start`. Empty/unknown output, `activating`, `deactivating`, and query errors print the available reason and issue no state-changing systemctl command. Existing explicit `inactive` and `failed` recovery remains unchanged.
- **Verification:** Add deterministic Makefile-driven tests for both recoverable states and each unknown/in-progress/error case, assert the exact command sequence and preserved query detail, then run the focused watchdog tests, `make lint`, and `git diff --check`. Do not run the full gate or touch service/runtime state.

### Follow-up evaluation: reset-failed command errors

- **Problem and measured baseline:** The inactive-service path stops when `reset-failed` times out, but currently ignores an ordinary nonzero return code and proceeds to `start`. The mocked systemctl boundary can reproduce this with `reset-failed` returning nonzero and an error string; the current sequence then includes `start`.
- **Success criterion:** Every nonzero `reset-failed` result, including non-timeout command errors, is reported with the operation and available detail and prevents `start` from running.
- **Verification:** Add a deterministic test for a nonzero `reset-failed` return with an error message, prove it fails before the fix because `start` is called, then run the focused watchdog tests, `make lint`, and `git diff --check`. Do not run the full gate or touch service/runtime state.

## File map

- Modify `tests/autopoiesis/test_cli_startup.py` to pin healthy, stale, missing-heartbeat, stopping, and inactive service cases with a temporary heartbeat and mocked `systemctl` boundary.
- Modify `src/autopoiesis/cli.py::_watchdog` to read `HealthMonitor` state and perform bounded restart only for an existing stale/dead heartbeat.
- Do not change `tools/watchdog.service.in`, signal timeout, trading code, risk limits, journal, or any runtime data in this task.

## Task 1: Add failing behavior tests

- [x] Extend the existing watchdog test so the healthy path has a fresh heartbeat for the current process and proves it issues no restart.
- [x] Add a stale heartbeat case with the same current PID and timestamp older than `stale_after_seconds`; assert the command sequence contains exactly one `restart` and then an `is-active` verification.
- [x] Add missing heartbeat and `STOPPING` heartbeat cases; assert neither issues `restart`.
- [x] Preserve the dead-service assertions: `reset-failed` precedes exactly one `start` and does not invoke `restart`.
- [x] Run the focused test through Makefile and confirm the stale case fails because the implementation currently reports active and does nothing. RED observed `['is-active']`; expected `['is-active', 'restart', 'is-active']`.

## Task 2: Implement the minimum recovery check

- [x] Import/use `HealthMonitor` in `_watchdog` with the config heartbeat path and stale threshold.
- [x] If systemd says active, read the heartbeat. Missing data means “state unknown during startup”: log it and do not restart. `STOPPING` means an orderly stop is underway: leave it alone. A live payload means healthy: leave it alone. Any other present payload that `HealthMonitor.is_live()` rejects is stale/dead: restart once.
- [x] Check the restart command's return code and verify the unit becomes active. Print a concrete failure when restart does not complete; do not loop/retry inside one watchdog run.
- [x] Keep the existing inactive-service `reset-failed` → `start` path and its verification; only explicit `inactive` and `failed` states enter this path, and any `reset-failed` error stops before `start`.
- [x] Run focused tests; the watchdog startup test file passed 29 cases, including stale/STOPPING transitions, unknown states, command failures, and timeouts.

## Task 3: Regression and operational evidence

- [x] Run the full `make test` suite in this worktree; record counts and duration: `1195 passed, 3 skipped in 27.30s`.
- [x] Run the full `make verify` suite only after tests finish; the gate self-test must not run concurrently with tests. Result: 52 classes, 52 passed, 0 failed; 1,892 test executions across 80 files. Runtime-dependent checks accurately reported SKIP where this isolated checkout had no journal/heartbeat.
- [x] Confirm no unrequested files changed and inspect `git diff --check`, `git diff`, and `git status`. Code scope is `src/autopoiesis/cli.py` and `tests/autopoiesis/test_cli_startup.py`; this plan and the separate RSI plan are the only untracked planning documents. `make lint` and `git diff --check` passed.
- [x] Run `make daily-report DATE=2026-10-09` against the existing record to verify the already-fixed report caller. This was run against the main checkout's existing paper record; it exited 0 and wrote the report. The isolated worktree has no runtime journal, so no synthetic replacement record was used.
- [x] Record exactly which runtime commands were run. `make daily-report DATE=2026-10-09` (read/report only) and `systemctl --user start quant-report.service` (read-only oneshot, `ExecMainStatus=0`) were previously exercised against the main runtime; no trading daemon stop/restart, order, live mode, or credentialed action was run for this task.

## Execution evidence (2026-10-10)

- RED: stale-heartbeat test failed on the original implementation because watchdog only called `is-active`.
- RED: transition tests reproduced `RUNNING → STOPPING`, stale → fresh before final observation, `TimeoutExpired`, unknown/in-progress systemd states entering recovery, and nonzero `reset-failed` continuing to `start`.
- Focused GREEN: `PYTEST_ADDOPTS='tests/autopoiesis/test_cli_startup.py -q' make test` — 29 passed.
- Full regression: `make test` — 1,195 passed, 3 skipped, 27.30 seconds.
- Full gate, run after tests completed: `make verify` — 52 classes passed, 0 failed; 1,892 executions / 80 test files. The worktree's runtime checks that require a journal/heartbeat reported SKIP; this gate result is not runtime paper-trading evidence.
- Static checks: `make lint` and `git diff --check` passed.
- Runtime safety: no live daemon stop/restart, no broker order, no live-trading enablement. Report checks above did not place orders.

## Rollback / stopping condition

If heartbeat state is malformed or unavailable, the watchdog must leave an active daemon alone and report unknown; do not infer that an active process is unhealthy from a missing file. If the restart path can race with a normal shutdown or fails the focused tests, stop and revise this task before touching the deployed unit.
