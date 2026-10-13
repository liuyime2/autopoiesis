# Next market-open validation plan

> **For agentic workers:** Use Superpowers subagent-driven-development for bounded implementation units, with specification review followed by quality review. Continue the authorized preparation without repeated permission prompts.

**Goal:** Prepare the next US equity session for auditable paper operation through real data, decisions, Guardian, execution attribution, feedback and recovery. Profitability and zero failures are not inferred from readiness.

**Architecture:** Reuse the existing Make runner, one daemon, external operator configuration, journal, doctor, broker client and local backup. Preserve the main worktree's existing prereg.py edit and all historical records. Deploy only a verified commit, maintain one runtime/state boundary and one active writer, retain rollback evidence, and measure actual open-session results separately from preflight.

**Tech Stack:** Existing Python package, Make and systemd user services; paper broker and local Ollama. No new dependency or live endpoint.

## Measured baseline at 2026-10-13 05:40–05:45 UTC

- Main commit 80d0593, published base 49063c8, one preexisting uncommitted prereg.py edit.
- Reviewed repair checkout 209222b5bffe28915fde8662eeb028da009dbb46; its exact remote CI has four successful jobs. Last source check 1,500 passed / 6 skipped; actual empty-environment clone nine steps passed; integration 55/55 classes.
- Active daemon PID 2800047, started October 12 18:26:26 EDT, loaded fingerprint 60d5aa8d767ce067. Heartbeat RUNNING, closed-market maintenance, cycle/error counts 0/0, broker=true, model=null. It has not loaded the reviewed repair.
- Local backup timer enabled/active; actual October 13 01:01:30 trigger completed 01:02:02 EDT with Result=success / exit 0. Research timer next 06:58:13 EDT still uses the old deployment.
- 21 TB free. Real alpha, decision benefit and controlled RSI have not been demonstrated. Old historical samples cannot be backfilled into new frozen provenance.
- Official NYSE core session is 09:30–16:00 New York time. The user confirmed
  **October 13 Tuesday, the nearest open**; actual broker next open is October 13
  09:30 EDT. Recheck the session using the broker calendar during readiness.

## Acceptance and verification

Pre-open READY means: exact verified source loaded; paper host/mode; unchanged or tighter hard limits; one live daemon/fresh heartbeat; real broker clock/account/positions/open orders readable; no unresolved submission; certified archive; local recovery checkpoint; real market data and real decision engine work; guarded closed-market behavior produces no submission; model failure has explicit safe fallback; research contract/tape and scheduled entry are correct or explicitly fail closed; pre/post-open collection is scheduled and cannot itself submit an order.

Open-session PASS requires new real journal records from the expected session, fresh real snapshots, matching decision/snapshot/strategy provenance, approved-only attributed submissions, no duplicate or unresolved intents, honest fill/feedback state, no unexpected errors and bounded risk. HOLD or no fill is not a failure, and does not prove profitable decision making. Outcomes beyond their horizon remain pending. No forced order to manufacture an execution PASS.

- [x] 1. Read-only operational audit. Run `make doctor` and bounded `make broker-latency-sample COUNT=5` against the existing deployment. Inspect only nonsecret configuration, runtime paths, library readiness, model residency and next broker session. Audit service templates and all relevant consumers for shared-runtime/source correctness. Record actionable failures; market-closed and missing forward outcomes remain distinct from failures.
- [x] 2. Write a bounded correction plan for each measured gap before code edits. Prefer existing commands; add a read-only readiness probe only if existing tools cannot collect the required real evidence safely. Use labelled synthetic refusal tests for any new nontrivial probe; run through Make and review specification then quality. Do not create a second trading loop.
- [x] 3. Prepare the concrete deployment/rollback checkpoint. Pre-stop journal snapshot is CERTIFIED; after the old writer exited, a separate 204-file / 85 MB app-state copy verified 204 SHA-256 hashes. Credentials were excluded. Original ten unit bytes and timer states were preserved. Repair-worktree runtime is archived and the retained runtime path is a symlink to the shared main runtime. Main prereg.py user diff SHA stayed unchanged.
- [ ] 4. Deploy the verified repair for paper validation. **Deployment and safe source switch are now complete**, but this item remains open until production stop latency is measured and written down. Actual rendered six service/four timer units passed systemd-analyze; mode/limits match; one new daemon has source fingerprint `9fb9066d820f27c9`, active heartbeat, zero restarts and matched startup reconciliation. No SIGKILL occurred. See `docs/evidence/market-open-readiness-2026-10-13.md`.
- [x] 5. Run the real minimum loop safely before market open. Actual model response/provenance was valid; market clock closed, scheduler false, preview HOLD 0; Guardian refused stale data, no approval/order. Open verifier returned expected WAIT/SESSION_NOT_OPEN with exit 2 and scheduled wrapper exit 0. READY describes paper process/pre-open readiness only; profitability remains UNPROVEN.
- [x] 6. Schedule next-session validation with the installed active timers: 08:45 preflight, 09:45 and 10:30 open verifier, America/New_York. Their future session observations remain pending and cannot be inferred from active timers or this early closed-market probe.
- [x] 7. Reconcile and publish this bounded change set: branch pushed, PR #10 open/unmerged, full summary and executable roadmap attached, source commit `da2da73` has all four exact CI jobs passing and credentials-free committed-tree clone passes. Actual open-session, holdout, paired LLM benefit, controlled RSI and profitability remain open.

