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

Clarification from the actual immutable-provenance audit: the prior 862/1602
"mismatch" count compared historical symbols with today's mutable strategy
universe. It is not proof of an immutable decision/snapshot mismatch. The new
read-only audit measures zero such symbol mismatches, but all 2,326 historical
cycles lack frozen decision-time provenance and are excluded from learning.

- [x] 1. Deterministic CI and truthful provenance. Reproduce the VIX test under UTC;
  use explicit synthetic test dates without weakening the four-day production
  freshness rule. Fix Git status detection and remove accidental production-host
  work from ordinary tests. Verify via Make focused tests, lint and type checks.
- [x] 2. Mandatory research contract. Connect registration verification, declared
  symbol/interval/window, tape validation, evaluator identity and trial persistence
  to driver.py, Makefile and research.service.in. Fix registry location without
  writing site-packages. Reject conflicting overlapping bars. Test refusals before
  backtests/ledger writes, exact repeat identity and supported rule constraints.
  Run the gate on an isolated copy of real cached bars; report actual outcomes.
- [x] 3. Exclude contaminated learning evidence. Trace all journal consumers;
  centralize eligibility, preserve accounting/history, record exclusions, snapshot
  strategy universe for future decisions and refuse unknown historical attribution
  for strategy learning. Test mismatches at real consumers and audit real journal
  read-only. No retroactive invented strategy universes or lifecycle reversals.
- [x] 4. Correct performance claims. Trace capital denominator and benchmark
  alignment. Replace unsupported relative-return claims with a verifiable contract
  or explicit unavailable status; keep owner flows separate and preserve real PnL.
  Test overlapping exposures/owner flows and run real read-only benchmark.
- [x] 5. Reliability and remaining gaps. Inspect backups, health, stop budgets and
  deploy entry points. Repair measured defects with focused regression tests; run
  snapshot/restore in disposable paths. Offsite durability and long-term RSI uplift
  remain pending if an external destination or new real observations are needed.
  Operator clarification: no offsite destination exists; complete reliable local
  backups now. Offsite deployment remains a recorded follow-up, not a requirement
  that can be satisfied by another directory on this host.
- [x] 6. Integration evidence. Independent spec/code review after each unit. Run
  make check, UTC tests, required verify gates and credential-free fresh clone on
  committed changes. Reconcile RSI plan/evidence/docs. Publish a reviewable branch
  and inspect GitHub CI if authentication permits; never report local as remote.

## Evidence and follow-up

Integration reconciliation (2026-10-13): Task 3 accepted as `20af2ba` after both
reviews and the real UNKNOWN-attribution audit. Task 4 benchmark/public repair
accepted as `72c03b0`/`2f68e5a` with real sealed accounting and publication review.
Final integration review found pre-existing CLI/daemon account-only profit-target
success paths; a new recorded follow-up unit `2026-10-13-profit-target-consumers.md`
is required before claiming all profitability verdict consumers are reconciled.
Task 5 accepted local backup `0ee9170` plus real restore/drill and hourly service
proof (`2305edb`), and cooperative stopping `996d895` after closing both additional
review blockers. Actual production shutdown timing and trading-source deployment
remain follow-ups. Offsite storage is pending by the user's explicit local-only
choice, not silently dropped. Installed sdist workspace registration passed actual
and independent release-check (`0fe69e8`).

Latest completed full check before the target-consumer follow-up: **1,484 passed /
6 skipped in 36.18s**, lint clean, 49-file mypy clean. Gate self-test passed. Initial
full verify found an actual tools-documentation naming mismatch, corrected and
independently rerun (37 tools documented). It also encountered the self-test's
temporary canary because root ran these two checks concurrently; serial focused
crash-recovery then passed 285 tests. A subsequent full gate read an incomplete
fresh-clone log while the harness rewrote it; that invocation is not accepted.
Final gate must run after the final clone log is complete, with no self-test mutation.

First actual clone setup failed because the host's default Python 3.12 lacks
venv/ensurepip; failure was correctly recorded. Explicit Python 3.10 created a
new independent virtualenv and is completing the nine Make steps. GitHub PR #10
is published. Its initial exact source `0fe69e8` passed all four remote jobs
(gate Python 3.10/3.11/3.12 and release); that is not proof for later commits.

