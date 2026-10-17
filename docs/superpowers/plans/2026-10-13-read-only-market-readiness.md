# Real paper market readiness collector plan

> Use Superpowers subagent-driven-development, specification review then quality review. Run scripts and tests through Make. Implement only after the shared paper entry unit is accepted.

**Problem / baseline:** At 209222b there are zero broker-calendar calls and no read-only command that combines the actual Hybrid engine, Guardian, loaded daemon source, trusted journal and session cycles. Smoke is broker-only. Daily report exits zero even with failing checks. The active daemon has an older source fingerprint and no current-session cycles. Therefore command exit alone cannot certify preparation.

**Goal / success:** A read-only collector returns 0 for measured preopen READY or session VERIFIED, 1 for a measured failure, and 2 for WAITING observations. No verdict establishes profitability. It must never submit/cancel orders, reconcile, run a cycle, maintain/admit strategies or write runtime state except its designated private report.

**Minimum architecture:** One `tools/market_readiness.py` using existing config, broker factory, shared `_build_agent_daemon`, health, journal, submission projection and learning-evidence checks. Add thin Make preopen/open targets and a source-fingerprint target. Local timer installation remains a separate Make operation; collector execution is observational. No dependencies or second trader.

Implementation inspection found the shared builder constructs its journal internally
before the probe can supply strictly observational reads. The measured seam is one
constructor assignment. Extend `_build_agent_daemon` with an optional existing
`journal` argument; ordinary daemon calls retain the exact existing constructor,
while the collector supplies its checked read-only journal. This avoids patching a
production module global at runtime and lets callbacks refuse newly corrupted rows.
Keep the production change to that signature/assignment, verify supplied journal
matches config path, and rerun the existing entry-parity tests. Hold an existing
shared lock only for coherent local history/projection/construction reads, never
through network/model work. No normal trading behavior changes.

## Acceptance

1. Require paper mode/endpoint, credentials, explicit expected loaded fingerprint, existing journal/lock/archive manifest, a live fresh heartbeat/PID, exact imported/expected/heartbeat source agreement. Read the actual running process environment privately (`/proc/<pid>/environ`) and resolve relative paths against its actual cwd, so service-provided maintenance/model settings cannot silently differ from Make defaults. Build with actual effective process configuration; only allowlisted nonsecret risk/config fields enter the report for comparison with the deployment checkpoint. Never print/copy credentials or change operator limits.
2. Read actual broker clock/calendar/account flags, positions and open orders. Derive session date/hours from broker calendar, with New York timezone; do not fabricate the next open or a market-open snapshot. Closed-market preview and open-session evidence are distinct.
3. Check trusted valid archive and full submission projection with zero unresolved identities. Immediately inspect dropped-line counts after each cycle/event read. Missing/untrusted/corrupt history fails, and a missing manifest must not trigger fresh-baseline writes. Do not silently truncate history/orders into apparent completeness.
4. Preopen: get real snapshots for all configured symbols. Validate symbol/timestamps/account data. Make one explicitly reported covered-symbol preview through the actual engine, requiring an actual successful model response and final matching frozen provenance to establish model availability. A valid response may legitimately become a policy override, so final decision_source alone cannot establish whether the model answered: use a temporary in-memory observation of the existing model call without modifying its result. Other configured symbols are data/coverage observations, not model-call claims. Refresh selector cycle count and eligible lessons in memory only. Review actual decision with actual Guardian/trade counter/holding values. A legitimate closed/stale refusal is recorded, never changed into an approval. Report additional held/attention-symbol coverage as pending; do not call `_round_symbols`, which can append failure events.
5. Re-read clock/heartbeat after slow preview; refuse mixed source/PID/phase observations. No raw rationale/override reason/SDK dump/exception messages: these can expose model or credential material. Serialize bounded allowlisted scalar summaries and fixed diagnostic codes. Atomic report mode 0600.
6. Open: require explicit session date/cutoff, new real cycles in that session, configured first-round coverage, fresh snapshots, matching named strategy provenance, Guardian-approved actionable submissions and consistent durable intent/acknowledgement identities. Expected uncovered HOLD0 with no provenance is a safe abstention, not attributed learning. Report errors, model/fallback counts, fills and pending feedback honestly. No new cycles/closed holiday is WAITING, not VERIFIED. Current heartbeat proves current process source; it cannot manufacture a producer fingerprint for historical cycles.
7. Synthetic refusal tests in an existing registered test file cover wrong paper/source, stale/dead heartbeat, unresolved/corrupt/untrusted history, invalid actionable provenance and missing coverage. Successful fixture gives forbidden mutation methods raising stubs and verifies no writes outside the report. Red before implementation, then green/focused lint/type, independent spec then quality review.
8. Thin Make timer installer renders explicitly frozen ROOT/fingerprint/session-cutoff paths into local systemd user units, verifies before enabling. Preopen 08:45 and open 09:45/10:30 weekday America/New_York. Reports only; no order execution or automatic readiness-document edits. Holidays produce WAITING. Whole process timeout exceeds the documented one-model-preview budget. Record actual next triggers after installation. Keep rendering and subprocess argument validation in one small `tools/install_market_readiness.py` utility, invoked through Make, rather than inline Python `exec` strings. Validate staged units before replacing installed definitions; failed verification leaves installed units unchanged. New scheduled collectors themselves invoke Make with the frozen interpreter/source/env path. The installer is an explicit configuration mutation, separate from observational collector execution.