## Stop and rollback criteria

Any live/nonpaper endpoint, wider risk setting, unapproved submission, provenance mismatch, ambiguous order retry, corrupted archive or conflicting writer is NO-GO. Freeze new trading work and preserve evidence; do not retry ambiguous submissions or erase journal/state. An infrastructure rollback restores previous unit bytes with the same preserved journal, never a stale replacement journal or a risk bypass. The old source has the attribution defect found in this audit: restoring its unit must leave trading stopped until an audited safe source is restored, rather than automatically resume that known unsafe loop. Model timeout uses the existing safe fallback and remains separately measurable.

## Evidence log

This is the preimplementation plan, not a completed readiness certificate. Commands, results, independent findings and acceptance reconciliation are appended as each unit actually finishes.

Read-only audit completed: actual old `make doctor` exit 0, 0 failures / 7 warnings;
its historical LLM-quality claims are not new-source evidence. New-source bounded
`make broker-latency-sample COUNT=5`: 10 successful reads / zero failures,
p50 26.862ms / p95 and max 90.739ms (2026-10-13 05:46:23 UTC).

Measured blockers: uncovered Hybrid decisions can submit without strategy identity;
--once duplicates a raw-LLM loop with incomplete operator risk/PID wiring; daemon
still has the old 30s stop timeout; no calendar/actual-engine read-only readiness
collector exists; research external supported spec is absent. These become bounded
units rather than reasons to certify READY. Actual library has 79 parsed specs,
zero ACTIVE, 22 PROBATION and one BASELINE, covering SPY/BIL/TLT only. No-cover HOLD
is a valid abstention; it cannot be turned into a forced trade for a green test.

Deployment audit: external configuration has no path overrides, so preserving the
private repair runtime then linking repair runtime to the real main runtime keeps
journal/locks/library/cache/log paths coherent. The installer rewrites ten units
and starts timers; retain exact previous unit bytes/states and stop watchdog timer
and any running watchdog oneshot around cooperative stop/start. Verify rendered
units and actual source, not installer exit alone. Do not replace the journal.

Actual `make smoke` exit 0 at October 13 02:04:52 EDT: broker clock closed,
next open **October 13 09:30 EDT**, next close 16:00 EDT, account ACTIVE,
six positions and zero open orders. This is real broker/snapshot evidence only;
the smoke command does not invoke the decision engine or Guardian.

Unit 1 is accepted in cc76d1a: independent spec review reran **116 passed /
1 skipped**, subsequent quality review reran **12 passed / 31 deselected**.
Missing strategy selection now abstains before the model and cannot reselect
through fallback. This proves synthetic attribution boundaries, not an order.

Deployment checkpoint: exact prior ten service/timer bytes and unit states retained
privately in `/localscratch/liuyime2/QuantGroup/runtime/autopoiesis/deployment-checkpoints/20261013/units`; the preexisting main
prereg.py diff is preserved. `make journal-backup` with the real JOURNAL_RUNTIME
completed exit 0 into that private checkpoint: five files, 2,326 cycles,
21,695 events, four generations, zero unparsed lines, recovery **CERTIFIED**.
Strategy/knowledge state and the actual switch remain pending.

Unit 2 accepted in e526c13: the separate raw-LLM --once assembly was deleted in
favor of the bounded audited daemon path; focused tests **142 passed**, independent
specification approved and quality review reran **59 passed**. No real trading
entry was launched while the old daemon remained active.

Unit 3 initial implementation: **73 focused tests passed** but independent spec
review refused approval for Make failure-exit masking, duplicate broker order IDs,
and omitted cumulative fill observations. Corrections are written before edits in
the unit plan and must be re-reviewed. Frozen full `make check ENVFILE=/dev/null`
also measured **1,563 passed / 6 skipped / 1 failed in 81.03s**: config wiring reads
host env despite the offline setting and omits actual Make readers. The next bounded
plan is `2026-10-13-config-wiring-isolation.md`. Do not certify readiness from this
failing full run or delete actual working research parameters to hide it.

