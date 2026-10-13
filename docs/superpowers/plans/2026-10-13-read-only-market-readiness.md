# Real paper market readiness collector plan

> Use Superpowers subagent-driven-development, specification review then quality review. Run scripts and tests through Make. Implement only after the shared paper entry unit is accepted.

**Problem / baseline:** At 209222b there are zero broker-calendar calls and no read-only command that combines the actual Hybrid engine, Guardian, loaded daemon source, trusted journal and session cycles. Smoke is broker-only. Daily report exits zero even with failing checks. The active daemon has an older source fingerprint and no current-session cycles. Therefore command exit alone cannot certify preparation.

**Goal / success:** A read-only collector returns 0 for measured preopen READY or session VERIFIED, 1 for a measured failure, and 2 for WAITING observations. No verdict establishes profitability. It must never submit/cancel orders, reconcile, run a cycle, maintain/admit strategies or write runtime state except its designated private report.

**Minimum architecture:** One `tools/market_readiness.py` using existing config, broker factory, shared `_build_agent_daemon`, health, journal, submission projection and learning-evidence checks. Add thin Make preopen/open targets and a source-fingerprint target. Local timer installation remains a separate Make operation; collector execution is observational. No dependencies or second trader.

## Acceptance

1. Require paper mode/endpoint, credentials, explicit expected loaded fingerprint, existing journal/lock/archive manifest, a live fresh heartbeat/PID, exact imported/expected/heartbeat source agreement. Capture only allowlisted nonsecret risk/config fields for comparison against the deployment checkpoint; retain existing operator limits.
2. Read actual broker clock/calendar/account flags, positions and open orders. Derive session date/hours from broker calendar, with New York timezone; do not fabricate the next open or a market-open snapshot. Closed-market preview and open-session evidence are distinct.
3. Check trusted valid archive and full submission projection with zero unresolved identities. Immediately inspect dropped-line counts after each cycle/event read. Missing/untrusted/corrupt history fails, and a missing manifest must not trigger fresh-baseline writes. Do not silently truncate history/orders into apparent completeness.
4. Preopen: get real snapshots for all configured symbols. Validate symbol/timestamps/account data. Make one explicitly reported covered-symbol preview through the actual engine, requiring a valid `llm` response with matching frozen provenance to establish model availability. Other configured symbols are data/coverage observations, not model-call claims. Refresh selector cycle count and eligible lessons in memory only. Review actual decision with actual Guardian/trade counter/holding values. A legitimate closed/stale refusal is recorded, never changed into an approval. Report additional held/attention-symbol coverage as pending; do not call `_round_symbols`, which can append failure events.
5. Re-read clock/heartbeat after slow preview; refuse mixed source/PID/phase observations. No raw rationale/override reason/SDK dump/exception messages: these can expose model or credential material. Serialize bounded allowlisted scalar summaries and fixed diagnostic codes. Atomic report mode 0600.
6. Open: require explicit session date/cutoff, new real cycles in that session, configured first-round coverage, fresh snapshots, matching named strategy provenance, Guardian-approved actionable submissions and consistent durable intent/acknowledgement identities. Expected uncovered HOLD0 with no provenance is a safe abstention, not attributed learning. Report errors, model/fallback counts, fills and pending feedback honestly. No new cycles/closed holiday is WAITING, not VERIFIED. Current heartbeat proves current process source; it cannot manufacture a producer fingerprint for historical cycles.
7. Synthetic refusal tests in an existing registered test file cover wrong paper/source, stale/dead heartbeat, unresolved/corrupt/untrusted history, invalid actionable provenance and missing coverage. Successful fixture gives forbidden mutation methods raising stubs and verifies no writes outside the report. Red before implementation, then green/focused lint/type, independent spec then quality review.
8. Thin Make timer installer renders explicitly frozen ROOT/fingerprint/session-cutoff paths into local systemd user units, verifies before enabling. Preopen 08:45 and open 09:45/10:30 weekday America/New_York. Reports only; no order execution or automatic readiness-document edits. Holidays produce WAITING. Whole process timeout exceeds the documented one-model-preview budget. Record actual next triggers after installation.

## Boundaries and remaining proof

The user requested nearest-open preparation; nearest session is assumed while optional date clarification remains unanswered. Trading is paper only. Uncovered HOLD, closed-market refusal, model response, scheduler refusal, session execution, fill outcome, alpha and profitability each retain separate evidence. Research has no external registered production contract yet; until configured it must fail closed and remains a separate measured gap.

- [ ] Labelled synthetic red baseline and implementation.
- [ ] Focused Make tests/lint/type and independent specification/quality review.
- [ ] Real deployed-source collector run and private report.
- [ ] Actual local timer install/next triggers; reconcile pending session proof.

No implementation or real collector run is asserted by this written plan.
