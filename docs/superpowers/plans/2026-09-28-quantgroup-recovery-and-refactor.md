# QuantGroup Recovery & Refactor Plan

Date: 2026-09-28
Status: **Awaiting approval** — no source file has been modified yet.
Baseline commit for rollback: `4092bc4`
Scope: `/localscratch/liuyime2/QuantGroup`

---

## 0. TL;DR

The Python domain layer (`src/min_agent/`) is structurally sound and has 175 passing
unit tests. The system is nonetheless **dead since 2026-06-23** and, if restarted today,
would still never trade profitably, because of four independent defects that each
individually make profitability structurally impossible.

This plan restores runnability, then makes the agent's learning signal real, then makes
iteration fast. It is ordered so that every phase is independently verifiable and
independently revertable.

---

## 1. Goal / Non-Goals

### Goal

1. The daemon can be started, runs 24x7 under a real supervisor, and cannot be
   misreported as healthy when it is not.
2. The agent can actually **buy and sell**: real positions and open orders are read from
   the broker, aggregate exposure is capped, and fills flow back into the journal.
3. The self-evaluation loop **rewards exploration and broker-verified PnL**, so the
   system cannot evolve into a permanent HOLD latch.
4. Every LLM and fallback decision is **attributable in the journal** — no silent
   substitution of hard-coded values for model output.
5. Iteration becomes a **single command** with a non-zero exit code on any fault.

### Non-Goals (explicitly out of scope)

- **No live trading.** `MIN_AGENT_MODE=live` stays rejected in `config.py:46-47`.
  Guardian and executor paper-URL guards are unchanged and not weakened.
- **No strategy alpha work.** Designing a profitable strategy is out of scope. This plan
  makes the *measurement and feedback machinery* honest so alpha can be measured at all.
- **No `history_version/` work.** 7 archived, unrelated projects. Moved out of the tree,
  not refactored.
- **No parameter tuning to manufacture activity.** Removing the hard-coded
  `reference_price: 100.0` from the prompt is in scope; hand-picking parameters until
  orders appear is not.

---

## 2. Verified Defects (evidence, not inference)

Every item below was reproduced against the live code and the on-disk 16.9 MB journal.

