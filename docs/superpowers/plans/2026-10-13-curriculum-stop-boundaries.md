# Refuse new curriculum stages after a cooperative stop

Written planning phase before edits. Use Superpowers bounded implementation,
independent specification then quality review, then final combined re-review.

**Problem / measured baseline:** Unit 9 resolves default waiting and passed full
Make check **1,713 / 6 skipped in 45.94s**. Final independent combined review then
measured an adjacent gap in `_curriculum_proposal`. A synthetic transport on the
actual StructuredCurriculumAgent sets the flag and raises TimeoutError; transport
calls observed flags **[False, True]**, proving one new request after stop. Other
validated synthetic task/spec probes show: stop at entry still starts proposal,
admission and screen; stop during a successful proposal still starts admission
and screen; stop during admission still starts screen. Completed proposal and
admission audit rows currently remain. Focused combined tests **234 passed in
14.55s** did not cover these boundaries. No real broker/model/service/runtime IO
was used by these probes. Starting a fresh 600s request after an interrupted
stage can exceed the unchanged 630s budget.

**Success / minimum design:** Surgical checks inside the existing method:
refuse entry when stopped; refuse a retry before and after its backoff if stopped;
preserve a genuinely completed proposal's audit row but do not start admission
after stop; preserve a completed admission's audit row but do not start screening
after stop. Keep failure reporting truthful when transport already failed, without
fabricating a proposal or successful retry. Stop during ordinary context/price
preparation must not begin the next bounded stage. Retain normal one-retry behavior
and all existing Guardian/admission checks when not stopped. No new cancellation
framework, raised timeout, risk change, rewritten history or interrupted file/order
transaction. Do not convert a stop into a successful new research experiment.

**Verification:** Synthetic regressions require zero new transport/admission/screen
calls after each measured boundary, exactly retained completed-result audit rows,
no false admission/proposal row on transport failure, and unchanged unstopped
retry/admission behavior. Use existing daemon test file plus focused Make
curriculum/admission/stop regressions, lint/type and diff checks. Independently
review both stages and final combined source. Root reruns frozen full check, a
new actual committed-tree fresh clone and exact CI; the prior clone is only a
baseline for its own tree. No whole gate or real runtime calls by subagents.

- [x] Record independent actual-code synthetic probes and full-run baseline.
- [x] Add minimum stage checks and targeted regressions.
- [x] Focused verification and independent specification/quality reviews passed.
- [ ] Final combined/full/new fresh-clone/exact CI and paper deployment.

**Implementation evidence (synthetic fixtures only):** Before source edits,
`make test TEST_ARGS='tests/autopoiesis/test_daemon.py -k "curriculum_stop_boundary or curriculum_transport_retry" --tb=short' ENVFILE=/dev/null`
reported **17 failed / 2 passed / 72 deselected in 2.12s**. Transport-stop and
backoff-stop both observed request flags **[False, True]**. Stage checks then
passed **19 / 72 deselected in 0.54s**. Additional assertions found lesson-stage
entry after stops during reflection, strategy-list or summary preparation:
**3 failed / 12 passed / 77 deselected in 0.98s** before the lesson-entry check.
Retry-context-stop coverage also verifies the original timeout is retained.

Final focused command:
`make test TEST_ARGS='tests/autopoiesis/test_daemon.py tests/autopoiesis/test_curriculum.py tests/autopoiesis/test_curriculum_feedback.py tests/autopoiesis/test_strategy_admission.py tests/autopoiesis/test_shutdown_budget.py tests/autopoiesis/test_signal_test.py tests/autopoiesis/test_daemon_heartbeat.py' ENVFILE=/dev/null`
passed **177 tests in 4.68s**. The 20 new parameterized cases retain completed
proposal/admission audit rows, refuse later stage entry, retain one truthful
timeout failure without a proposal on stopped retries, and preserve unstopped
one-retry behavior plus the existing missing-HOLD_BASELINE risk rejection.
`make lint-files FILES='src/autopoiesis/daemon.py tests/autopoiesis/test_daemon.py' ENVFILE=/dev/null`
passed; `make type-files FILES='src/autopoiesis/daemon.py' ENVFILE=/dev/null`
reported **no issues in 1 source file**; scoped `git diff --check` passed.
The daemon/test source is frozen for independent specification then quality
review. No whole gate, fresh clone, broker/model/service/runtime calls or commits
were performed by this implementation unit.

Root acceptance: independent SPEC approved with 177 passed in 4.43s; independent QUALITY approved with 177 passed in 4.22s. Both confirmed scoped lint/diff checks and no blocking findings. Final combined validation and deployment remain pending.