Final research diagnostic under accepted accounting/evaluator code: evaluator
4501e3a03f4895726b38a5e699475666342b7f70bd09ad3e0b7332199e44e6ea,
identity 78b5241f4745caf70d416b682259e9a9a1dd08bb81d751c18fb3e3642e929bd5.
Same explicit registered spec and real 24,111-bar tape described below. Retained
the earlier 11 trials: 22 cumulative attempts, required-trades heuristic 47;
11 new backtests, 4 OVERFIT / 7 INSUFFICIENT / 0 PASS, no unrecorded outcome.
Measured `/usr/bin/time make research-search ENVFILE=/dev/null ...`: 52.96s wall,
50.55s user, 2.27s system, max RSS 195,416KB. Exact repeat: 6.22s wall, 0 backtests,
11 cached outcomes, still 22 cumulative attempts, same verdicts. This proves
identity/cache/ledger behavior on real data, not alpha or scientific preregistration:
the spec was registered after the historical window started. Original conflicting
live cache remains preserved and refused; operational canonical-cache choice is
still required before enabling scheduled research under this contract.

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

Task 2 regression baseline: `make test ENVFILE=/dev/null
TEST_ARGS='tests/autopoiesis/test_research_contract.py'` -> **16 failed in 0.73s**.
Missing/unregistered/unsupported specs reached loaders/backtests, overlaps silently
preferred a price, re-registration rewrote first registration, and persistence
failure returned zero. These fixtures are explicitly synthetic software checks.
Implementation design: reuse the actual grid (one TREND_FOLLOW per threshold plus
one FIXED_SIZE buy-hold), version its rule/metric/evaluator, declare thresholds,
exact candidate budget, costs, folds and training bars. `expected_trades` is intent
metadata, never an override of cumulative required-trades control. Identity hashes
contract bytes, actual evaluator implementation and settings; historical generic
specs may retain metadata registrations but cannot execute supported CLI research.

### Task 2 implementation and focused evidence

Root cause: spec was optional at both production callers and driver boundary;
registration checked only copy existence/bytes; the loader silently preferred the
first conflicting price and mixed intervals; repeat matching omitted execution
settings and evaluator identity; an unpersisted result still exited zero. The old
loader-integration test never called its loader (`if False`).

The shared preparation gate now freezes one read of verified spec bytes, validates
only the existing versioned threshold grid and metric, selects symbol/interval/window,
checks selected bars and overlap consistency, and prepares complete evaluation
identity. Contract declarations require thresholds, exact candidate budget, costs,
fold count and training length; unknown execution promises are refused. Each trial
records spec/implementation hashes, actual settings, ordered tape fingerprint,
first registration metadata and factual pre-window/after-window registration scope.
Registrations are immutable and idempotent; corrupt metadata/copies and future
registration timestamps are refused. Registry and ledger defaults are relative
runtime paths with documented external environment overrides, independent of
installed source locations.

Make and scheduled research require an externally supplied registered SPEC. The
run derives exact fetch symbol/window/interval from that verified contract, passes
the same explicit cache directory to fetch/search, and stops on fetch failure.
The fetcher's filename now uses its actual requested interval. It never
post-registers a hypothesis. Exact repeats reuse stored outcomes; changed contracts,
settings, implementations or tapes count as new attempts. Corrupt ledgers stop the
CLI instead of resetting attempt counts, and incomplete persistence fails the
command. A nonblocking ledger session lock excludes simultaneous CLI evaluation
before cumulative ledger reads or backtests.

Measured checks (all with disposable synthetic fixtures; no real-market conclusions):

- Initial red contract baseline: `make test ENVFILE=/dev/null
  TEST_ARGS='tests/autopoiesis/test_research_contract.py'` -> **16 failed in 0.73s**;
  first green after driver/gate repairs -> **16 passed in 0.38s**.
- Concurrency red: `make test ENVFILE=/dev/null
  TEST_ARGS='tests/autopoiesis/test_research_contract.py -k concurrent'` ->
  **1 failed, 32 deselected in 0.45s**, reaching trial reads while a separate process
  held the ledger. Green with session lock -> **1 passed, 32 deselected in 0.36s**.