| ID | Defect | Proof |
|---|---|---|
| **D1** | Positions and open orders are **never** read. `alpaca-trade-api 3.2.0` has no `REST.get_all_positions` / `REST.get_orders`; the correct names are `list_positions` / `list_orders`. Both calls raise `AttributeError` and are swallowed by `except Exception: return ()` at `data_gateway.py:69-70` and `:86-87`. | `hasattr` check on the real `REST` class; **851/851** journal cycle records have `positions: []` and `open_orders: []` |
| **D2** | The agent can therefore **never sell**: `guardian.py:74` sees an empty position book and rejects every SELL. The duplicate-order guard (`guardian.py:77`) is permanently inert. `total_exposure` at `guardian.py:80` is always `0.0`. `max_total_exposure` is never even passed (`cli.py:177-182`), so there is **no aggregate exposure cap**. `guardian.py:67` caps only the *increment*. | 4/4 SELL attempts rejected; `fixed-size-sell-001` RETIRED with "severe operational failure rate 1.00" — it was killed for *obeying* the risk limit |
| **D3** | The reward function **pays the maximum score for doing nothing**, and selection is a deterministic `argmax` on it. `evaluator.py:463-470` returns `1 - (errors+rejected)/cycles` with **no PnL evidence needed**, so a strategy that never trades scores `1.0`. `strategy_engine.py:139` then picks it forever. | `reflection.json`: `trend-follow-sell-001` = 36 cycles, 0 submitted, 0 rejected, `{HOLD: 36}`, **score 1.0** — beats `fixed-size-buy-001` (5 real orders, score 0.833). 800/851 journal cycles are HOLD. |
| **D4** | The only guard against D3 is disabled twice: `strategy_engine.py:103` returns `False` for any `kind != "FIXED_SIZE"` (the latch winner is `TREND_FOLLOW`), and `:107` requires `lifecycle == "PROBATION"` (permanently false once promoted to ACTIVE). | The latch winner is `ACTIVE`. |
| **D5** | Health reporting is a **replay of a file**. `health.py:17-20` only parses `heartbeat.json`; `cli.py:61-63` prints it and **always returns 0**. No `os.kill(pid, 0)`, no timestamp comparison. | `heartbeat.json` = `"status": "RUNNING"`, `pid 2590767`, `ts 2026-06-23T13:02:59Z`; `ps -p 2590767` → no such process. **97 days stale.** 112 of 113 `reviews/*.json` are byte-identical `{"ok": true, "issues": [], "tests_ok": true}` |
| **D6** | Any Alpaca blip **kills the daemon**. `scheduler.py:31-34` has no `try/except`; `daemon.py:100` calls it unguarded. `run_forever.sh:28` masks the crash with `\|\| true` → infinite 30 s crash loop. | Real traceback in `runtime/min_agent/daemon.out`: `requests.exceptions.ConnectTimeout` at `scheduler.py:34` |
| **D7** | The LLM makes **zero** trade decisions. `cli.py:189` injects `PolicyEngine` (static JSON lookup + arithmetic). `OllamaDecisionEngine.decide` is reachable only from `--once`. Meanwhile `curriculum.py:127-135` catches **every** LLM failure and returns a hard-coded `_fallback_task()`, which `daemon.py:249-262` then journals as `status="SUCCESS"`. | 42 `CURRICULUM_FAILED`; 30 are disguised fallbacks. `cli.py:232,238` hard-code `created_at` and `reference_price: 100.0` into the prompt → 9 admitted strategies carry `reference_price ∈ {100…200}` against SPY ≈ $735. |
| **D8** | Guardian's **correct** rejections are fed to the lifecycle manager as strategy defects, causing PAUSE/RETIRE for compliance, which `auto-fix.sh:36-39` / `auto_reviewer.py:133-135` then silently re-enable — bypassing `StrategyAdmission` and `Guardian.review_strategy`. `auto-fix.sh:41-44` additionally **raises** `max_position_value` to a hard-coded `5000.0`. | **Direct violation of AGENTS.md §6** ("no automatic weakening of hard risk limits"). `auto-fix.log:18` reports `fixed-size-sell-001` as `✓ 正常` while it is `lifecycle=RETIRED` and therefore unselectable (`strategy_engine.py:126`). |
| **D9** | `conda` is not on `PATH` (`~/.bashrc` is 1 byte). Every ops entrypoint hard-codes bare `conda`. Combined with `set -e` (not `pipefail`) + `| tee`, a `command not found` exits **0** and prints `✅ 无需修复`. | `auto-fix.log:31-39` and `market-check.log:63-72` are dated **today** and contain the banner and footer but none of the `[1/3][2/3][3/3]` body. |
| **D10** | No supervision of any kind. `crontab -l` → none. `systemctl --user` → no service units. `ps` → no `min_agent`, no `ollama`. The "cron" in `AUTO_REVIEW_README.md:44-50` is a table of **Claude session IDs**. `final_check_and_summary.py:66` prints a hard-coded `"✅ 每小时监控: Cron任务已设置"` that is never checked. | Verified live. |
| **D11** | Journal: no rotation, no size cap, no `fsync`, and **every** reader silently drops unparseable lines with no counter (`journal.py:28-31, 42-46, 60-63`). `trade_counter.py:16` does a full `read_all()` **every trading cycle**; maintenance does 6 full passes. Cost is O(file size). | 16.9 MB / 5 385 lines, ~1.2 MB/day, `read_all()` = 0.73 s today. `P&L_EVIDENCE_RECORDED` alone is 9.7 MB (58%). |
| **D12** | Non-atomic, unlocked writes to the live strategy library, which the daemon re-reads **every cycle** (`policy_engine.py:23`). A torn read makes `strategy_engine.py:38-39 except: continue` drop the strategy for that cycle → a phantom HOLD. | 9 write sites; `grep` for `os.replace|flock|fcntl|tempfile` in `src/` → **zero hits**. `auto-fix.sh:46` and `auto_reviewer.py:135` write the same 4 files with no lock against each other or the daemon. |
| **D13** | No fill feedback. `executor.py:63` reads `filled_qty` from the *submit* response (always 0 for a market order). Nothing re-polls. The 23 SUBMITTED orders fill, but the journal never learns it. | `reflection.json`: `submitted_orders: 5, filled_quantity: 0.0, fill_quantity_ratio: 0.0, pnl_evidence: "missing_fill_price_and_broker_activity"` |
| **D14** | The knowledge library is **write-only**. `knowledge_library.py:20,26,29` (`try_load`/`load`/`list`) have **zero** call sites. No lesson ever influences a decision. | `grep` across `src/` |
| **D15** | No version control. `git rev-parse` → `fatal: not a git repository`. Risk-limit rewrites are untraceable. **99.9%** of the tree is `history_version/` (4.5 GB of unrelated projects). | 4.7 MB excluding it. `screenlog.0` is 8.8 MB of unrelated Codex-CLI TUI escape codes. `skill_library/{knowledge,skills}.json` are `{}` while the real data sits in their `.bak` files. |

