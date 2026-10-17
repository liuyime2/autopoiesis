# Make concurrent journal verification fail on writer failure

Written planning phase before implementation; use Superpowers bounded implementer,
independent specification review, then quality review. This is a synthetic test
correction, not evidence of actual broker execution or unlimited lock fairness.

**Problem / measured baseline:** Frozen e28a2f1 full `make check ENVFILE=/dev/null`
reported 1 failed / 1,693 passed / 6 skipped in 210.38s, including an unhandled
writer-thread TimeoutError in `test_a_reader_never_sees_a_half_written_append`.
That test still passed: it increments the count before append succeeds, does not
capture thread failures, and does not assert the thread terminated after join.
An unlimited writer and 30 complete scans allow workload growth unrelated to the
finite reader/writer exclusion property. A read-only standalone reproduction via
`make test ENVFILE=/dev/null` with the exact test and
`-W error::pytest.PytestUnhandledThreadExceptionWarning --durations=1` passed one
test in 0.32s (call 0.06s); the full-run warning remains the measured failure, not
an assertion that the standalone run reproduced it.

**Success / minimum design:** Replace this test's unlimited workload with finite
actual append/read work and bounded synchronization that exercises concurrent
activity. Capture worker exceptions; count only completed appends; join and assert
worker termination; assert all expected unique cycle identities, no dropped or
partial records, and successful reader observations. A writer error or failure to
finish must fail the test. Preserve production `file_lock` and journal behavior;
do not increase lock timeouts, add arbitrary sleeps, or weaken safety assertions.
Reuse the adjacent deterministic reader-waits-for-writer test where useful rather
than adding a redundant lock framework. Test fixtures are explicitly synthetic.

**Verification:** Focused Make durability tests with thread warnings as errors,
the nearby journal/atomic/rotation regressions, lint and diff checks. Independently
review specification then quality; root reruns the full frozen suite and actual
fresh-clone harness serially before publishing exact CI and paper deployment.
No broker/model/service/runtime mutation and no whole-gate runs by reviewers.

- [x] Record actual warning, source-level acceptance gap and standalone measurement.
- [x] Implement finite concurrent verification with truthful failure propagation.
- [x] Focused verification: 28 durability tests, then 83 nearby regressions passed;
      injected synthetic writer timeout failed the corrected test as required.
- [x] Independent specification review, then quality review of the frozen change.
- [ ] Final combined review, full suite, fresh clone and exact CI.

## Implementer evidence (2026-10-13)

Only `tests/autopoiesis/test_durability.py` changed. The bounded test now performs
30 real append/read rounds using synthetic cycle fixtures, with timed start and
finish barriers. The completed-append count increments after append returns;
worker exceptions are captured and abort both barriers. Reader failures also
abort both barriers, and cleanup joins and asserts worker termination. Every scan
checks zero dropped lines and an ordered complete prefix; the final scan requires
exactly `seed,c1,...,c30`. The adjacent reader-blocks-on-writer regression remains
part of verification; production locks and timeouts are unchanged.

- `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_durability.py -W error::pytest.PytestUnhandledThreadExceptionWarning --durations=3'`
  passed 28 tests in 2.95s; corrected test call 1.51s.
- Failure-propagation probe temporarily replaced the worker's append with
  `raise TimeoutError("synthetic worker failure probe")`. With that explicit
  synthetic fault, `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_durability.py::test_a_reader_never_sees_a_half_written_append -W error::pytest.PytestUnhandledThreadExceptionWarning'`
  failed 1 test in 0.38s at `assert not worker_errors`, reporting the injected
  timeout. The real append was restored immediately before the next command.
- `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_durability.py tests/autopoiesis/test_journal.py tests/autopoiesis/test_journal_rotation_recovery.py tests/autopoiesis/test_journal_snapshot.py -W error::pytest.PytestUnhandledThreadExceptionWarning --durations=3'`
  passed 83 tests in 3.65s; corrected test call 1.56s.
- `make lint-files ENVFILE=/dev/null FILES=tests/autopoiesis/test_durability.py`
  reported `All checks passed!` after restoring the append.
- `git diff --check` exited 0. No whole gate, fresh clone, broker, model, service,
  or runtime mutation was performed by this implementer.

Independent specification review approved with **28 passed in 2.97s**; subsequent
quality review approved with **28 passed in 3.03s**, both treating thread warnings
as errors. The barriers permit concurrent attempts and bound workload; scheduling
does not guarantee contention or fair lock acquisition. The adjacent deterministic
blocking regression remains complementary evidence. Full/fresh-clone/CI pending.
