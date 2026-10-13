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

## Actual scheduled 10:30 open verification — 10:30:00 to 10:30:43 EDT

It ran, and failed for the same reason, as predicted from the 09:45 evidence: `status=FAIL`, exit 1, `diagnostics=["SESSION_QUOTE_NOT_FRESH"]`, `ExecMainStatus=2`. Broker clock `2026-10-13T14:30:26Z`, `market_open=true`, `scheduler_trade_now=true`. Daemon `cycle_count=79`, `error_count=0`, model and broker healthy, six positions, **0** open orders, **0** unresolved submissions, journal 2,406 cycles / 21,900 events.

### Whole session, 09:30–10:30 EDT, from the journal

**80 cycle records, 0 errors.** 77 HOLD / 3 SELL. Executions 73 SKIPPED, 5 REJECTED, 2 SUBMITTED. Cycles per symbol: SPY 8, BIL 8, TLT 8, XLB 8, XLE 8, XLF 8, SOXL 7, BMBL 8, SOXS 8, CIFR 5, KORU 3, PFAI 1.

**Both submitted orders filled, confirmed independently by the broker.** `214f5b8e…` TLT SELL 1 filled at 78.53 (09:37:25.709692Z → 09:37:26.061456Z); `48914161…` TLT SELL 1 filled at 78.33 (10:13:44.690167Z → 10:13:44.822342Z). TLT went 226 → 224 shares. Open orders at 10:35 were **0**; equity $99,762.25. Every submission has a durable `ORDER_SUBMISSION_INTENT`, an `ORDER_SUBMISSION_ACKNOWLEDGED`, and an `ORDER_FILL_SNAPSHOT`. Nothing is unresolved, nothing was replaced or cancelled.

### The gate failure is structural, not incidental

All four stale-quote cycles are BIL, and only BIL:

| time | quote age | Guardian reason |
|---|---|---|
| 09:34:40 | 1546 s | the price quote is 1611s stale, beyond the 900s limit |
| 09:58:31 | 1240 s | the price quote is 1253s stale, beyond the 900s limit |
| 10:03:52 | 1561 s | the price quote is 1574s stale, beyond the 900s limit |
| 10:12:06 | 2055 s | the price quote is 2068s stale, beyond the 900s limit |

Half of BIL's eight cycles in the first hour carried a quote older than 900 s, with gaps of 20 to 34 minutes between prints. BIL is a bond ETF that does not trade continuously; after an overnight close, its next print arrives well past any freshness limit the system would care to trade on. Guardian refused all four and the session carried on, which is the correct behaviour.

But `SESSION_QUOTE_NOT_FRESH` fails the whole window on any such record, so with BIL in `AUTOPOIESIS_SYMBOLS` **this gate cannot pass**. It is not a signal that something went wrong today; it is a gate that measures a property the configuration guarantees. Three of the four were also recorded as `REJECTED` rather than `SKIPPED` even though they were `HOLD 0` with no strategy, because Guardian checks staleness before the HOLD branch. Those would independently trip `SESSION_PROVENANCE_INVALID`, whose safe-abstention predicate only accepts `SKIPPED`.

The honest reading of today is therefore two separate statements: the daemon behaved correctly on real data for a full hour with zero errors, and the certificate designed to grade it is unpassable for this symbol list. Both are recorded; neither is adjusted to make the other look better.

### What the learner did to the live library in one hour

`fixed-size-bil-sell-001` is now `RETIRED` on disk. `trend-follow-20260824-001` is now `PROBATION` with **`probation_restarts: 5`**, having been returned to probation five times between 10:02 and 10:24, each time for "served its full N-cycle probation budget without submitting an order" with N climbing 13, 14, 15, 15, 16. `MAX_PROBATION_RESTARTS` is **3**, so the restart cap is not binding on this path. `fixed-size-tlt-sell-001` is `PROBATION` with 0 restarts and is the only strategy that has traded.