---

## 3. Design Decisions

### 3.1 Broker data is real or loudly absent (fixes D1, D13 prerequisite)

Introduce a typed error. **Never degrade broker data to an empty result.**

```python
# src/min_agent/data_gateway.py
class BrokerDataUnavailable(RuntimeError):
    """Broker state could not be read. Never degrade to an empty result: a
    risk check that reads 'no positions' when the API is down is unsound."""
```

- `_fetch_positions()` → `self.client.list_positions()`; transport errors propagate.
- `_fetch_open_orders()` → `self.client.list_orders(status="open")`.
- A record that cannot be priced is **not** silently dropped — it raises, because a
  position the gateway cannot price must not vanish from a risk check.
- `snapshot()` no longer computes a fake `daily_loss = equity - portfolio_value` (always
  `0.0`, which silently disables the daily-loss kill switch at `guardian.py:52`).
  Instead `AccountSnapshot` gains `day_start_equity_known: bool`; Guardian **fails
  closed** on BUY when it is `False`, with reason `account day-start equity unavailable`.

### 3.2 Failed cycles are journaled, not dropped (fixes an unlogged failure class)

`loop.py:20` currently calls `data_gateway.snapshot()` *outside* the try-block, so a
broker failure increments `error_count`, writes a heartbeat, and journals **nothing** —
invisible to the evaluator, reflector, and curriculum.

New event type `CYCLE_FAILED` is journaled instead of a `CycleRecord`. This deliberately
does **not** synthesise a placeholder `DataSnapshot`: fabricating a snapshot would violate
AGENTS.md §2. The event carries the symbol, error class, and message.

### 3.3 Score must require exploration (fixes D3, D4, D8)

`evaluator._score` is redefined as reliability × exploration, with PnL as a modifier that
can only *adjust* a score earned by acting:

```python
_SYSTEM_REJECTION_REASONS = frozenset({
    "market is closed", "max trades per day reached", "data snapshot is stale",
    "daily loss limit reached", "insufficient buying power",
    "conflicting open order exists", "only paper mode is allowed",
})

def _score(cycles, errors, rejected_orders, *, strategy_fault_rejections=0,
           trade_attempts=0, realized_pnl=None):
    if cycles <= 0:
        return 0.0
    operational = max(0.0, 1.0 - ((errors + strategy_fault_rejections) / cycles))
    exploration = trade_attempts / cycles
    score = operational * (0.25 + 0.75 * exploration)   # inaction is never competitive
    if realized_pnl is None:  return score              # no evidence -> no bonus
    if realized_pnl > 0:      return min(1.0, score + 0.1)
    if realized_pnl < 0:      return max(0.0, score - 0.2)
    return score
```

Two changes, both necessary:

1. **`trade_attempts / cycles` multiplier.** Doing nothing can no longer be optimal.
   Replayed against the real `reflection.json`:
   - `trend-follow-sell-001` (36 HOLD, 0 submitted) → `1.0 × 0.25` = **0.25**
   - `fixed-size-buy-001` (6 BUY, 5 submitted, 1 fault) → `0.833 × 1.0` = **0.833**

   The argmax now selects the strategy that actually trades. The latch is broken by
   construction, not by a special case.
2. **Guardian's correct rejections stop counting against the strategy.** A strategy
   stopped by "market is closed" or "max trades per day reached" was obeying the system.
   This is the root cause of D8's retire-then-reenable ping-pong.

`StrategyResult` gains `trade_attempts: int = 0`.