- Final focused suite: `make test ENVFILE=/dev/null
  TEST_ARGS='tests/autopoiesis/test_research*.py tests/autopoiesis/test_replay_bar_fetch.py'`
  -> **132 passed in 6.77s**. Includes an actual tempfile bar cache through the shared
  gate, invalid/missing/changed/unsupported contracts, corrupt/non-real/naive/invalid
  bars, overlap equality/conflicts, interval/window filtering, precise settings
  forwarding, changes to tape/cost/evaluator identity, immutable registration,
  frozen reads, cumulative counts, persistence failure and Make fetch refusal.
- `make lint-files ENVFILE=/dev/null FILES='src/autopoiesis/research/driver.py
  src/autopoiesis/research/prereg.py src/autopoiesis/research/trials.py
  src/autopoiesis/research/walk_forward.py tools/check_research_tape.py
  tools/fetch_replay_bars.py tests/autopoiesis/test_research_contract.py
  tests/autopoiesis/test_research_prereg.py tests/autopoiesis/test_research_driver.py
  tests/autopoiesis/test_research_fetch_make.py tests/autopoiesis/test_replay_bar_fetch.py'`
  -> **All checks passed**. Earlier lint findings were import sorting and unused
  fixture unpackings; fixes were rerun through the suite.
- `make type ENVFILE=/dev/null` -> **Success: no issues found in 48 source files**.
  Initial type run found five newly introduced heterogeneous-settings/source-path
  annotations; corrected annotations/null-source guard passed the final rerun.
- `make verify ENVFILE=/dev/null CLASS=config-example-names` -> **1 class passed**,
  documenting 40 recognized names; `CLASS=config-example-complete` -> **1 class
  passed**, all 44 variables the config loader reads documented.
- Scheduled direct-Make invocation follow-up: `make test ENVFILE=/dev/null
  TEST_ARGS='tests/autopoiesis/test_research_fetch_make.py'` -> **5 passed in 0.14s**.
- `git diff --check` -> **exit 0**.

Limits and remaining work: no service installation, credentials, live runtime state
or production trading behavior was changed. The example declares historical
integrity diagnostics; local registration timestamps cannot establish data
independence. The OOS metric is mean fold net-PnL/spent-notional percentage, not
portfolio performance. The cumulative square-root trade guard is a sample-size
heuristic, not formal Bonferroni/FWER control; independent holdout/family validation
and sustained clean paper observations remain necessary for an edge claim. Cache
provenance checks validate the local broker-fetch format, not cryptographic broker
authenticity. The public Python search remains explicitly labelled diagnostic;
CLI session locking is not a lock for arbitrary external low-level writers.
The real-cache gate and isolated real-ledger execution, independent reviews, full
suite, credential-free committed fresh clone and commit remain pending with root.
Task 2 source diff is frozen for review; no commit was made by the implementation agent.

Task 2 spec review correction: the synthetic CLI ledger contains 3 rows with
`evaluator_sha256: null`, despite the hash being present in evaluation identity.
The CLI omitted forwarding the prepared hash. Success is every persisted row
identifying the actual evaluator independently, verified by the existing real CLI
fixture with an explicit hash assertion. Also update tools/README.md's stale
fetch-failure description to the now-required registered contract and nonzero exit.
Root reproduced the missing-hash regression through Make: **1 failed, 43 deselected
in 0.37s**. After forwarding the prepared hash, the same focused check returned
**1 passed, 43 deselected in 0.28s**; focused lint passed. The service documentation
now states the external registered spec and fetch-failure behavior. Re-review pending.

Task 1 status reconciliation: earlier references to pending independent review and
commit are historical snapshots, now superseded. Root reported the focused repair
accepted after spec/quality review and committed as **29d1eea** before Task 2
implementation began. The remaining Task 1 integration/fresh-clone gates are still
Task 6 work; they have not been inferred from focused tests or the commit.