Unit 3 is subsequently accepted in 1de7679: **104 focused tests passed**;
specification and subsequent quality review approved after actual-process config,
Make WAIT-vs-FAIL, identity/fill and transient-heartbeat corrections. Real collector
execution and timer installation remain pending. Unit 4 initially enabled a full
check with **1,612 passed / 6 skipped in 38.94s**, but quality then found shell
literal/comment false consumers. That correction is now independently approved
(84 focused tests); the final changed tree still needs its own full/fresh-clone
proof. These are not a deployed-source or market-open certificate.

The user added whole-account visibility and asset scope, explicitly including
options and crypto. The new bounded plan is `2026-10-13-account-asset-boundary.md`.
Its measured baseline is 68 focused passes, but the current equity pipeline has
no options/crypto adapter and truncation safeguards differ between trading and
readiness. Resolve account visibility and unsupported-instrument/order-list
boundaries before the source deployment; do not enable unsupported trading.

Units 5 and 6 are accepted in 47fccb1 and e28a2f1: whole-account order completeness,
finite numeric data and explicit equity asset boundaries passed **211** focused
tests plus **125** safety regressions; latest recorded signed/fractional account
context passed **7** direct and **145** related tests. Each received independent
specification then quality approval. Actual `make smoke` at **03:48:21 EDT** read
six us_equity long/integer holdings, complete zero open orders, ACTIVE account and
crypto status, and options approved/trading levels 3. Options/crypto permissions
do not supply missing adapters; XLB/XLE/XLF strategy coverage, fractional orders
and whole-account short cover remain pending in the asset-scope evidence document.

Final combined source review through e28a2f1 approved without blockers, but the
subsequent actual full check found **1 failed / 1,693 passed / 6 skipped / 1 warning
in 210.38s**. This supersedes the earlier passing full result for final acceptance.
The stale recorded D1 audit is fixed in b4e7253: independent specification and
quality each ran **25** governance tests and the retained **62/62** defect checks.
The writer-thread warning is a separate test acceptance gap, planned in
`2026-10-13-bounded-durability-verification.md`; it remains pending until its own
review and the new frozen whole-suite run. Deployment steps 4–6 are still pending.

Additional read-only actual library inventory through a temporary Make `--eval`
recipe using the existing `StrategyLibrary.list()` against the main runtime returned
**79 parsed / 0 rejected** (BASELINE 1, PROBATION 22, PAUSED 41, RETIRED 15).
Private result: `/tmp/autopoiesis-strategy-inventory-20261013.json`. No files were
moved or repaired. Nested historical quarantine files are outside the loader's
root-only glob; a broader filesystem count is not a current parse-failure count.

Unit 8 is accepted in 2fe3bb4: the corrected bounded journal test requires thirty
completed appends/scans and worker termination; a synthetic writer timeout now
fails rather than producing a passing warning. Related regressions **83 passed**,
independent specification **28 passed in 2.97s**, subsequent quality **28 passed
in 3.03s**. Production locks are unchanged. All eight bounded units have written
targets and two-stage reviews; final combined re-review and the new whole-suite,
fresh clone and CI remain required before deployment.

The frozen 56c1875 full check subsequently passed **1,707 / 6 skipped in 44.04s**
with thread warnings as errors. Final independent combined review then blocked
deployment on an additional wait boundary: ordinary default sleep may continue
for **675s** after SIGTERM, beyond the **630s** budget. Synthetic actual-handler
probes confirmed resumed sleep. This measured gap is planned before edits in
`2026-10-13-cooperative-daemon-wait.md`; another frozen full run is required.

The old process also uses ordinary sleep and an older signal handler that writes
STOPPING health. For the actual switch, pause watchdog and mutation timers, verify
the old process is parked after its closed-market heartbeat (Linux wait channel
may be `poll_schedule_timeout.constprop.0` for this Python build), and retain
existing drop-ins. A unique temporary transition stop drop-in will grant **705s**
(old declared 675s wait plus 30s buffer), solely to let that old wait return without
SIGKILL. Cooperative stop is still required; do not kill through a journal/order
boundary. Remove that temporary drop-in after confirmed old exit, reload and
verify the final derived new-source budget before starting the new process.
This does not increase a risk limit or establish an exact filesystem stop bound.

Unit 9 is accepted in f4e3232: default waits poll the stop flag in at most one-second
sleep requests while preserving the normal monotonic deadline and explicit callbacks.
The actual synthetic 30s wait now returns within 2s after SIGTERM; independent
specification and quality each passed **100** focused tests. The signal handler
remains flag-only, risk/order boundaries unchanged, final stop inventory **630s**.
Final combined re-review and frozen full check are running; fresh clone and exact
CI must follow, then the operational steps. Nine accepted source units do not
constitute actual deployed-source or open-session verification.