`OFFLINE_VALIDATION_COMPLETED` reports `trend-follow-20260824-001` as `INCONCLUSIVE_INSUFFICIENT_EVIDENCE` twice, at 09:53 with "only 0 informative decision(s) out of 3" and at 10:19 with "out of 6". The strategy that covers SPY produces decisions the evaluator never counts as informative, so it can never be screened, so it can never leave probation, so it is handed another budget. That loop is the mechanism behind the library's long-standing zero-ACTIVE state.

The curriculum path also produced a false premise and one failure: `CURRICULUM_PROPOSED` at 09:56 asked for an exit path for TLT's 225 open shares "with no exit path in the current strategy set", while `fixed-size-tlt-sell-001` exists and had already sold. `STRATEGY_ADMISSION_REVIEWED` correctly refused the proposal as behaviourally identical to that strategy, and `OFFLINE_VALIDATION_COMPLETED` then returned INCONCLUSIVE for the near-duplicate `fixed-size-tlt-sell-002`. The admission gate did its job; the premise did not. `CURRICULUM_FAILED` with `ValueError` at 10:10.

### Net tradable state after one hour of open session

Six configured symbols: SPY held by a strategy that resets forever and never trades; BIL's only strategy retired by the staleness misclassification, leaving BIL uncovered; TLT the only symbol with a working strategy, which sold 2 shares; XLB, XLE and XLF uncovered. Four attention names and CIFR were cycled and could not act. Against a starting book of six positions, the measured result of the first hour is **two shares of TLT sold at 78.53 and 78.33**, no other symbol able to act, zero errors, zero unresolved submissions, and two certificate FAILs that are truthful.

No profitability claim follows from this. Sell prices are known, the cost basis is known, and the position carries a large pre-existing unrealized loss; attributing any of it to the session is not something this evidence supports.

## Deployment of the staleness-attribution fix — 14:06 to 14:23 EDT

Source `3619ff2ddac17037` deployed mid-session, replacing `9fb9066d820f27c9`. The switch
happened with the market open, at the operator's instruction, and every step below is an
observed value rather than an intention.

