# Audit repair execution plan

Goal: make research and strategy evaluation fail closed on untrustworthy evidence,
restore reproducible checks, and report what the paper record actually establishes.
Architecture: reuse the existing Make entry points, preregistration, journal and
evaluators. Preserve original records. No broker orders, risk relaxation or live mode.
Tech stack: Python, pytest, Make, existing systemd templates; no new dependencies.

## Baseline and acceptance

Baseline commit: 80d0593 (source baseline 49063c8). Main has an existing uncommitted
prereg.py path edit; preserve it. Implementation is isolated in codex/rsi-audit-repairs.
Native worktree tools did not return; a normal Git worktree provides the isolation.
planning-with-files is not installed; this written plan and evidence log provide
the repository's documentation gate, with installed Superpowers execution skills.

Measured prior audit: UTC CI 1 failed / 1323 passed / 5 skipped; local check 1329
passed in 315.80s. Research uses 134456 bars despite a six-month declaration;
862/1602 attributed cycles have symbol mismatch. These are baseline observations,
not claims about a new run. Reproduce focused failures before changing their cause.

- [x] 1. Deterministic CI and truthful provenance. Reproduce the VIX test under UTC;
  use explicit synthetic test dates without weakening the four-day production
  freshness rule. Fix Git status detection and remove accidental production-host
  work from ordinary tests. Verify via Make focused tests, lint and type checks.
- [ ] 2. Mandatory research contract. Connect registration verification, declared
  symbol/interval/window, tape validation, evaluator identity and trial persistence
  to driver.py, Makefile and research.service.in. Fix registry location without
  writing site-packages. Reject conflicting overlapping bars. Test refusals before
  backtests/ledger writes, exact repeat identity and supported rule constraints.
  Run the gate on an isolated copy of real cached bars; report actual outcomes.
- [ ] 3. Exclude contaminated learning evidence. Trace all journal consumers;
  centralize eligibility, preserve accounting/history, record exclusions, snapshot
  strategy universe for future decisions and refuse unknown historical attribution
  for strategy learning. Test mismatches at real consumers and audit real journal
  read-only. No retroactive invented strategy universes or lifecycle reversals.
- [ ] 4. Correct performance claims. Trace capital denominator and benchmark
  alignment. Replace unsupported relative-return claims with a verifiable contract
  or explicit unavailable status; keep owner flows separate and preserve real PnL.
  Test overlapping exposures/owner flows and run real read-only benchmark.
- [ ] 5. Reliability and remaining gaps. Inspect backups, health, stop budgets and
  deploy entry points. Repair measured defects with focused regression tests; run
  snapshot/restore in disposable paths. Offsite durability and long-term RSI uplift
  remain pending if an external destination or new real observations are needed.
  Operator clarification: no offsite destination exists; complete reliable local
  backups now. Offsite deployment remains a recorded follow-up, not a requirement
  that can be satisfied by another directory on this host.
- [ ] 6. Integration evidence. Independent spec/code review after each unit. Run
  make check, UTC tests, required verify gates and credential-free fresh clone on
  committed changes. Reconcile RSI plan/evidence/docs. Publish a reviewable branch
  and inspect GitHub CI if authentication permits; never report local as remote.

## Evidence and follow-up

Task 1 accepted after independent spec and quality reviews. The quality review
identified a host Git signing dependency in the disposable fixture; after disabling
signing/hooks, the exact injected signing-config reproduction passed 4 tests.
Root independent Detroit run: 39 passed, 2 explicit host skips in 1.56s, with
ENVFILE=/dev/null. Independent reviewer UTC run: 39 passed, 2 skips in 1.47s.
Production freshness code is unchanged. These are focused checks, not remote CI.

### Task 2 design before implementation

Problem: production Make/service callers omit the optional spec, and driver.main
does not verify registration or tape quality even when passed one. Overlapping
files silently prefer the first price; registry defaults to source-package storage.
Baseline: 2 production search callers omit SPEC; the latest audit measured 134456
bars over ten years, 0 registered-contract checks at the driver boundary. The
existing loader-integration test contains `if False` and never executes the loader.

Acceptance: no CLI/Make/scheduled search without an explicit registered supported
contract; no backtests or writes for missing/changed/unsupported contracts, symbol
or interval mismatch, malformed prices or conflicting overlapping timestamps.
Spec bytes, evaluator implementation, costs/folds and ordered data identify every
trial and its repeat cache. Persistence failure makes the command fail. Scientific
preregistration before seeing data is not established merely by registering an
already studied historical tape: label such runs integrity diagnostics.

Minimum design: one shared prepare/validate function for driver and tape-checker;
relative runtime registry (with documented environment override, no site-packages),
explicit versioned supported threshold-grid rule/metric and evaluation settings,
and strict loader interval/provenance/conflict checks before fingerprinting. Reuse
walk-forward and its cost controls, fail unsupported promises instead of inventing
an optimizer. Scheduled Make entry requires an operator-supplied registered SPEC;
never auto-register a hypothesis after reading data. Leave unrelated exploratory
legacy scripts labelled diagnostic, unable to promote production strategies.
Verification: failing CLI/Make/loader integration tests then focused research tests;
read-only real-cache preparation and isolated real-ledger runs through Make.