The final review of f4e3232 confirmed the default wait fix but found a separate
curriculum stage boundary: synthetic actual transport call flags **[False, True]**
prove a new retry after stop, and completed proposals/admissions could start later
stages. Unit 10's pre-edit plan is `2026-10-13-curriculum-stop-boundaries.md`.
Its implementation has **177** focused passes and specification approval; quality
and final acceptance remain pending here.

Actual committed c2fa05d fresh-clone execution completed **8 PASS / 1 FAIL**,
with empty credentials and Python 3.10. Its two full suites each passed **1,713 /
6 skipped**; the final gate was **54/55**, **2,816** executions across **94** files.
The portable-unit gate incorrectly assigned the dedicated readiness templates to
the legacy renderer and omitted timer.in discovery. The independent diagnosis and
pre-edit target are in `2026-10-13-readiness-template-portability.md` (unit 11).
Private failed raw log retained at
`/tmp/autopoiesis-open-fresh-clone-c2fa05d.failed.log`; a new final clone is required.
The actual old local backup at **05:02:07 EDT** completed success/exit 0.

Units 10 and 11 received independent SPEC and QUALITY approval (177 and 126 focused passes respectively). The frozen full check passed 1,747 / 6 skipped in 88.01s, thread warnings fatal. Final combined review found one remaining internal curriculum request boundary: parser retry observed [False, True], and two-phase model generation starts phase B inside one outer call. Unit 12 is planned in `2026-10-13-curriculum-request-stop.md`; the final full/fresh/CI certificate remains pending. Review also records the existing transient-installer Ollama substitution gap as a separate follow-up; the intended persistent installation is independently verified.

Unit 12 accepted: the final independent combined review and two focused SPEC/QUALITY reviews confirm the callback checks every underlying actual curriculum HTTP request, including internal phase/retry boundaries. The synthetic boundary suite is 10 cases; independent reviews each passed 241 related tests. Frozen final `make check ENVFILE=/dev/null TEST_ARGS='-W error::pytest.PytestUnhandledThreadExceptionWarning'` passed **1,757 / 6 skipped in 47.53s**, mypy covers 49 source files with no issues. Source fingerprint for this frozen, uncommitted unit12 tree is `9fb9066d820f27c9`. Fresh committed-tree clone, exact current PR CI, runtime deployment and session results remain pending.

## 2026-10-13 complete project status and follow-up execution plan

The concise current-state matrix, remaining work by dependency, package entry conditions, repeatable commands, success/no-go criteria, and open-source/web scope are consolidated in [IMPLEMENTATION_ROADMAP_2026-10-13.md](../IMPLEMENTATION_ROADMAP_2026-10-13.md). It is the executable follow-up index; the existing RSI plan retains the detailed experimental methodology and dated evidence retains the history.

Unit 10 accepted in `9aa026e`; unit 11 accepted in `0b2c319`; unit 12 accepted in `b9922be`. Each has a written target and independent specification/quality approval. Current final source `make check` is **1,757 passed / 6 skipped in 47.53s**, warnings fatal; `make type` reports 49 source files with no issues. This source SHA's committed-tree fresh clone, four exact GitHub PR CI jobs and actual paper deployment/session observation are still required. Unit 11 also records the pre-existing `install-transient` Ollama-render gap; intended persistent installer is used, no transient installer success is claimed.

The latest actual research window produced **0 PASS** (1 OVERFIT, 10 INSUFFICIENT of 11 new trials). This confirms an honest negative/insufficient result, not alpha. Current holdings remain six us_equity positions, 0 complete open orders at the earlier read time. Options/crypto permissions are visible but their project adapters do not exist. XLB/XLE/XLF are uncovered; fractions and account-external short cover remain incomplete. No claim of whole-account arbitrary-asset autonomous execution is supported.

## Actual paper deployment update — 2026-10-13 06:08 EDT

Full raw timings and boundaries are in [market-open-readiness-2026-10-13.md](../../evidence/market-open-readiness-2026-10-13.md). In brief: source/CI/clone gates passed; old daemon stopped cooperatively; local journal and complete whitelisted application state checkpoint verified; new service loaded the expected fingerprint and safely reconciled; current account smoke passed six positions / complete zero open orders; readiness timers are installed. Early preflight READY had a real model response but stale quote caused a safe Guardian refusal; profitability remains UNPROVEN. The scheduled 08:45 and 09:45/10:30 checks are future evidence, not achieved outcomes. Exact production stop time was not recorded and remains a small operational measurement follow-up.