Task 2 real-cache preparation: `make research-register ENVFILE=/dev/null
SPEC=/tmp/autopoiesis-research-repair.C9ugL0/diagnostic.spec
PREREGISTRY=/tmp/autopoiesis-research-repair.C9ugL0/registry` registered the documented
historical diagnostic example. `make research-check-tape` with that contract and
the deployed replay directory failed nonzero: conflicting overlapping SPY price at
**2026-10-07T16:10:00Z** in `SPY_5Min_rolling.json`. This is a successful refusal,
not a successful dataset. Source cache is preserved. For the separate end-to-end
integrity diagnostic, select the latest completed fetch (`SPY_5Min_rolling.json`,
24,192 source rows, fetched October 12) before computing any result, copy it into an
isolated cache, and use the same frozen declared window. Do not select on returns
or treat the single-file diagnostic as remediation of the conflicting cache.

Task 2 quality review correction plan: a registered instrument containing shell
syntax reached recursive Make and executed the reviewer's harmless synthetic marker
twice. Also a matching cached `PASSES` result with NaN was reused with exit zero.
Success: reject invalid broker symbol grammar before exposing fetch arguments, and
reject non-finite/malformed matching evaluation results before reuse, without
resetting cumulative historical attempt counts. Add focused refusal regressions,
then run research tests/lint and independent quality re-review. No shell probe will
contact a broker or use operator credentials.

Root quality regressions: **4 failed, 44 deselected in 0.46s** before fixes;
**4 passed, 44 deselected in 0.37s** after symbol/result validation. Broader research
suite then **136 passed in 6.91s**; one added contradictory PASS case gives
**49 contract tests passed in 0.88s**, focused lint clean. Independent quality
re-review approved with **49 passed in 0.81s**. Independent spec review also
approved after ledger-hash/documentation corrections. Task 2 accepted.

Single-fetch real diagnostic completed with the pre-quality-fix evaluator hash
`1d568571d9c16ce7ec932893235e4e8d1ab5894c7cd3f1f72be1b6164db6b306`:
**24,111 selected bars**, 81 outside-window exclusions, fingerprint
`sha256:e3c2bf54eb495e8ccd82502485d7aaf7a239e2838c0d1ba8d83984e4acd75ea4`;
11 recorded candidates, **4 OVERFIT, 7 INSUFFICIENT, 0 PASS**, zero persistence
failures. This proves that version's real path ran, not alpha or scientific
preregistration. Further evaluator accounting repairs have their own written plan
`2026-10-12-backtest-accounting-repairs.md`; final real execution/repeat must be
rerun with the final evaluator identity in task 6. No prior trial is relabelled.

## Final accepted source evidence (2026-10-13)

Final integration review's account-target gap was fixed and independently reviewed
in **cd2874e**. Full check after that source: **1,499 passed / 6 skipped in 38.25s**,
lint clean and 49-file mypy clean. Actual committed-source fresh clone on explicit
Python 3.10.18: exit 0, **9 steps PASS, zero FAIL**, new empty venv/config;
**1,498 passed / 7 skipped**, 55/55 gate classes and 2,488 test executions. The
extra skipped host configuration check is expected with disposable empty XDG config.
After clone-log completion, root serial `make verify ENVFILE=/dev/null` exited 0:
**55/55 classes, 2,489 executions, zero failures across 94 files**. Original log
is retained verbatim; trailing output padding is not manually edited into evidence.

Actual remote CI for source cd2874e: run **38348912218**, gate Python
3.10/3.11/3.12 and release all completed SUCCESS. Published review branch is PR #10.
Final documentation/evidence commit changes no production source; inspect that
latest PR SHA's CI separately before delivery. All source-repair tasks accepted;
the RSI/alpha/profitability/deployment/offsite/full-state follow-ups remain open in
`docs/evidence/rsi-repairs-2026-10-13.md`, not erased or marked as achieved.

Actual first automatic hourly backup trigger was observed at 01:01:30 EDT,
completed successfully at 01:02:02, ExecMainStatus=0/Result=success. Trading daemon
remained active with PID 2800047 and old source. Final public refresh from the same
sealed real journal succeeded; collected tests 1,505 in 94 files, unchanged net
paper accounting and UNKNOWN portfolio comparison. No order or risk change.
