# tools/

Operator utilities. **None of these are part of the trading path and none are
imported by `src/min_agent/`.** They are kept because an operator may still need
them for diagnosis; they are versioned so they can be repaired rather than
re-invented.

## `alpaca_smoke.py`

Read-only connectivity probe against the Alpaca paper API (clock, account,
positions, orders).

It was originally at the repository root as `test_alpaca.py`. The `test_`
prefix caused `pytest` to import a network-calling module on every run — see
`pytest.ini`'s `testpaths = tests`, which now also prevents this. Renamed so
the hazard cannot recur.

## `legacy/`

Archived one-off diagnostic scripts from the June 2026 debugging sessions.
Read-only unless noted. They predate the `StrategyAdmission` / `Guardian`
gating and some write runtime state directly:

| Script | Writes production state? |
|---|---|
| `check_learning.py` | no |
| `check_market.py` | no |
| `check_post_open.py` | no |
| `check_why_no_new_orders.py` | no |
| `debug_journal.py` | no |
| `debug_link.py` | no |
| `fix_alpaca_url.py` | no (misnamed; it only prints) |
| `final_check_and_summary.py` | no — but it prints a **hard-coded** `✅ 每小时监控: Cron任务已设置` that is never verified. Do not trust its output. |
| `check_real_alpaca.py` | no — partial-credential disclosure removed 2026-09-28 |
| `full_ingest.py` | **yes** — appends to the production journal |
| `monitor.py` | no |

Three scripts that wrote production strategy files while bypassing
`StrategyAdmission` and `Guardian.review_strategy` were **deleted**, not
archived: `create_better_strategies.py`, `fix_and_optimize_strategies.py`,
`disable_old_strategies.py`. They hard-coded `max_position_value = 5000.0` and
were the mechanism by which risk limits were hand-edited outside Guardian,
which AGENTS.md §6 forbids. Recover them from commit `4092bc4` if the
investigation is ever needed.
