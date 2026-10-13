# Cooperative daemon stop repair plan

Problem: signal handling performs journal/heartbeat IO, so interruption during an
append may reenter a lock. A stop request does not prevent later maintenance stages,
broker clock calls, post-cycle work or backoff sleeps. Exceptions after a completed
cycle count the same cycle again.

Measured source baseline: one signal handler heartbeat IO call; run has zero stop
checks between maintenance and clock/sleep, maintenance has zero between its stages;
`_run_symbol` increments both counts once in try and again in except. Reproduce these
with explicitly synthetic cycles and callback failures before editing daemon source.

Success: handler only sets the stop flag. On the next cooperative boundary the
daemon skips new work, including later maintenance stages, strategy screens,
clock/backoff and post-cycle tasks. Every attempted run_once increments each cycle
counter exactly once even if subsequent callbacks fail. Hard risk controls unchanged.

Minimum design: add direct stop checks at existing boundaries, reuse one counting
point, retain ordinary-loop STOPPED heartbeat outside the signal handler. No threads,
timeouts or forced cancellation. This is not a hard wall-clock stop guarantee:
filesystem IO and an in-flight request still must finish before the boundary.

- [x] Reproduce handler IO, later maintenance/clock/sleep, post-cycle and double-count defects.
- [x] After learning-evidence review releases daemon.py, make minimal boundary repair.
- [ ] Independent review pending; focused daemon/shutdown regressions and lint/type pass below.

Source is intentionally frozen while the earlier learning change is being reviewed.
Verify with `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_daemon.py
-k "stop_boundary or exactly_once" --tb=short'` then the entire daemon test file.

Red command above returned **7 failed, 44 deselected in 0.95s** on the frozen
learning-evidence source. All failures are the intended source defects: handler
heartbeat call, clock and sleep after stop, four later maintenance operations,
post-cycle call, 15s backoff and counts 2 instead of 1 after either callback failure.
No daemon source was changed for this reproduction.

Added an actual synthetic two-strategy library regression: stop during the first
screen still screens the second. Expanded command returned **8 failed, 44 deselected
in 1.04s**; the additional failure is exactly `['first', 'second']` instead of `['first']`.

## Repair execution

Parent released daemon source after learning commit `20af2ba` was accepted. First
removed signal-handler IO and moved both attempt counters to one point outside the
exception branch. Make focused handler/callback/post-cycle-count checks returned
**3 passed, 49 deselected in 0.34s**. Then run-loop and broker backoff stop boundaries
returned **3 passed, 49 deselected in 0.33s**. Added explicit checks between existing
maintenance stages and strategy screens; all original regressions returned **8 passed,
44 deselected in 0.32s**.

Additional reproduction: stop during post-cycle reflection still called lifecycle
and curriculum work. Red **1 failed, 52 deselected in 0.63s**. Added the same direct
checks between post-cycle evidence read, reflection and lifecycle. No cancellation
thread or hard timeout was added. A returned record still updates last_cycle_id
and its attempt count before skipping post-cycle work.

One validation invocation named a nonexistent `test_maintenance_causality.py` and
ran zero tests; corrected to the actual daemon and shutdown-budget test files.
Final expanded results are recorded once complete, followed by lint/type and review.

Further entry-boundary reproduction showed an already stopped daemon still started
run_once and a broker clock read: **2 failed, 53 deselected in 0.66s**. Direct guards
now refuse new symbol/clock work before incrementing counts or contacting the broker.
Scoped lint initially found unused `signum` after removal of handler IO; renamed it
`_signum`, preserving the positional signal callback API. Removed the obsolete
coverage exclusion because the real handler is directly exercised by the regression.

Final verification: `make test ENVFILE=/dev/null
TEST_ARGS='tests/autopoiesis/test_daemon.py tests/autopoiesis/test_shutdown_budget.py
--tb=short'` -> **63 passed in 0.79s**. `make lint-files ENVFILE=/dev/null
FILES='src/autopoiesis/daemon.py tests/autopoiesis/test_daemon.py'` -> **All checks passed**.
`make type ENVFILE=/dev/null` -> **49 source files clean**. `git diff --check` -> zero.
Source and tests frozen for independent review; no commit or actual service stop.
These synthetic tests establish cooperative behavior and exact attempt counting,
not a measured production shutdown duration. In-flight requests and filesystem
operations retain their actual completion time; no hard wall-clock guarantee.

## Quality review: startup boundary and completed-cycle identity

Problem/baseline: one stop during the initial RUNNING heartbeat still starts broker
reconciliation and appends its review event before STOPPED. A completed run_once
followed by a failing cycle-count callback produces counts 1/errors 1 but leaves
last_cycle_id at the previous cycle. Reproduce both with synthetic callbacks first.

Success: a startup stop skips reconciliation yet reaches normal STOPPED heartbeat,
signal restoration and lock release. A non-None returned record updates its cycle
identity before any callback can fail. Counts and existing reconciliation risk gate
remain unchanged; stopping never permits a trading cycle to skip reconciliation.

Minimum design: guard the startup reconciliation call with the existing stop flag;
move the existing cycle-id assignment immediately after a returned record. No new
state or exception policy. Verify two focused regressions then full daemon/shutdown
tests, scoped lint/type, and independent re-review.

- [x] Reproduce both quality findings, repair, verify and freeze for re-review.

Red `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_daemon.py
-k "startup_stop_boundary or exactly_once_when_callback" --tb=short'` ->
**2 failed, 54 deselected in 0.38s**. Trace still included broker-reconcile and
reconcile-event; last_cycle_id remained `previous-cycle` after the callback failure.

Repair: startup reconciliation is guarded by the sticky stop flag; the normal loop
tail still writes STOPPED and finally restores signals/releases the lock. Assign a
returned non-None record's cycle_id immediately after run_once, before the counter
callback. Full focused daemon/shutdown Make run -> **64 passed in 0.79s**; scoped
lint -> **All checks passed**; type -> **49 source files clean**; diff check -> zero.
Re-frozen for independent re-review; no commit or service mutation by this agent.

Independent spec review approved original 63 tests; quality review reproduced two
additional defects, then independently re-reviewed the fixes: **64 passed in
0.84s**, scoped lint and diff check clean. Startup stop skips reconciliation and
reaches STOPPED/lock release; callback failure keeps the completed record ID with
one attempt and one error. Both blockers closed. Review is complete; actual
production stop-duration measurement remains a deployment follow-up.