- **Pre-stop state recorded:** PID 3184440, RUNNING, 325 cycles, `error_count=1`, zero
  restarts since 05:53:18. The single error is the 10:10:04 `CURRICULUM_FAILED` ("constrained
  response contained no JSON object"), a maintenance-stage failure; no cycle carried an error
  at any point in the session.
- **Unit bytes preserved:** 14 service/timer files with SHA-256 sums at
  `/tmp/opencode/unit-backup-20261013-1406/`.
- **Timers paused** around the switch: `quant-watchdog`, `market-preflight`,
  `market-open-verify`, `quant-research`. `quant-backup` and `quant-report` were left alone.
- **Cooperative stop, measured:** stop requested 14:07:02, process gone 14:08:06,
  **64 s**, against the derived `TimeoutStopSec=630`. `Result=success`, `ExecMainStatus=0`,
  no SIGKILL, final heartbeat `STOPPED` with 325 cycles. This closes the one open
  operational measurement in the roadmap, which had recorded only that the earlier 06:08
  stop was not timed.
- **Post-stop journal snapshot:** `deployment-checkpoints/20261013/post-stop-journal-1407`,
  exit 0, 2,651 cycles, 22,107 events, four generations, **0 unparsed lines**, archive
  recovery `CERTIFIED`, `realized_pnl` 760.03 and `unrealized_pnl` 46.58 at snapshot time.
- **Application state checkpoint:** `deployment-checkpoints/20261013/predeploy-state-1408`,
  215 files / 311.3 MB across strategies, knowledge, observations, preregistration, reports,
  replay caches, readiness output, curriculum, reflection, risk baselines, research trials,
  signal trials, revisions and logs. All 215 SHA-256 hashes verified. Credentials excluded.
  (`du` reports a wrong total on this filesystem; the byte count is from `find -printf`.)
- **Units rendered:** `make install-service` wrote 10 files to
  `/home/liuyime2/.config/systemd/user`. All ten are **byte-identical** to the bytes that were
  running, so the switch did not alter any risk, mode or endpoint setting: `paper`,
  `shadow=false`, `shorts=shadow`, `manage_account=true`, position cap 25,000, exposure cap
  90,000, 100 attempts/day, `stale_after_seconds=900`. The service unit does not embed the
  fingerprint, so no unit change was required for the deploy itself.
- **Start:** 14:09:16, PID 3412695, zero restarts, `active (running)`. Heartbeat reports
  fingerprint `3619ff2ddac17037`, matching the on-disk source.
- **Startup reconciliation:** `ORDER_RECONCILIATION_REVIEWED` `matched: true` at 14:10:16.
  Broker read independently: account ACTIVE, equity 99,776.45, **0** open orders, five
  positions intact (BIL 209, SPY 32, TLT 218, XLB 1, XLF 1). Journal carries **0** unresolved
  submissions.
- **Readiness timers reinstalled with the new fingerprint.** Both units bake
  `EXPECTED_FINGERPRINT` into `ExecStart`, so without this every check tomorrow would have
  failed with `SOURCE_MISMATCH`. `systemctl --user show` confirms both now carry
  `3619ff2ddac17037`, with `DEPLOYMENT_AFTER=2026-10-13T14:09:16-04:00`. The install was
  directed at the manager's real unit directory explicitly, after the morning's incident in
  which `make install-readiness-timers` defaulted to `$XDG_CONFIG_HOME` and exited 0 having
  written somewhere the manager never reads. All six timers are armed: watchdog, backup,
  report, research, and the two readiness timers for 08:45 and 09:45/10:30 tomorrow.

### Live verification of the new deployment

`market-open-verify` against the new source, read-only, at 14:22 EDT: `status=WAIT`,
`exit_code=2`, diagnostic `FIRST_ROUND_COVERAGE_MISSING`, `profitability=UNPROVEN`. That is
the designed soft refusal, not a failure - the run was thirteen minutes into the session and
the first-round deadline is 19:09:16 EDT, so partial coverage is reported rather than failed.
Identity passed on every axis: the daemon's fingerprint equals both the requested fingerprint
and the current on-disk source, and there is no `SOURCE_MISMATCH`.

Session evidence in that window: 5 cycles, 5 of the 6 configured symbols observed (XLF not yet
cycled), 3 eligible cycles, 2 model decisions, 2 policy decisions, 1 fallback, 2 safe
unattributed abstentions, 1 submitted order, 1 pending fill observation, and
**`actionable_unexecuted: 0`** - nothing was approved and left unsent. Every cycle carried
`source=alpaca` with quote ages of 0, 2, 6, 114 and 15 seconds. The order submitted at 14:19:42
(`fixed-size-tlt-sell-001`, TLT SELL 1, `autopoiesis-0f313e6f17d9f95b`) filled at 78.39; TLT
went 218 to 217 and open orders returned to zero.

`CURRICULUM_PROPOSED` at 14:16 offered a "Fixed Size SPY Buy Probe" and
`STRATEGY_ADMISSION_REVIEWED` refused it because 21 candidates are in probation and short of
the 13-cycle budget - the capacity gate holding.

**The fix has not yet been exercised by a live stale refusal.** No stale-quote cycle occurred
under the new deployment in the verified window, so the claim that it works rests on the 21
tests and their measured red-to-green, not on a live refusal yet being attributed correctly.
The next BIL staleness event is the real test, and the attribution is observable in the next
`REFLECTION_GENERATED` payload.

## Session close under the fixed source — 14:09:16 to 16:47 EDT

The daemon ran the remainder of the session unchanged: one PID, `NRestarts=0`, 130 cycles,
`error_count=0`, no cycle carrying an error, last cycle at 16:02:54.

- 130 cycles across 11 symbols: SPY, BIL, TLT, XLB, XLE, XLF each 13; BMBL, CIFR and SOXS
  each 13; NOK 9; SOXL 4. 125 HOLD and 5 SELL. 124 SKIPPED, 5 SUBMITTED, 1 REJECTED.
- **Zero retirements.** Every lifecycle event after the switch is a
  `PROBATION -> PROBATION` return. Under the old source one strategy was retired in the first
  46 minutes of the session.

### The fix was exercised live, once

At 15:31:21 EDT, BIL's quote was 1,102 s old and Guardian refused it with
`'the price quote is 1115s stale, beyond the 900s limit'` - the interpolated message that
`is_system_rejection` returned `False` for this morning. Under the deployed code it returns
**True**, and no retirement followed.

The honest limit of that evidence: the cycle carried no strategy, because
`fixed-size-bil-sell-001` had already been retired, so the charge would not have been
attributed to anything under the old code either. What the live run proves is that the
classification is correct on the real message string. That it protected a specific strategy is
not demonstrated, and it will not be until BIL has a strategy to protect. The two findings are
the same finding.

### What the session produced that was not expected

**Correction to an earlier claim in this file.** An interim reading of this section stated
that `trend-follow-20260824-001` was stranded and that SPY therefore had no servable strategy.
That was inferred from `_needs_probation` arithmetic, not measured, and it is wrong. The real
selector was asked directly at 17:13 EDT via a read-only preflight against the 2026-10-14
session, and it reports **SPY `selectable=true`**, with the preview served by
`trend-follow-20261003-001`. The strategy has `probation_restarts=59` and about 43 cumulative
cycles against a granted budget of 52, so it still has budget; the "52" in the lifecycle
message is the evaluator's own counter and is not the same number. The published claim is
withdrawn. Commit `bd1cd13` carries it in its message and is not rewritten.

Measured per-symbol coverage, from the real selector:

| symbol | strategies in library | servable (PROBATION/ACTIVE, enabled) | state |
|---|---|---|---|
| SPY | 77 | **20** | heavily over-supplied, 33 of them PAUSED variants of the same idea |
| TLT | 1 | 1 | `fixed-size-tlt-sell-001`, trading |
| XLE | 1 | 1 | `fixed-size-xle-sell-001`, admitted during the session |
| XLF | 1 | 1 | `fixed-size-xlf-sell-001`, admitted during the session |
| **BIL** | 1 | **0** | its only strategy is the one retired by the staleness misclassification |
| **XLB** | **0** | **0** | never had a strategy at all; holds 1 share |

So the two real coverage gaps are BIL, which is recoverable through a re-admission path, and
XLB, which has no strategy because none was ever admitted rather than because one was lost.
SPY is the opposite problem: twenty servable candidates for one symbol, of which several carry
restart counters of 33, 59, 66 and 362 that stopped affecting anything past the third.

**The curriculum failed twice with the identical error** - `constrained response contained no
JSON object`, at 10:10:04 and again at 16:47:34. Both curriculum attempts today failed the same
way, which makes it a reproducible defect in the constrained-generation path rather than a
transient model failure.

**The curriculum keeps proposing a strategy that already exists.** Two admissions were refused
after the switch: at 14:55:54 with `strategy_id already exists; duplicate admission rejected`,
and at 15:40:01 as `behaviourally identical to existing strategy 'fixed-size-tlt-sell-001'`.
The gate is doing its job; the proposal path is not reading the current book before asking.

### Closing account state, read from the broker at 17:08 EDT

Clock closed, next open 2026-10-14 09:30 EDT. Account ACTIVE, equity **99,771.90**, **0** open
orders. Four positions: BIL 209 (+19.36), SPY 32 (+82.74), TLT 215 (-1,994.68), XLB 1 (+0.70).
XLF was exited entirely during the day. 15 orders filled today across both sources.

No return is claimed. Sell prices and cost basis is known and the book carries a large
pre-existing unrealized loss on TLT; nothing here attributes any of it to decision quality, and
the day's `PROFIT_TARGET_CHECKED` reads `profit target unproven`.

## Deployment of the re-admission path — 17:24 to 17:40 EDT

Source `712dad74d423eeef` deployed with the market closed, replacing `3619ff2ddac17037`.

- Unit bytes preserved (14 files) at `/tmp/opencode/unit-backup-20261013-1720/`. Watchdog,
  readiness and research timers paused around the switch.
- **Cooperative stop measured at 4 s** against the 630 s budget, `Result=success`,
  `ExecMainStatus=0`. Far below the 64 s of the mid-session switch earlier today, because with
  the market closed no synchronous stage was in flight at SIGTERM - which is the first real
  evidence that the stop budget is driven by in-flight stages rather than by a fixed cost.
- Post-stop journal snapshot: **CERTIFIED**, 2,781 cycles, 22,286 events, four generations,
  0 unparsed lines.
- Application state checkpoint `predeploy-state-1731`: 220 files / 311.4 MB, every SHA-256
  verified.
- `make install-service` rendered ten units, all **byte-identical** to the running bytes;
  mode, shadow, shorts and manage_account unchanged; `TimeoutStopSec=630`.
- Started 17:31:45, PID **3492811**, `NRestarts=0`, fingerprint `712dad74d423eeef` matching
  the on-disk source. `ORDER_RECONCILIATION_REVIEWED` `matched: true`. Broker read
  independently: equity 99,771.88, **0** open orders, four positions intact.
- Both readiness units reinstalled with the new fingerprint and
  `DEPLOYMENT_AFTER=2026-10-13T17:31:45-04:00`; `systemctl --user show` confirms both load
  `712dad74d423eeef`. All six timers armed.

### What the repair does, verified against a copy of the live library

Run read-only against a copy, so the live library was not touched:

    BEFORE: fixed-size-bil-sell-001 | RETIRED | readmissions: 0
            reason: severe operational failure rate 1.00
      accepted = False
      reason   = 23 candidate(s) are in probation, still short of the 13-cycle budget and
                 able to act at the current price, which is the cap; admitting another cannot
                 be evaluated and would further delay the strategies already waiting

The id-duplicate refusal is gone: the proposal now reaches the rest of the gate. **It is then
refused by the capacity check**, because the probation queue is full. The retirement is no
longer the binding constraint; the queue is.

### The queue is the actual bottleneck, measured

| | |
|---|---|
| library total | **81** |
| lifecycle | 23 PROBATION, 41 PAUSED, 16 RETIRED, 1 BASELINE, **0 ACTIVE** |
| probation candidates short of budget and servable | **23** |
| `max_probation_queue` | **13** |
| probation candidates by symbol | **SPY 20**, TLT 1, XLE 1, XLF 1 |
| by kind | 18 TREND_FOLLOW, 5 FIXED_SIZE |
| probation candidates with `probation_restarts` past `MAX_PROBATION_RESTARTS` | 6 |
| dormant probation | 0 |

The gate has therefore been closed all day and is still closed: the backlog is 10 over its own
cap, and nothing drains it because the only rule that removes a probation candidate requires
it either to submit an order or to have never attempted one. Eighteen of the twenty-three are
SPY `TREND_FOLLOW` variants distinguished only by `reference_price` (750 to 810 in five-dollar
steps) and `threshold_pct` (0.012 to 0.03). SPY trades near 778, so nearly all of those bands
bracket or sit just below the price and count as able to act.

`PROBATION_CYCLES = 13` is calibrated in its own comment against `a FIXED_SIZE produces them at
an observed 0.75-0.91 per selected cycle`, because the offline screen refuses to judge a
strategy below ten informative decisions. The queue is full of TREND_FOLLOW, and the measured
yield for that family is **0 informative decisions out of 33**. The budget and the screen are
therefore incompatible for most of what is queued: a candidate can serve its whole budget and
still be unjudgeable, and the only thing that happens next is another return to probation.

This is the constraint behind the rest of the day. With 0 ACTIVE strategies and the gate
closed, every cycle goes to probation candidates that cannot be screened, and the self-evolution
loop cannot admit, promote or retire anything into a decision it could learn from. Recorded here
rather than fixed, because raising `max_probation_queue` would reopen the gate by making the
queue bigger, which is the shape of a solution that looks like progress and is not.

### Why the queue is full: diagnosed, and the earlier reading corrected

The obvious reading - that `PROBATION_CYCLES = 13` is calibrated for FIXED_SIZE's yield and
cannot work for TREND_FOLLOW's - was measured and is **wrong**. The offline screen is not
short of budget; it has almost nothing to judge.

`trend-follow-20260824-001` has 43 decisions in the screen and **0 informative**, every one
`PENDING`. The counterfactual scores a decision as a one-share probe over the next
`DEFAULT_HORIZON_HOURS = 24`, so a decision is `PENDING` until a real quote exists at or
after that horizon. Every one of those 43 was decided on 2026-10-13. They are not missing;
they are not due yet.

The rows that *were* graded - 9 of them, verdicts GOOD_HOLD, NEUTRAL, NOT_EXECUTED - are all
pre-2026-10-13 cycles, and all nine fail `row_eligible` because their
`strategy_provenance` is `None`. Measured across the whole journal:

| date | SPY cycles attributed to a strategy **with** provenance | **without** |
|---|---|---|
| 2026-06-11 … 2026-10-12 | **0** | 473 |
| 2026-10-13 | **46** | **0** |

Every attributed SPY decision before today was recorded without strategy provenance, so the
screen refuses to attribute it - correctly, because it cannot prove which strategy produced a
decision whose behaviour digest it never saw. Those rows cannot be repaired: the provenance was
never written down.

So the order of causes is the opposite of the one first assumed:

1. Provenance was not being recorded at all until the source deployed at 05:53 today.
2. Every historical decision is therefore permanently unscoreable, and the screen has been
   reading them as silence for weeks.
3. Since the fix, 46 provenance-carrying SPY decisions exist. They become gradeable once 24
   hours of forward quotes exist - that is, from **2026-10-14 afternoon**.
4. Until then `scored` is 0, every candidate is `INCONCLUSIVE`, nothing is promoted, nothing is
   retired for a bad decision, and the probation backlog stays over its cap.

**Nothing here calls for a larger probation budget.** The budget is not the constraint; the
elapsed time since the provenance fix is. Calibrating per strategy family would have scaled a
number against a screen that was not measuring the thing the budget buys.

The testable prediction this leaves: `trend-follow-20260824-001` holds 43 of today's 46
provenance-carrying SPY decisions, and the screen needs ten informative ones. After the
2026-10-14 session produces forward quotes for them, that strategy's
`OFFLINE_VALIDATION_COMPLETED` should report `scored` above zero for the first time in the
journal's history, and the probation queue should begin to drain. If it does not, the cause is
somewhere else and this diagnosis is wrong.

Two more measured facts from the same dig. The counterfactual only ever evaluates **SPY** - all
493 `COUNTERFACTUAL_EVALUATED` events carry `symbol: SPY` and no other symbol has a row - so
`fixed-size-tlt-sell-001`, `fixed-size-xle-sell-001` and `fixed-size-xlf-sell-001`, the three
strategies that actually traded today, have **zero** decisions in the screen and cannot be
judged on any horizon. And `day_direction` returns **0 days** over these rows, so the
day-adjusted gate has never had a base rate to read a strategy's ratio against.

The scoring itself is sound: across 213,416 rows in the journal there are 189,912 non-`PENDING`
verdicts, so the grader works when it has gradeable input.