`_is_degenerate_no_exploration` (`strategy_engine.py:101-114`) drops the
`kind != "FIXED_SIZE"` and `lifecycle == "PROBATION"` preconditions and applies to any
tradable strategy with `trade_attempts == 0` over a real window.

### 3.4 Exploration floor — acquire evidence, then optimise (fixes D3 end-state)

If **no** eligible strategy has any trade evidence in the current window, the system has
no basis for selection, and the honest research move is a **bounded probe**, not a HOLD.
`StrategySelector.select` returns the smallest FIXED_SIZE BUY strategy that Guardian would
approve, with `rationale="exploration probe: no strategy has trade evidence in the current
window"`. Size is minimal (1 share) to bound cost. This is explicit, journaled, and
rate-limited by the existing `max_trades_per_day`.

Without this, a fresh install with no history would HOLD forever waiting for a score it
can never earn.

### 3.5 Health is liveness, not a file replay (fixes D5)

```python
class HealthMonitor:
    def __init__(self, heartbeat_path, *, stale_after_seconds=900, pid_probe=os.kill)
    def is_pid_alive(self, pid: int) -> bool
    def is_stale(self, now=None) -> bool
    def liveness(self) -> dict          # {"pid_alive", "stale", "age_seconds"}
    def status_json(self) -> str        # now includes "live": bool
```

`cli.py --status` returns **0 only when live**, non-zero otherwise. `doctor` (§3.8) is
built on this.

### 3.6 The market clock fails closed (fixes D6)

`MarketScheduler._clock` catches everything and returns `None`; `market_is_open()` already
returns `False` for `None`, so the effect is *no trading* — the correct fail-safe. The
exception is retained in `self.last_error` so the daemon journals why it backed off
instead of silently idling.

### 3.7 Provenance is disclosed, never disguised (fixes D7)

- `TradeDecision` gains `decision_source: Literal["llm", "fallback_policy_engine", "baseline"]`.
- New `HybridDecisionEngine` calls the LLM with a context built from **real** snapshot
  values. On failure it falls back to `PolicyEngine` and sets
  `decision_source="fallback_policy_engine"`. Guardian remains the final authority either
  way.
- `curriculum.py:_fallback_task` sets `CurriculumTask.source="fallback"`; `daemon.py`
  journals that value instead of `status="SUCCESS"`.
- `cli.py:232,238` hard-coded `created_at` / `reference_price: 100.0` are deleted from the
  prompt. The prompt receives real prices.

### 3.8 One command to run and to iterate (fixes D9, D10, D15)

`min_agent.cli doctor` is the new fast-iteration primitive. It checks credentials, Alpaca
paper reachability, Ollama + model presence, heartbeat **liveness**, journal size/growth,
strategy-library state, and reconciliation — printing a table and **returning non-zero on
any fault**. `minictrl` wraps it so iteration is one line.

`--loop --cycles N` runs a bounded, resumable loop for fast iteration; `--daemon` stays for
24x7 under a real supervisor.

---

## 4. Implementation Sequence

Each phase ends with a **verification gate**. Phases 0–2 are the blockers.

### Phase 0 — Baseline & hygiene (done / trivial remainder)
- [x] `git init` + `.gitignore` + baseline commit `4092bc4` *(rollback point)*
- [ ] `pytest.ini`: add `testpaths = tests` so root `test_alpaca.py` stops being collected
- [ ] Move `history_version/` → `/localscratch/liuyime2/_archive_QuantGroup/` (reversible `mv`)
- [ ] Delete `screenlog.0` (8.8 MB of unrelated ANSI garbage)
- [ ] Restore `skill_library/{knowledge,skills}.json` from `.bak`
- [ ] Delete orphan `engine.pid` / `ollama.pid`; unlink the `engine.log` symlink
- [ ] Remove the `key[:10]` / `secret[:10]` prints at `check_real_alpaca.py:27-28`
- [ ] Move `test_alpaca.py` → `tools/alpaca_smoke.py`; move the 12 one-off
      `check_*.py` / `fix_*.py` / `debug_*.py` → `tools/legacy/`
- [ ] Delete the 3 that mutate production state: `create_better_strategies.py`,
      `fix_and_optimize_strategies.py`, `disable_old_strategies.py`
