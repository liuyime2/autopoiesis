# Refuse every actual curriculum model request after stop

Written planning phase before source changes. Use one bounded Superpowers implementation followed by independent specification and quality reviews.

Problem / measured baseline: full frozen unit10+11 check passed 1,747 tests / 6 skipped in 88.01s. Final independent combined review passed 303 focused tests but an actual-code synthetic probe observed request stop flags [False, True] inside StructuredCurriculumAgent's parser retry. The actual generate_curriculum_task_json adapter also issues phase A then phase B within one outer transport invocation, so caller-only checks cannot refuse the second request. Each new request has a 600s declared timeout and can exceed the unchanged 630s stop budget. No actual broker/model runtime operation was used for this probe.

Success / minimum design: guard each actual _ollama_curriculum_transport.call before engine.transport using the owning daemon's existing stop flag. Bind a small optional predicate in the existing adapter and shared _build_agent_daemon assembly; default standalone calls preserve behavior. The late-bound daemon callback must only run after construction. Stop refusal must be a distinct non-transport failure so it cannot trigger a fresh transport retry or false completed proposal; retain genuinely completed audit rows and existing parsing/capability validation when not stopped. No new cancellation framework, risk changes, raised timeout, live endpoint, interrupted in-flight IO or rewritten history.

Verification: labelled synthetic tests using the actual assembly/adapter/generator prove zero new actual HTTP transport requests for stop at entry, phase-A completion, parser retry and capability retry, and unchanged unstopped two-phase/retry behavior. Verify callback belongs to the assembled daemon, both before/after stop; standalone compatibility; truthful failure audit. Use existing classified test files. Focused Make tests, lint/type/diff; independent SPEC then QUALITY. Root reruns whole check, actual committed-tree fresh clone and exact CI before deployment.

- [x] Record measured independent blocker and written target before edits.
- [x] Implement minimum request guard and synthetic regressions: optional predicate immediately before each actual transport request; owning daemon callback is deferred until after assembly; refusal is RuntimeError rather than a retryable transport failure.
- [x] Focused verification: RED reproduced four failures; GREEN 241 focused tests, 10 request-boundary cases, changed-file lint and mypy passed. Commands and measured results below.
- [x] Independent specification and quality reviews approved.
- [ ] Final combined/full/new fresh clone/exact CI and actual paper deployment.

Unit12 implementation evidence (labelled synthetic HTTP responses; no external broker/model IO):

- RED: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py -k "assembled_curriculum or standalone_curriculum_adapter"'` — 4 failed / 4 passed / 104 deselected in 1.13s. Observed phase-A stop request flags `[False, True]`, parser and capability stops `[False, False, True, True]`, and missing refusal at stopped adapter entry. Unstopped controls and final completed-response audit passed.
- GREEN after the minimal source change: same command — 8 passed / 104 deselected in 0.45s.
- Focused regression: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_curriculum.py tests/autopoiesis/test_daemon.py'` — 241 passed in 6.31s. This includes the optional generator `spec_hint` branch controls.
- Final boundary verification after the test-only lint correction: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py -k "assembled_curriculum or standalone_curriculum_adapter or curriculum_request_predicate"'` — 10 passed / 104 deselected in 0.70s. These prove stop refusal at entry, phase A→B, parser retry and capability retry; separate assembled daemons retain separate predicates; standalone generation/retries remain available; successful final responses retain truthful completed audits while refusals record FAILED without proposals/admissions/backoff.
- `make lint-files ENVFILE=/dev/null FILES='src/autopoiesis/cli.py tests/autopoiesis/test_cli.py'` — all checks passed after correcting one test SIM108 finding.
- `make type ENVFILE=/dev/null` — success, no issues in 49 source files.
- Whole-worktree `git diff --check` reported pre-existing root-owned `docs/evidence/fresh-clone.log:378` trailing whitespace. Unit12 does not edit that file. `git diff --check -- src/autopoiesis/cli.py tests/autopoiesis/test_cli.py docs/superpowers/plans/2026-10-13-curriculum-request-stop.md` — exit 0, no findings.

Scope remains source `cli.py`, existing classified `test_cli.py`, and this plan. No full gate, self-test, fresh clone, runtime/service/network operation or commit was run by the implementer. Final deployment and independent reviews remain owned by root.

Root acceptance: independent SPEC reran 241 focused tests in 6.15s; QUALITY reran 241 in 6.41s. Both approved the owning-daemon guard, all internal retry/request boundaries, truthful audits, standalone compatibility, scoped lint and diff checks. The new full Make check also passed 1,757 tests / 6 skipped in 47.53s with thread warnings fatal. New fresh-clone, exact GitHub CI and actual paper deployment remain pending.