## Boundaries and remaining proof

The user requested nearest-open preparation; nearest session is assumed while optional date clarification remains unanswered. Trading is paper only. Uncovered HOLD, closed-market refusal, model response, scheduler refusal, session execution, fill outcome, alpha and profitability each retain separate evidence. Research has no external registered production contract yet; until configured it must fail closed and remains a separate measured gap.

- [x] Labelled synthetic red baseline and implementation: measurements recorded below.
- [x] Focused Make tests/lint/type: measured commands recorded below.
- [x] Independent specification reapproval and quality re-review: both approved the operational-state correction; measured evidence is recorded below.
- [ ] Real deployed-source collector run and private report.
- [ ] Actual local timer install/next triggers; reconcile pending session proof.

The initial written plan asserted no implementation or real collector run. Implementation progress is recorded below; real deployment/session proof remains pending.

## Construction seam amendment (before edit)

Measured baseline: two additional synthetic builder tests fail because `_build_agent_daemon` rejects the `journal` keyword. The builder creates one `JsonlJournal` before its initial lot read; that read currently has a create-capable lock. Minimum change: accept an optional existing journal, require its resolved path to match the effective config, and otherwise retain the exact existing constructor. The observer supplies its strict read-only journal; ordinary daemon/once callers are unchanged. Verify with the two seam tests plus existing entry-parity/risk tests. The root authorized this narrow extension of the implementation boundary.

Synthetic red baseline: `make test TEST_ARGS='tests/autopoiesis/test_cli.py -k readiness' ENVFILE=/dev/null` produced 20 expected collector-missing failures and 29 deselections before code. After the first implementation and fixture schema correction, 30 readiness tests pass. Additional slow-preview phase/source and actionable-unexecuted assertions first produced 3 failures/27 passes, then passed after fixes. Seam baseline: `make test TEST_ARGS='tests/autopoiesis/test_cli.py -k readiness_builder --tb=short' ENVFILE=/dev/null` produced 2 expected unsupported-keyword failures/59 deselections.

## Installer amendment (before edit)

Measured implementation issue: the initial Make renderer contains an approximately 2,000-character inline Python recipe and replaces installed unit bytes before systemd validation. Minimum remedy: a small separate `tools/install_market_readiness.py` utility, called only by Make, stages four files and invokes argument-list subprocesses; verification failure preserves installed bytes. Services invoke the registered Make targets with frozen interpreter, root, external env path, fingerprint and deployment cutoff. Tests use labelled synthetic runner injection and never contact systemd. The collector remains observational. Root authorized this installer path and service amendment. With optional journal injection the extra callback-rebinding helper is redundant and can be deleted; the shared builder captures the supplied strict journal throughout.


## Implementer verification checkpoint