- [ ] Delete dead modules containing fabricated-result factories:
      `reasoning_engine.py` (`_trade`/`_create_strategy` return `True`;
      `_reconcile` returns a fabricated `{"matched": True}`), `reflector.py`, `modes.py`

**Gate:** `git status` clean; `pytest tests/min_agent -q` still 175 passed; working tree
< 20 MB.

### Phase 1 — Make it runnable and honest (D1, D2, D5, D6)
- [ ] `data_gateway.py`: `BrokerDataUnavailable`, `list_positions`/`list_orders`,
      `AccountSnapshot.day_start_equity_known`
- [ ] `guardian.py`: fail closed on `not day_start_equity_known`; expose
      `max_total_exposure` audit
- [ ] `config.py`: add `MIN_AGENT_MAX_TOTAL_EXPOSURE` (default = `max_position_value × 4`);
      add `stale_after_seconds`
- [ ] `cli.py:177-182`: **pass** `max_total_exposure`
- [ ] `loop.py`: move `snapshot()` inside try; journal `CYCLE_FAILED`
- [ ] `models.py`: add `CYCLE_FAILED` to `JournalEventType`
- [ ] `health.py`: liveness + staleness (3.5)
- [ ] `cli.py:61-63`: `--status` returns non-zero when not live
- [ ] `scheduler.py`: fail-closed `_clock` + `last_error`
- [ ] New `tests/min_agent/test_data_gateway.py` — asserts the exact client method names
      exist, and that a raising client surfaces `BrokerDataUnavailable` rather than `()`
- [ ] Extend `tests/min_agent/test_health.py` — stale heartbeat and dead pid must be detected
- [ ] Extend `tests/min_agent/test_scheduler.py` — a raising clock must not propagate

**Gate:** all tests pass, and **the 97-day-stale heartbeat is now reported unhealthy**
(prove it before fixing anything else).

### Phase 2 — Break the latch (D3, D4, D8)
- [ ] `evaluator.py`: `_SYSTEM_REJECTION_REASONS`, fault-vs-system split, new `_score`
- [ ] `models.py`: `StrategyResult.trade_attempts`
- [ ] `strategy_engine.py`: degenerate guard without kind/lifecycle preconditions;
      exploration floor (3.4)
- [ ] Regression test reproducing the real `reflection.json` numbers and asserting
      `score(HOLD-only) < score(traded)`
- [ ] Regression test: a strategy rejected only by `max trades per day reached` is
      **not** PAUSEd or RETIREd
- [ ] `auto-fix.sh`: delete the `max_position_value` rewrite and the `lifecycle` override;
      become detect-and-report, exit non-zero
- [ ] `auto_reviewer.py:133-135`: same deletion

**Gate:** the latch regression test fails on the pre-fix code and passes after; `auto-fix.sh`
can no longer modify any risk limit (verified by a test that greps its own source).

### Phase 3 — Close the loop (D13, D14, D11, D12)
- [ ] New `src/min_agent/atomicio.py`: `write_text_atomic`, `write_json_atomic`, `FileLock`
- [ ] Route all 9 write sites through it; `fcntl.flock` on the strategy dir and the journal
- [ ] `strategy_engine.py:38-39`: log parse failures instead of `continue`
- [ ] `journal.py`: size-based rotation + `fsync` + a **dropped-line counter** exposed on
      the object; add a targeted `submitted_orders_on(day)` so `trade_counter` stops
      calling `read_all()` every cycle
- [ ] `executor`/`daemon`: `_resolve_pending_fills()` — re-poll unresolved orders by
      `client_order_id`, journal `ORDER_FILL_CONFIRMED` with qty and avg price
- [ ] `evaluator`: attribute fills per strategy (fixes `fill_quantity_ratio: 0.0`)
- [ ] `policy_engine`: read the knowledge library and inject lessons into the decision
      context (closes D14 — the library stops being write-only)

**Gate:** `fill_quantity_ratio > 0` and `pnl_evidence !=
"missing_fill_price_and_broker_activity"` after a real paper order; journal rotates.

### Phase 4 — LLM in the loop, honestly (D7)
- [ ] `HybridDecisionEngine` + `TradeDecision.decision_source`
- [ ] Delete hard-coded prompt values (`cli.py:232,238`)
- [ ] `CurriculumTask.source`; `daemon.py` journals it truthfully
- [ ] Re-admit strategy specs whose `reference_price` came from the fake prompt example

