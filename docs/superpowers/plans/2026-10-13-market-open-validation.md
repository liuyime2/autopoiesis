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
- Official NYSE core session is 09:30–16:00 New York time. Determine the next actual session from the broker clock/calendar; optional user date clarification is pending. Prepare the nearest session first.

## Acceptance and verification

Pre-open READY means: exact verified source loaded; paper host/mode; unchanged or tighter hard limits; one live daemon/fresh heartbeat; real broker clock/account/positions/open orders readable; no unresolved submission; certified archive; local recovery checkpoint; real market data and real decision engine work; guarded closed-market behavior produces no submission; model failure has explicit safe fallback; research contract/tape and scheduled entry are correct or explicitly fail closed; pre/post-open collection is scheduled and cannot itself submit an order.

Open-session PASS requires new real journal records from the expected session, fresh real snapshots, matching decision/snapshot/strategy provenance, approved-only attributed submissions, no duplicate or unresolved intents, honest fill/feedback state, no unexpected errors and bounded risk. HOLD or no fill is not a failure, and does not prove profitable decision making. Outcomes beyond their horizon remain pending. No forced order to manufacture an execution PASS.

- [x] 1. Read-only operational audit. Run `make doctor` and bounded `make broker-latency-sample COUNT=5` against the existing deployment. Inspect only nonsecret configuration, runtime paths, library readiness, model residency and next broker session. Audit service templates and all relevant consumers for shared-runtime/source correctness. Record actionable failures; market-closed and missing forward outcomes remain distinct from failures.
- [ ] 2. Write a bounded correction plan for each measured gap before code edits. Prefer existing commands; add a read-only readiness probe only if existing tools cannot collect the required real evidence safely. Use labelled synthetic refusal tests for any new nontrivial probe; run through Make and review specification then quality. Do not create a second trading loop.
- [ ] 3. Prepare a concrete deployment and rollback checkpoint. Use `make journal-backup`, preserve local strategy/knowledge/configuration state without copying credentials into the repository, retain the existing user edit and old service definitions. Verify the new source against actual operator settings before stopping. Declare a single shared runtime location; avoid switching to an empty worktree runtime.
- [ ] 4. Deploy the verified repair for paper validation. Validate rendered units with systemd-analyze; use the existing Make entry for a cooperative stop/restart. Do not kill through an in-flight order, bypass PID protection or weaken Guardian. Confirm actual process-loaded fingerprint, unchanged paper/risk settings, startup reconciliation, active heartbeat and required timers. Record rollback conditions and measured stop/start timing.
- [ ] 5. Run the real minimum loop safely. With actual clock closed, verify real data/decision/Guardian refusal and no new broker submissions; never label a synthetic test as this execution. Confirm actual installed/runtime source, valid decision metadata and local journal append/recovery. While the daemon is active, collect read-only decisions rather than run another trader. Resolve any observed fault before marking preflight READY.
- [ ] 6. Schedule actual next-session validation. Before open, after first complete round and later in the session, run read-only readiness/day reports using the deployed source and real runtime. Record session-specific cycles, source, attribution, Guardian, orders/fills, fallback/error counts and pending outcomes; scheduling is not an execution PASS. Use local systemd/available native automation, no unsolicited external messages.
- [ ] 7. Reconcile evidence, publish bounded changes and inspect exact CI. Document READY/NO-GO and all partial/unobserved portions. Keep open-session, holdout, paired LLM, controlled RSI and profitability goals open until their actual observations exist. Preserve the retained checkout required by installed units.

## Stop and rollback criteria

Any live/nonpaper endpoint, wider risk setting, unapproved submission, provenance mismatch, ambiguous order retry, corrupted archive or conflicting writer is NO-GO. Freeze new trading work and preserve evidence; do not retry ambiguous submissions or erase journal/state. An infrastructure rollback restores the reviewed previous source/unit with the same preserved journal, never a stale replacement journal or a risk bypass. Model timeout uses the existing safe fallback and remains separately measurable.

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
