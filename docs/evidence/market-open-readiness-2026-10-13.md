# Paper deployment and readiness evidence — 2026-10-13

All times below are America/Detroit unless a timestamp includes `Z`. This is a paper-only deployment. It records the new process and pre-open diagnostic at their actual observed times; it does not certify an open-session trade, alpha, returns, or RSI improvement.

## Source checks before deployment

- Frozen source `0e91598074e52179981392782235528bb79f7e16`; its only commit after the checked PR source `da2da73bf9b3812aa2cd1e1b87cd67faff783c7f` records CI/fresh-clone evidence and updates this roadmap. Package/test source is identical.
- `make check ENVFILE=/dev/null TEST_ARGS='-W error::pytest.PytestUnhandledThreadExceptionWarning'`: **1,757 passed / 6 skipped**. mypy: 49 source files, zero issues.
- `make fresh-clone ENVFILE=/dev/null PYTHON=/localscratch/liuyime2/conda_env/llm/bin/python`: committed-tree clone of `dbd55c2`, Python 3.10.18, fresh venv, empty isolated XDG and no broker credentials: all **9/9 steps passed**. Both full check and pytest: 1,757 passed / 6 skipped; integration gate: **55/55 classes, 2,900 executions, 94 test files**. All **14** unit/service/timer templates passed renderer ownership/substitution checks. The actual harness log is [`fresh-clone.log`](fresh-clone.log).
- Exact GitHub run **38378167116** for source commit `da2da73bf9b3812aa2cd1e1b87cd67faff783c7f`: release and Python 3.10/3.11/3.12 gate jobs succeeded. The PR is open and unmerged.

## State preservation and process switch

- Prior daemon PID **2800047** was on source fingerprint `60d5aa8d767ce067`, paper mode and market-closed maintenance. The four old operational timers were paused during the switch. A unique temporary 705-second stop override allowed its existing sleep to return; `make stop` exited 0 and its heartbeat recorded `STOPPED`. The override was then removed, with no SIGKILL. The new unit derives `TimeoutStopSec=630`.
- Immediately after stop, `make journal-backup ENVFILE=/dev/null JOURNAL_RUNTIME=/localscratch/liuyime2/QuantGroup/runtime/autopoiesis DEST=/localscratch/liuyime2/QuantGroup/runtime/autopoiesis/deployment-checkpoints/20261013/post-stop-journal-0549` produced a certified five-file snapshot: 2,326 cycles, 21,759 events, four generations, zero unparsed lines.
- After stop, a separate private checkpoint copied the operator's strategies, knowledge, observations, preregistration, replay/cache, report, curriculum/reflection, risk baseline and research/lineage state. It contains 204 files / 85 MB; all 204 hashes verified. Credentials were not copied. The prior ten unit-file bytes and original state checkpoint remain preserved.
- The repair worktree's prior 120 MB runtime is preserved at `/localscratch/liuyime2/QuantGroup/runtime/deployment-diagnostics/rsi-repair-runtime-20261013T0952Z`. The retained worktree `runtime` now points to the single shared `/localscratch/liuyime2/QuantGroup/runtime`; the main worktree's existing `prereg.py` diff SHA remains `982b4db42a17126e4d21a03554c69a738844b44a5a4d67d01dc3e7c1fa6d4f8f`.
- `make install-service` rendered ten files under the manager's actual unit directory, `/home/liuyime2/.config/systemd/user`; `systemd-analyze --user verify` passed for all six service and four timer files.
- New paper daemon started **05:53 EDT**, PID **3184440**, with source fingerprint `9fb9066d820f27c9`, matching the on-disk source. systemd reports active/running, zero restarts and no exit error. Broker startup reconciliation appended a matched result: no extra broker orders and no unresolved client order IDs.
- Actual process config remains paper, `manage_account=true`, `shadow=false`, `shorts=shadow`, symbols BIL/SPY/TLT/XLB/XLE/XLF, position cap 25,000, total exposure cap 90,000, 100 attempts/day and 1,200 daily cycles. No live endpoint or wider hard risk value was installed.

## Actual account and closed-market observation