**Gate:** the journal can distinguish LLM output from fallback **by data inspection**;
zero `_fallback_task` results are labelled `SUCCESS`.

### Phase 5 — Infrastructure that works (D9, D10, D15)
- [ ] `cli doctor` (3.8) — the single health command, non-zero on any fault
- [ ] `minictrl` wrapper: `doctor | once | loop --cycles N | daemon | stop | report`
- [ ] `systemd --user` unit template + `minictrl install-service`; cap crash-loops with
      `StartLimitBurst`
- [ ] `set -euo pipefail` + explicit timeouts in every `.sh`; `auto_reviewer.py` gains
      `timeout=` and 7-day retention as `AUTO_REVIEW_README.md:56` already promises
- [ ] Re-enable curriculum; align `OLLAMA_BASE_URL` to the port ollama actually serves
- [ ] Rewrite `docs/superpowers/specs/2026-06-09-min-agent-daemon-runbook.md` — it currently
      promises "no fallbacks" in `data_gateway.py` and a live `cron` that never existed
- [ ] Replace the stale root `task_plan.md` / `findings.md` / `progress.md` / `LEARNING_STATUS.md`
      with current state

**Gate:** `minictrl doctor` exits 0 with ollama + creds present; killing the daemon makes
it exit non-zero; the supervisor restarts it and the heartbeat recovers.

### Phase 6 — End-to-end proof
One full paper session must produce, verified from the journal:
`submitted > 0` **and** `filled > 0` **and** non-empty per-strategy PnL attribution
**and** at least one lifecycle transition driven by that PnL.

**This is the only success criterion that counts.** Cycle counts, green review files, and
"daemon running" are not evidence of anything.

---

## 5. Trading-Agent Safety Gate (AGENTS.md §6)

| Requirement | How it is preserved |
|---|---|
| **Real-data provenance** | §3.1 removes every silent degradation to `()`. §3.2 forbids synthesising a snapshot. §3.7 makes fallback attribution explicit. A `DataSnapshot` can only exist if the broker produced it. |
| **Guardian enforcement** | Guardian stays the sole path to `executor.execute` (`loop.py:50-51`) and remains a frozen model. Phases 2 and 6 only ever **add** limits (`max_total_exposure`, fail-closed on unknown day-start equity). No phase removes a limit. |
| **No automatic weakening of limits** | §Phase 2 deletes the only code that raised a limit (`auto-fix.sh:41-44`). A test asserts `auto-fix.sh` and `auto_reviewer.py` contain no risk-limit assignment. |
| **Market-open checks** | `scheduler._clock` now fails closed; `guardian.py:58` unchanged. Three independent gates remain. |
| **Paper/live separation** | `config.py:46-47`, `guardian.py:36-37`, `executor.py:11-12`, `cli.py:113-115,157-159` — all untouched. |
| **Audit/journal output** | §3.2 journals failures; §3.7 journals provenance; §3.3 removes the drop-silently behaviour; Phase 3 adds fill records. The journal becomes a complete record rather than 78% unattributable. |
| **No fabricated results** | No phase introduces synthetic data. `reasoning_engine.py` is deleted *because* it fabricates `{"matched": True}`. Success claims in Phase 6 are verified by reading the journal, not by a self-reported status field. |
| **Autonomy bounds** | No LLM-generated shell execution exists or is added. Strategies remain validated structured specs; there is no `exec()`. |

---

## 6. Safety Constraints & Irreversible Actions

**Fully reversible** (Phase 0–2): all source edits are under git at `4092bc4`.
`git checkout -- <path>` or `git revert` restores any of it.

**Irreversible, requires explicit approval:**

| Action | Justification | Mitigation |
|---|---|---|
| Delete `screenlog.0` | 8.8 MB of unrelated Codex-CLI TUI escape codes; confirmed not produced by this project (`grep -rIn '\bscreen\b'` → 0 hits in the project) | none needed; zero information content |
| Delete `create_better_strategies.py`, `fix_and_optimize_strategies.py`, `disable_old_strategies.py` | They bypass `StrategyAdmission`/`Guardian` and write production state with a hard-coded `5000.0` — the mechanism by which risk limits were hand-edited (D8) | Preserved in git history at `4092bc4` |
| Delete `reasoning_engine.py` | Contains a fabricated-result factory (`_reconcile` → `{"matched": True}`); violates AGENTS.md §2 if ever wired | Preserved in git history at `4092bc4` |
| `git init` + move `history_version/` | Already done / reversible `mv` | The move is a `mv`; the path is recorded here |

