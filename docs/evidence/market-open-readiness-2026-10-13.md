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