### Task 1: deterministic checks and Git provenance

Problem: routine tests depend on wall-clock date and production-host state; Git
provenance interprets empty command output as a dirty tree.
Baseline reproduced: `TZ=UTC make test
TEST_ARGS='tests/autopoiesis/test_risk_judgment.py::test_a_calm_index_leaves_the_forecast_alone'`
returned **1 failed in 0.15s**, with `2026-10-08 is 5 days old`. Inspection confirms
`git diff --quiet` emits no stdout on both clean and dirty trees, and the ordinary
synthetic provenance test leaves the real journal path and broker clock reachable.
Prior full-suite timings (315.80s local / about 29s CI) are audit observations, not
a new attribution measurement.

Success: deterministic synthetic fresh/stale/future and four-day-boundary tests,
unchanged production freshness guard, truthful clean/unstaged/staged/untracked/error
Git state, and ordinary provenance tests with disposable files and no broker access.
Verification: first failing Git regressions against real disposable repositories,
then focused `TZ=UTC make test`, `make lint-files`, and `make type`. Full integration
and fresh-clone checks belong to task 6.
Design: freeze only the test clock; reuse Git porcelain status; isolate synthetic
provenance input paths and broker reads; retain real-host checks through an explicit
Make target and opt-in environment setting. No production trading-path changes.

Implementation verification (2026-10-13 UTC / 2026-10-12 local):

- Red: `make test TEST_ARGS='tests/autopoiesis/test_provenance_unified.py -k
  git_provenance'` -> **4 failed, 3 passed, 6 deselected in 0.65s**. Clean tree,
  non-repository, missing Git and timeout all incorrectly printed `dirty: yes`.
- Green after Git fix: same command -> **7 passed, 6 deselected in 0.54s**.
- `TZ=UTC make test TEST_ARGS='tests/autopoiesis/test_risk_judgment.py
  tests/autopoiesis/test_provenance_unified.py'` -> **39 passed, 2 skipped in 1.60s**.
  The skips are explicitly opt-in real-host checks. Freshness ages 0, 4, 5 and -1
  are exercised with synthetic dates; production `risk_judgment.py` is unchanged.
- `make lint-files FILES='tools/provenance.py
  tests/autopoiesis/test_risk_judgment.py tests/autopoiesis/test_provenance_unified.py'`
  -> **All checks passed**.
- `make type` -> **Success: no issues found in 48 source files**.
- `make -n test-provenance-host` -> explicit opt-in pytest command printed; target
  was not executed, so no real-host timing or broker result is claimed.

Real-host verification is available as `AUTOPOIESIS_JOURNAL=/absolute/path/journal.jsonl
make -C /path/to/deployed/checkout test-provenance-host`. Use a checkout holding
the intended `runtime/autopoiesis/heartbeat.json` and `research_trials.jsonl`;
this isolated worktree has neither. The journal uses its documented external
configuration path, and the opt-in target can query the broker clock if credentials
are configured. Ordinary tests patch both broker clock and journal path, and the
synthetic print test asserts an empty disposable journal was read successfully.

Task 1 implementation and focused verification are complete; independent spec and
quality reviews and a commit remain pending. Full suite, remote CI and fresh-clone
evidence remain task 6, so the top-level task checkbox is not yet marked complete.

Quality review follow-up: the disposable Git fixture inherited host signing and
hooks configuration. Reproduction with `GIT_CONFIG_COUNT=2
GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=true
GIT_CONFIG_KEY_1=gpg.program GIT_CONFIG_VALUE_1=/bin/false make test
ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_provenance_unified.py -k
actual_tree_state --maxfail=1'` returned **10 deselected, 1 error in 0.19s**;
the fixture commit exited 128 (`gpg failed to sign the data`). Plan: override
signing and hooks on the synthetic commit invocation itself. Success is the same
host-config reproduction passing all four tree states, plus focused lint. This
changes test setup only; production Git provenance must still read actual Git state.
Green after the fixture commit gained `-c commit.gpgsign=false -c
core.hooksPath=/dev/null`: the exact host-signing reproduction above returned
**4 passed, 10 deselected in 0.55s**. `make lint-files ENVFILE=/dev/null
FILES='tests/autopoiesis/test_provenance_unified.py'` returned **All checks passed**.
`TZ=UTC make test ENVFILE=/dev/null
TEST_ARGS='tests/autopoiesis/test_risk_judgment.py tests/autopoiesis/test_provenance_unified.py'`
returned **39 passed, 2 skipped in 1.47s**. Review finding addressed; quality
re-review and commit are still pending.

Each unit records commands, outputs, root cause, limits and commit below. Synthetic
fixtures prove software behavior only. Real research results cannot establish live
profitability. A profitable, autonomous recursive improvement loop still requires
frozen control/treatment experiments and clean paper observations over time.