**Never do without a new decision:** place a real (non-paper) order; lower a risk limit;
`git push`; delete `runtime/min_agent/journal.jsonl`.

---

## 7. Verification Commands

```bash
export PATH=/home/liuyime2/miniconda3/bin:$PATH
cd /localscratch/liuyime2/QuantGroup
export PYTHONPATH=src

# unit + regression suite (must stay green throughout)
conda run -n llm python -m pytest tests/min_agent -q

# fast health check — the primary iteration loop
conda run -n llm python -m min_agent.cli doctor ; echo "exit=$?"

# proves a dead daemon is now detected (currently it is NOT)
conda run -n llm python -m min_agent.cli --status ; echo "exit=$?"

# no exec / shell / forced-success in the trading path
grep -R --exclude-dir='__pycache__' -nE "exec\(|shell=True" src/min_agent || echo OK
grep -R --exclude-dir='__pycache__' -n "return True" src/min_agent || echo OK

# no auto-relaxation of risk limits outside Guardian
grep -RIn "max_position_value.*=.*5000" auto-fix.sh auto_reviewer.py || echo OK

# no fabricated data in the paper path
grep -RIn --exclude-dir='__pycache__' -E "mock|fake|fabricat|synthes|random\." src/min_agent || echo OK
```

**End-to-end success proof (Phase 6), read from the journal — not from any status file:**

```python
# submitted > 0, filled > 0, per-strategy PnL attribution non-empty
```

---

## 8. Known Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Phase 3's fill feedback surfaces **real losses** in the PnL attribution for the first time | Certain | This is the point. It must not be softened. Report it as measured. |
| Breaking the HOLD latch means the agent will actually trade and can lose paper money | Certain | Paper-only, capped by `max_position_value` / `max_total_exposure` / `max_daily_loss` / `max_trades_per_day`. A 1-share exploration probe bounds worst-case cost per probe. |
| 175 existing tests encode the old `_score` signature | Certain | Keep the old parameters as defaulted keywords; update only the assertions that encoded the reward-inaction bug. |
| Restart-loop-reset of in-memory curriculum counters (`curriculum.py:57`) means the curriculum never progresses | Certain | Move the counter into the journal so it survives restarts. Scheduled for Phase 4. |
| Ollama was last seen on port **11435** with `CUDA_VISIBLE_DEVICES=3`, but `run_forever.sh:11,14` configure `11434`/`GPU 0` | High | Resolve explicitly in Phase 5 by probing, not by assuming. |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` are **absent** from the environment and from every `.env` in the tree | High | Requires user action. `doctor` reports it; Phase 6 cannot complete without real paper credentials. |

---

## 9. Rollback

```bash
git log --oneline            # baseline: 4092bc4
git revert <commit>          # revert a whole phase
git checkout 4092bc4 -- <paths>   # restore specific files
git reset --hard 4092bc4     # full rollback (destroys later work)
```

`runtime/` is untracked, so no rollback can lose broker evidence or journal history.

---

## 10. Success Definition for the Whole Objective

The objective — *"review, think, heavily refactor, design, fix, achieve a fully working
end-to-end system, and make iteration fast"* — is met when **all** of the following hold,
each verified from primary evidence:

1. `minictrl doctor` exits 0 with credentials, ollama, and the broker reachable, and
   exits **non-zero** when the daemon is killed.
2. A supervised 24x7 run survives broker blips without exiting and without a stale heartbeat.
3. A real paper order is submitted, **fills**, and the fill is recorded in the journal.
4. Per-strategy PnL attribution is non-empty and derived from broker evidence.
5. The journal distinguishes LLM decisions from fallbacks **by inspection**.
6. No code path can raise a risk limit automatically.
7. A HOLD-only strategy cannot outscore a strategy that trades — proven by a regression test.
8. Iteration is one command: run, inspect, change, re-run, with a meaningful exit code.
