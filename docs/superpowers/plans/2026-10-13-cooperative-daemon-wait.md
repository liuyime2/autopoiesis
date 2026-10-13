# Bound stop latency during the daemon's default wait

Written planning phase before edits. Superpowers implementation, independent
specification then quality review and final combined review apply.

**Problem / measured baseline:** Frozen 56c1875 full Make check passed **1,707 /
6 skipped in 44.04s**, with thread warnings as errors. A subsequent source audit
found the default `AgentDaemon.sleep = time.sleep` resumes after its flag-only
SIGTERM handler. Closed scheduler wait is `min(maintenance=900, stale*0.75=675)`;
the derived stop budget is **630s**, and its stage inventory omits scheduler wait.
This permits forced termination even with no in-flight order. An explicitly
synthetic two-second wait using the actual AgentDaemon default and SIGTERM through
a temporary Make recipe returned **2.087s after SIGTERM**, stop flag true, exit 0.
The probe used `AgentConfig.from_env({})`, no broker/model/runtime writes. An
initial probe setup failed because AgentConfig requires constructor fields; no
sleep measurement is attributed to that failed attempt.

**Success / minimum design:** Preserve the signal handler's flag-only, no-IO
boundary and all in-flight transaction completion. Replace only default daemon
waiting with bounded polling of the stop flag, at most **1s** between checks.
Respect the requested total delay using a monotonic deadline; do not spin or
shorten normal market cadence. Keep explicit injected sleep callbacks unchanged
for existing deterministic tests. Route every production wait using the existing
`self.sleep` seam, including closed-market, round, broker backoff and evidence
waits. Report the declared cooperative wait bound in the existing shutdown stage
inventory, without increasing service timeout or weakening any risk/market rule.

**Verification:** Meaningful synthetic real-process SIGTERM regression interrupts
a long default wait (process exits well before requested duration); normal default
waiting does not return early, and explicit injected callbacks retain their
contract. Verify flag-only signal regression and all existing stop/cadence tests.
Use focused Make daemon/scheduler/shutdown-budget regressions, lint and type; then
two independent reviews and final combined re-review. Root must run a new full
frozen suite, committed-tree fresh clone and exact CI. The earlier 1,707-pass run
does not certify this new source. No real service/broker/model/runtime operation
inside implementation or review, and no whole gates run by subagents.

- [x] Record source bounds and actual scaled synthetic SIGTERM baseline.
- [x] Implement default cooperative wait and honest stage inventory.
- [x] Focused verification and independent specification/quality reviews passed.
- [ ] Final combined/full/fresh-clone/CI and actual cooperative paper switch.

## Bounded unit 9 implementation evidence

The default `sleep=None` selects `_cooperative_sleep`; explicit callbacks are kept
by identity, including a falsey callable. The cooperative method uses a monotonic
deadline and blocking sleeps of at most `COOPERATIVE_WAIT_POLL_SECONDS = 1.0`.
Existing `self.sleep` call sites remain the single seam, and `_request_stop`
remains a flag-only handler. The shutdown inventory reads the polling declaration
through its existing AST reader; the derived budget remains **630s**.

Before changing production code, the new inventory regression failed because the
wait stage was absent and the polling regressions failed against the captured
`time.sleep` default. An initial subprocess readiness wrapper could not observe
that captured builtin; corrected readiness then reproduced the actual bug:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_shutdown_budget.py
-k sigterm_interrupts_actual'` failed with `TimeoutExpired` **3s after SIGTERM**
during the synthetic 30s default wait. The child was killed and reaped by cleanup.

After implementation, all six targeted new regression cases passed (**2.49s**).
The synthetic SIGTERM child exited cleanly with the stop flag true within the
test's **2s** post-signal threshold. A real 30ms ordinary wait did not return early;
labelled synthetic monotonic-clock cases checked chunk bounds and deadline handling
with and without oversleep. These are test results, not real broker/model results.

Focused verification on this unit:

```sh
make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_daemon.py tests/autopoiesis/test_daemon_heartbeat.py tests/autopoiesis/test_scheduler.py tests/autopoiesis/test_shutdown_budget.py -W error::pytest.PytestUnhandledThreadExceptionWarning'
# 100 passed in 4.09s
make lint-files ENVFILE=/dev/null FILES='src/autopoiesis/daemon.py tools/shutdown_budget.py tests/autopoiesis/test_daemon.py tests/autopoiesis/test_shutdown_budget.py'
# All checks passed!
make type ENVFILE=/dev/null
# Success: no issues found in 49 source files
git diff --check
# exit 0
```

Unit frozen for independent specification then quality review. No commit, full
suite, fresh-clone gate, CI gate, service operation or broker/model call was
performed by this implementation unit; those remaining checks stay with root.

Independent specification review approved **100 passed in 3.91s**, then quality
review independently approved **100 passed in 3.91s**, focused lint and diff check.
No in-flight stage was shortened and no real daemon was signalled by these reviews.