- `make smoke ENVFILE=<external operator env>` exited **0** at approximately 06:08 EDT: account ACTIVE, crypto status ACTIVE, options trading/approved level 3, six whole-account positions (BIL, SPY, TLT, XLB, XLE, XLF), complete open-order snapshot with **zero** orders. The report confirms us_equity adapter availability only; options, crypto and unknown asset classes remain unsupported by this application. Raw account output is private and omitted.
- `make doctor` exited **0**, with zero failures and eight warnings. The warnings include historical attribution/strategy performance/champion quality and are not new-cycle profitability evidence.
- Read-only `make market-preflight ... DATE=2026-10-13` completed at **06:02 EDT**, `READY`, zero diagnostics. It observed a real model response and valid frozen SPY strategy provenance. The proposed preview was HOLD, quantity 0; actual broker market clock was closed and the scheduler returned `trade_now=false`. The latest regular-session quote was stale, so Guardian refused with `STALE_DATA`; `guardian_approved=false` and `closed_market_approval_certified=false`. No order was submitted or cancelled. The report explicitly says profitability `UNPROVEN` and additional held-attention coverage `UNOBSERVED`. This is a safe closed-market readiness result, not market-open quote evidence.
- Read-only scheduled-mode `make market-open-verify ... DATE=2026-10-13` completed at **06:08 EDT** as `WAIT`, diagnostic `SESSION_NOT_OPEN`, report exit code 2. With `SCHEDULED=1`, the Make wrapper returned 0 as designed. No open-session record or trading result is claimed.

Private report files are under `runtime/autopoiesis/readiness/`; no account balances, credentials, raw rationale or private journal are published here.

## Actual enabled timers and remaining session check

- The four existing watchdog/backup/research/report timers are enabled and active. A real new-source local backup service completed successfully at 06:01 EDT. The watchdog service also ran successfully, observed a live daemon, and the timer now shows a next occurrence.
- Read-only readiness timers were installed through `make install-readiness-timers` in the actual unit manager directory. `market-preflight.timer` is active for **08:45 EDT**; `market-open-verify.timer` is active for **09:45 and 10:30 EDT**. Each runs only the read-only collector.
- The actual 08:45 preflight and 09:45/10:30 open observations remain pending at the time this evidence is written. Open-session checks require new session cycles, fresh quotes, source/provenance match, Guardian and order/fill reconciliation. A HOLD/no fill can be safe and cannot be converted to a profit claim.

Paper-only and Guardian/market-time checks remain mandatory. A successful preflight does not demonstrate alpha or RSI; the latest real-data diagnostic search still has zero PASS trials.

## Corrected open-verifier deployment cutoff — 08:12–08:18 EDT

The installed `market-open-verify.service` carried `DEPLOYMENT_AFTER=2026-10-13T09:53:18-04:00`, while the documented and measured deployment instant is **05:53:18 EDT** (`ExecMainStartTimestamp`, PID 3184440). Converting both to UTC gives `09:53:18+00:00` versus `13:53:18+00:00`: the configured cutoff was the deployment start *in UTC* relabelled as Eastern, a **4h00m** drift.

Root cause: the deployment timestamp's UTC clock reading was substituted without converting the offset, so the minutes and seconds survived and the hour shifted.

Measured consequence in `tools/market_readiness.py`, not an estimate:

- `_require(after <= now, "DEPLOYMENT_CUTOFF_IN_FUTURE", 2)` fails for any 09:45 trigger, because `09:45:00 <= now <= 09:45:30 < 09:53:18`. The 09:45 run was therefore guaranteed to report FAIL/exit 2 and collect no session evidence at all.
- `_session_evidence` starts at `max(opened, after)`, so the 10:30 run would have measured `09:53:18–10:30` and excluded the first 23 minutes of the actual session. Excluding real opening cycles from the observation window is a self-inflicted evidence gap, not a stricter gate.

Correction, installed through `make install-readiness-timers` with `DEPLOYMENT_AFTER=2026-10-13T05:53:18-04:00`:

- The change was rendered first into a temporary unit directory with `READINESS_UNIT_DIR`; `systemd-analyze --user verify` passed and the rendered bytes differed from the installed bytes in exactly one token.
- The first install attempt wrote to `/localscratch/liuyime2/ohome/.config/systemd/user` and reported exit 0, because `make install-readiness-timers` without an explicit `READINESS_UNIT_DIR` defaults to `$(XDG_CONFIG_HOME)/systemd/user` and this shell exports `XDG_CONFIG_HOME=/localscratch/liuyime2/ohome/.config`, while `systemctl --user show-environment` reports no `XDG_CONFIG_HOME` at all, so the user manager loads from `/home/liuyime2/.config/systemd/user`. The exit code alone would have concealed this. The four stray files, all written at 08:15:18, were removed and that directory was left empty; the correct directory received the install.
- Post-install verification: all four units byte-identical to the verified dry run; only `market-open-verify.service` differs from the preserved pre-change bytes; `systemctl --user show market-open-verify.service -p ExecStart` now reports `DEPLOYMENT_AFTER=2026-10-13T05:53:18-04:00`; both timers still active for 08:45 and 09:45/10:30.
- Pre-change unit bytes are preserved at `/tmp/opencode/unit-backup-20261013/` with their SHA-256 sums, so the correction is reversible by restoring four files.
- The trading daemon was not touched: MainPID 3184440, `NRestarts=0`, `ExecMainStartTimestamp` still 05:53:18 EDT, unit `active`, heartbeat RUNNING with fingerprint `9fb9066d820f27c9`, `cycle_count=0` and `error_count=0` while the market is closed. No risk limit, Guardian rule, execution path or broker order state was modified.

This correction widens the evidence window and makes the gate stricter, not more forgiving: with the cutoff restored, the 10:30 run's first-round deadline becomes 09:30+60min = **10:30**, so all six configured symbols (SPY, BIL, TLT, XLB, XLE, XLF) must each appear in a real cycle record before 10:30 or the run reports `FIRST_ROUND_COVERAGE_MISSING`. That is a real failure mode and is left standing rather than softened.

Follow-up, not fixed here: `Makefile`'s `READINESS_UNIT_DIR` default can silently resolve to a directory the systemd user manager does not read. It should derive the manager's real unit path (or fail when the two disagree) before this installer is used unattended.

The 08:45 preflight, 09:45 and 10:30 open observations remain pending. Installing an active timer and correcting its arguments is not a session result.

## Actual scheduled 08:45 preflight — 08:45:09 to 08:47:59 EDT

The installed timer fired on its own; this is scheduled evidence, not a manual invocation. `Result=success`, `ExecMainStatus=0`, report `status=READY`, `exit_code=0`, `diagnostics=[]`.

- Broker clock `2026-10-13T12:45:37Z` (08:45:37 EDT), `market_open=false`, session date 2026-10-13, open `13:30:00Z` = 09:30 EDT, close `20:00:00Z` = 16:00 EDT. Account ACTIVE, **6** positions, complete open-order snapshot with **0** orders, `unresolved_submissions=0` across 2,326 journal cycles and 21,853 events, `trusted_archive=true`.
- Daemon identity confirmed in-process: PID **3184440**, status RUNNING, fingerprint `9fb9066d820f27c9`, `cycle_count=0`, `error_count=0`, `broker_healthy=true`. Read-only configuration is unchanged from deployment: paper, `manage_account=true`, `shadow=false`, `shorts=shadow`, symbols BIL/SPY/TLT/XLB/XLE/XLF, position cap 25,000, total exposure cap 90,000, daily loss cap 500, 100 attempts/day, 1,200 daily cycles, `min_confidence=0.5`, `min_price=5`, `stale_after_seconds=900`, model `qwen3.8:27b`, interval 300s, maintenance 900s.
- **Strategy coverage measured per configured symbol:** SPY, BIL and TLT are `selectable=true`; **XLB, XLE and XLF are `selectable=false` with `HOLD_ZERO_WITHOUT_ATTRIBUTION`.** Half the configured universe therefore has no frozen strategy and can only abstain. These are abstention cycles, not failures, and they must not be counted as decisions.
- Preview: a real model response was observed with valid provenance against frozen strategy `trend-follow-20260824-001` for SPY; action HOLD, quantity 0, `decision_source=llm`, `scheduler_trade_now=false`. Every quote was genuinely stale — SPY's newest regular-session print is `2026-10-12T16:19:47-04:00` — so Guardian refused with `STALE_DATA`, `guardian_approved=false`, `closed_market_approval_certified=false`. Nothing was submitted.
- The report states `profitability=UNPROVEN` and `additional_held_attention_coverage=UNOBSERVED`.