- `make test TEST_ARGS='tests/autopoiesis/test_cli.py --tb=short' ENVFILE=/dev/null`: **73 passed in 3.64s**. This includes the existing 29 CLI/entry/risk/Make tests and 44 readiness/installer tests. Every added broker/model/process fixture is labelled synthetic; all network factories/transports are stubbed. Success refuses forbidden daemon/loop/execution/journal/heartbeat mutation methods and compares all temporary files before/after the designated 0600 JSON report.
- `make lint-files FILES='tools/market_readiness.py tools/install_market_readiness.py tests/autopoiesis/test_cli.py src/autopoiesis/cli.py' ENVFILE=/dev/null`: **all checks passed**.
- `make type-files FILES='tools/market_readiness.py tools/install_market_readiness.py src/autopoiesis/cli.py' ENVFILE=/dev/null`: **no issues in 3 source files**.
- Additional measured red loops: bootstrap-only config/report bounds **2 failed/68 deselected** before fixes; installer staging and model-completion Guardian time **3 failed/70 deselected** before fixes. The Make delegation test first failed for the absent target, then passed with arguments sent through environment values rather than shell interpolation.
- `git diff --check`: no whitespace errors. No commit, service operation, real broker/model call, credential/config edit, runtime state mutation or full repository gate was performed by this implementation unit. Actual timer scheduling and real collected evidence remain assigned to the root after independent reviews.

The collector is 400 lines; the separate installer is 72. The only production-source change is the optional validated journal construction seam. The original builder owns every callback and risk limit. Specification and quality review must complete before deployment; no profitability claim follows from these synthetic passes.

## Independent specification correction target (before edits)

Independent review reran 73 passing tests but refused approval for three measured
gaps: both scheduled services accept Make exit 2, although Make maps a collector
FAIL (1) and WAIT (2) to that same exit; submitted clients are unique but broker
order IDs are not; fill totals use only immutable submission responses and pending
counts ignore terminal cumulative snapshots. These are three distinct failure
cases, not real-session evidence.

Minimum correction: retain Make-only execution and add an explicit scheduled WAIT
normalization argument/environment (WAIT JSON remains exit_code=2, scheduled command
returns zero only for WAIT); remove SuccessExitStatus=2 so actual FAIL still fails.
Require unique broker order IDs across selected submissions. Reuse existing
confirmed_fill_events projection for cumulative observations, validate their
client/order/symbol/side/strategy identity, count each order once, and report pending
broker fill observations separately from learning_feedback=UNOBSERVED. Cover real
schema partial, terminal filled, and canceled-zero observations using clearly
synthetic fixtures. Establish red results before fixes, run CLI/readiness tests and
focused lint/type, then repeat independent specification followed by quality review.
No daemon trading path, risk, actual services or runtime is changed by this unit.


## Specification correction verification checkpoint

- Before correction, `make test TEST_ARGS='tests/autopoiesis/test_cli.py -k "duplicate_broker or cumulative_fill or fill_identity or scheduled" --tb=short' ENVFILE=/dev/null` produced **14 expected failures / 73 deselected**. The tests reproduced duplicate broker-order acceptance, omitted partial/final/canceled cumulative fills, unchecked client/order/symbol/side/strategy fill identities, missing explicit scheduled normalization, and Make's WAIT/FAIL ambiguity.
- Before quantity validation, `make test TEST_ARGS='tests/autopoiesis/test_cli.py -k fill_quantity_must --tb=short' ENVFILE=/dev/null` produced **4 expected failures / 87 deselected** for negative, NaN, infinity, and greater-than-requested quantities. These are labelled synthetic refusal cases.
- After correction, `make test TEST_ARGS='tests/autopoiesis/test_cli.py --tb=short' ENVFILE=/dev/null` produced **91 passed in 5.35s**. This includes actual GNU Make recipes with a synthetic collector: scheduled WAIT returns Make 0 while JSON remains WAIT/2; scheduled FAIL returns Make 2 while JSON remains FAIL/1. Both service templates have no SuccessExitStatus=2 and pass SCHEDULED=1. No actual service or broker was invoked.
- Cumulative fills reuse `fill_reconciler.confirmed_fill_events` over observations no later than collection time. Each matching selected order contributes one latest cumulative quantity, validated against real schema identity/source/paper/requested quantity and finite nonnegative numeric bounds. Partial orders remain pending; terminal zero canceled observations close the broker-observation count. `pending_fill_observations` and `learning_feedback=UNOBSERVED` deliberately answer separate questions.
- Focused lint/type and whitespace checks are rerun below; independent specification and quality approval remain pending. The root's unrelated existing config-wiring gate failure is outside this unit and is recorded in its own next-unit plan. No config-wiring test, actual runtime, service, credentials, deployment, commit, or full gate was modified/run by this correction unit.