The collector's own peak memory was 3.3 GB. It builds a daemon instance to preview decisions; it is a separate process from the trading daemon and does not trade.

This is preopen readiness only. It proves the scheduled path executes and the identity, account, calendar and refusal boundaries hold before 09:30; it contains no open-session cycle and no return.

## Actual scheduled 09:45 open verification — 09:45:03 to 09:45:44 EDT

The corrected cutoff let this run execute: it cleared `SESSION_NOT_OPEN` and then reached the session evidence, which is the first thing the broken value would have prevented. It reported **`FAIL`, exit 1, diagnostic `SESSION_QUOTE_NOT_FRESH`**, `Result=exit-code`, `ExecMainStatus=2`. The broker clock was `2026-10-13T13:45:29Z`, `market_open=true`, session 13:30Z–20:00Z, `scheduler_trade_now=true`.

The daemon did real work before that verdict: `cycle_count=20`, `error_count=0`, `model_healthy=true`, `broker_healthy=true`, journal 2,346 cycles / 21,865 events, six positions, **0** open orders, **0** unresolved submissions.

### Session cycles from the journal, 09:30–09:45 EDT

21 real cycle records, every one `source=alpaca` and `market_open=true`. `quote_age` is the snapshot timestamp minus the quote timestamp; Guardian's limit is 900 s.

| time | sym | quote_age_s | stale | action | qty | strategy | execution |
|---|---|---|---|---|---|---|---|
| 09:33:40 | SPY | 1 | no | HOLD | 0 | trend-follow-20260824-001 | SKIPPED |
| 09:34:40 | BIL | 1546 | **yes** | SELL | 1 | fixed-size-bil-sell-001 | **REJECTED** |
| 09:35:59 | TLT | 2 | no | SELL | 1 | fixed-size-tlt-sell-001 | **SUBMITTED** |
| 09:37:26 | XLB | 12 | no | HOLD | 0 | — | SKIPPED |
| 09:37:41 | XLE | 2 | no | HOLD | 0 | — | SKIPPED |
| 09:37:56 | XLF | 11 | no | HOLD | 0 | — | SKIPPED |
| 09:38:11 | BMBL | 30 | no | HOLD | 0 | — | SKIPPED |
| 09:38:26 | SOXS | 1 | no | HOLD | 0 | — | SKIPPED |
| 09:38:41 | PFAI | 119 | no | HOLD | 0 | — | **REJECTED** |
| 09:38:56 | KORU | 10 | no | HOLD | 0 | — | SKIPPED |
| 09:40:10 | SPY | 2 | no | HOLD | 0 | trend-follow-20260824-001 | SKIPPED |
| 09:41:04 | BIL | 193 | no | HOLD | 0 | — | SKIPPED |
| 09:41:19 | TLT | 4 | no | SELL | 1 | fixed-size-tlt-sell-001 | SKIPPED |
| 09:42:44 | XLB | 8 | no | HOLD | 0 | — | SKIPPED |
| 09:43:00 | XLE | 12 | no | HOLD | 0 | — | SKIPPED |
| 09:43:16 | XLF | 2 | no | HOLD | 0 | — | SKIPPED |
| 09:43:31 | BMBL | 23 | no | HOLD | 0 | — | SKIPPED |
| 09:43:45 | SOXS | 4 | no | HOLD | 0 | — | SKIPPED |
| 09:44:00 | SOXL | 3 | no | HOLD | 0 | — | SKIPPED |
| 09:44:16 | KORU | 3 | no | HOLD | 0 | — | SKIPPED |
| 09:45:29 | SPY | 11 | no | HOLD | 0 | trend-follow-20260824-001 | SKIPPED |

XLB, XLE and XLF abstain with no strategy, exactly as the 08:45 coverage report predicted. BMBL, SOXS, PFAI, KORU and SOXL are the four `attention_slots` most-active names added by the round builder under `universe=tradable`; none is in the account, and only PFAI was refused.