Final correction freeze: `make test TEST_ARGS='tests/autopoiesis/test_cli.py --tb=short' ENVFILE=/dev/null` reports **93 passed in 5.57s**, including two extra regressions for future fill exclusion and repeated legacy cumulative rows. Focused `make lint-files` on the collector/installer/test_cli/CLI reports **all checks passed**; `make type-files` on collector/installer/CLI reports **no issues in 3 source files**. `git diff --check` is clean. The collector is now 442 lines; the installer remains 72. Independent specification review must be repeated before quality review. The previous 400-line statement records the initial freeze, not the corrected size. Actual source deployment, runtime evidence and timer operations are still pending root work.

## Quality correction target (before edits)

Specification approved the 93-test correction. Independent quality inspection
then measured a remaining false-success path: collect explicitly accepts STARTING
and BACKING_OFF, and HealthMonitor.is_live checks liveness rather than operational
RUNNING state. AgentDaemon's daily-cycle-limit branch emits BACKING_OFF without a
CYCLE_FAILED event, so complete earlier coverage can incorrectly certify VERIFIED
while the current daemon is unable to trade. The report omits this state.

Minimum change: initial and final heartbeat observation require RUNNING for READY
or VERIFIED; fresh live STARTING/BACKING_OFF produce a fixed-code WAIT. Report only
bounded status/error_count and broker/model booleans or null, never raw messages.
Historical cumulative errors alone must not permanently reject a recovered RUNNING
daemon; unknown model health remains distinct from the actual preview response.
First reproduce preopen/open and late-state-change false successes in labelled
synthetic tests, then correct and rerun focused Make tests/lint/type. Repeat quality
review and confirm specification remains satisfied. No production heartbeat or
daily limit behavior, risk limits, operational state or schedules are modified.


## Quality correction verification checkpoint

Before editing, `make test TEST_ARGS='tests/autopoiesis/test_cli.py -k "nonrunning or recovered_running" --tb=short' ENVFILE=/dev/null` produced **10 expected failures / 93 deselected**. Eight cases showed false READY/VERIFIED for initial/late STARTING/BACKING_OFF in both phases; two recovered RUNNING cases exposed the missing bounded status/cumulative-error/health metadata. The correction requires RUNNING initially and finally, after safe effective configuration/report paths are established, and updates final bounded health metadata before a state WAIT. `_health` probes the PID from its one read payload instead of borrowing liveness from another heartbeat read. No production heartbeat, backoff, daily cap or risk behavior changed.

The full focused CLI run after correction produced **103 passed in 5.07s**. A further labelled synthetic rollover regression proves that a dead first PID cannot borrow liveness from the next read's different live PID; the final run is **104 passed in 5.61s**. Initial STOPPING/STOPPED/ERROR and paper/source/dead/stale refusals retain FAIL. Only the fresh live STARTING/BACKING_OFF states get DAEMON_NOT_RUNNING WAIT; historical error_count alone does not fail recovered RUNNING. Scheduled WAIT still preserves private report/JSON exit_code 2 and process success, while actual FAIL still fails through Make.

Focused `make lint-files` and `make type-files` are rerun on the collector/installer/test_cli/CLI boundary, plus `git diff --check`, before refreezing. Specification must reapprove this correction before quality re-review. No full gate, actual service/broker/model/runtime/credentials, commit, unit 4 path or operational-setting mutation occurred in this unit.


Quality-correction freeze: the final `make test TEST_ARGS='tests/autopoiesis/test_cli.py --tb=short' ENVFILE=/dev/null` output is **104 passed in 5.61s**. Focused `make lint-files` reports **all checks passed** and `make type-files` reports **no issues in 3 source files**. `git diff --check` is clean. Collector size is 453 lines; installer remains 72. Documentation matches the bounded state-report and WAIT behavior. Both independent re-reviews remain pending root work.

Accepted review checkpoint: independent specification re-review reran **104 passed
in 5.26s** and approved; subsequent independent quality re-review reran **11 passed /
93 deselected** and approved. Earlier review refusals are retained above as the
reason for the measured corrections. This resolves the review item only; real
deployment, model/Guardian observation, timer installation and open-session results
remain pending. The separate host-dependent config gate failure must be fixed before
the full repository check can pass.