### The one executed order, reconciled against the broker

TLT SELL 1 was Guardian-approved on a 2-second-old quote and submitted with durable write-ahead: `ORDER_SUBMISSION_INTENT` at 09:37:25 (`autopoiesis-69ff75e212473da32b2b8c2b`), `ORDER_SUBMISSION_ACKNOWLEDGED` with `order_id=214f5b8e-ceb5-43b4-baff-9e4593961ecf` and `broker_status=pending_new`. Provenance is frozen and complete: `fixed-size-tlt-sell-001`, `behavior_digest=0da1e85c…`, universe `('TLT',)`, PROBATION, with a rationale that names the 226-share position at $17,745 against the $25,000 cap and confidence against the 0.5 floor.

The broker confirms the outcome independently: `status=filled`, `filled_qty=1`, `filled_avg_price=78.53`, `submitted_at=13:37:25.709692Z`, `filled_at=13:37:26.061456Z`, no replacement, no cancel, no failure. Open orders at 09:55 were **0**. Positions after the fill are six: BIL 209, SPY 32, TLT 225, XLB 1, XLE 2, XLF 2; account equity $99,805.72.

The BIL cycle also records the model fallback working as designed: `llm_unavailable: ValueError: LLM response did not contain a JSON object`, and the fixed-size strategy was used instead.

This is a real attributed execution on real fresh data. One filled share is not a return and not evidence of profitable decision making.

### Why the gate failed, and two defects it exposed

`SESSION_QUOTE_NOT_FRESH` is the 09:34:40 BIL record, and only that record: its quote was 1,546 s old against a 900 s limit because the bond ETF had not printed since the previous session. Guardian refused it, no order was sent, and the daemon carried on. The gate treats any stale-quote record in the window as a failure, so a correctly-refused data event is indistinguishable from a risky trade on stale data.

`fixed-size-bil-sell-001` was retired 58 seconds later. `STRATEGY_LIFECYCLE_UPDATED` at 09:39:38 moved it PROBATION → RETIRED with reason `severe operational failure rate 1.00`. The cause is a live defect, not bad trading:

- The BIL refusal reason is the interpolated message `'the price quote is 1611s stale, beyond the 900s limit'`.
- `SYSTEM_REJECTION_REASONS` contains only the non-interpolated `'data snapshot is stale'`; `SYSTEM_REJECTION_REASON_PREFIXES` contains no staleness prefix.
- `is_system_rejection()` therefore returns **False** for that message, so `strategy_fault_rejections` incremented, and with exactly one cycle in the window `failure_rate = 1.00` cleared the severe threshold.
- `strategy_engine.py` already documents that this exact mechanism retired the library's only SELL-capable strategy before, and fixed it by adding prefixes for the interpolated messages that were reachable at the time. The interpolated staleness message was not among them.

So a live, otherwise-healthy SELL strategy was permanently retired by the learner because a thinly traded symbol had no print in the first minutes of the session. This is the same defect class recurring, it happened 46 minutes into the new deployment, and it works directly against the RSI objective by destroying strategies on data events. No sample-size floor applies to the severe-failure rule either: one cycle was enough.

`fixed-size-bil-sell-001` has not been hand-restored. Restoring it outside the admission gate is the failure mode the `strategy_engine` comment records, so it goes through the gate after the classification is fixed.

Second defect, in the gate rather than the daemon: the readiness collector's safe-abstention predicate requires `execution.status == "SKIPPED"`, so PFAI's correctly-refused `selection_unavailable:` HOLD (`'symbol is priced below the minimum'`, 4.755 < the 5.0 floor, a price that slipped under the floor between the attention-name filter and the snapshot) would trip `SESSION_PROVENANCE_INVALID`. The collector does not model a mandate refusal as a safe abstention.

Neither defect is fixed here, and neither may be fixed today to make a session verdict pass. The 10:30 run re-reads the same 09:30–10:30 window, which already contains the stale BIL record, so a second `SESSION_QUOTE_NOT_FRESH` FAIL is the expected and honest outcome. Widening `stale_after_seconds`, excluding the offending window, or relaxing the collector's per-record assertion would each convert a true finding into a green light.
