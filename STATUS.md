# QuantGroup — status

Last updated: 2026-09-28. This file replaces `LEARNING_STATUS.md`,
`learning_daemon_summary.md`, `findings.md`, `progress.md` and `task_plan.md`,
all of which described the system as running when it had been dead for 97 days.

## Where things stand

`minictrl doctor` is the source of truth. Run it; do not trust this file over it.

```
./minictrl doctor
```

Ollama is serving on `:11434` with `deepseek-r1:8b`, both LLM paths are
verified live against the real model, and the credentials work. The credentials
live in `$XDG_CONFIG_HOME/min-agent/env` (mode 600, outside the repository) — the
earlier "credentials FAIL" in this file was stale. With the stack up, `doctor`
reports **RESULT OK** with 0 failures and 2 warnings, both of them the closed-lot
criterion:

| Check | State |
|---|---|
| alpaca credentials | **PASS** — present in the external env file; paper endpoint reachable, market OPEN |
| daemon | PASS — `min-agent.service` up, pid alive, heartbeat fresh |
| proof: filled>0 | **PASS** — 23 orders submitted, 23 fills broker-confirmed, `fill_quantity_ratio` 1.0 |
| proof: per-strategy pnl | **WARN** — still empty; the agent holds 23 SPY and has never closed a lot |
| pnl evidence | **PASS** — broker portfolio history verified, `account_return_pct` 0.002 |
| journal | OK — 18 MB, 910 cycles, 0 unparseable |
| strategy library | OK — 23 strategies, 10 selectable, including one SELL strategy |

### The self-evolution loop now runs

This is the substantive change since the last update. Curriculum had been on
deterministic fallback for the entire audit — the loop was dead while every health
check reported green. It now runs end to end against the real model:

```
18:15:18 SUCCESS  source=llm  trend-follow-sell-002   →  ACCEPTED into PROBATION
```

The model closed its own capability gap. It proposed `fixed-size-sell-002` with
`action=SELL` after the engine stated which action the library could not express,
and the daemon admitted `trend-follow-sell-002`, after which
`capability_coverage` reported `uncovered_actions_now: []` — for the first time the
library can actually sell.

### What is still missing, and why

`proof: per-strategy pnl` is still empty, and it is now reachable rather than
structurally impossible. The remaining steps are all system behaviour, not
defects:

- The agent holds 23 SPY bought in June at an average of **742.03**; last price
  **767.25**, so +3.40% unrealized. Those lots are reconstructed from
  broker-confirmed fills and are closable.
- A SELL strategy is selectable (`trend-follow-sell-002`, PROBATION). The selector
  walks the probation queue oldest-first, so it is currently 6th behind five BUY
  strategies; each needs 3 cycles, so roughly 90 minutes of 5-minute cycles.
- Guardian will approve a SELL: `max_total_exposure` and `max_position_value` are
  BUY-gated, and the holdings check passes against 23 broker-confirmed shares.

What has **not** been done, deliberately: no risk limit was weakened, no prompt was
tuned to force a trade, and the market was not manipulated to produce a fill. The
49 BUY decisions and 0 fills are the system's own record.

## What was wrong, and what is fixed

Full detail, with reproduction evidence, in
[`docs/superpowers/plans/2026-09-28-quantgroup-recovery-and-refactor.md`](docs/superpowers/plans/2026-09-28-quantgroup-recovery-and-refactor.md).

Fifteen defects were confirmed against the live code and the 16.9 MB journal.
The ones that made the system structurally incapable of profiting:

- **Positions were never read.** `alpaca-trade-api 3.2.0` has no
  `get_all_positions` / `get_orders`; both calls raised `AttributeError` and
  were swallowed into `()`. All 851 recorded cycles carry an empty position
  book, so every SELL was rejected and there was no aggregate exposure cap. A
  second latent bug sat in the same path: Alpaca returns order `side` lowercase
  while the model requires uppercase, so open orders were being dropped too.
- **The reward function paid the maximum score for doing nothing**, and the
  selector was a deterministic `argmax` on it. The system evolved toward HOLD
  and locked itself there: 800 of 851 recorded cycles were HOLD.
- **Risk limits were auto-raised outside Guardian.** `auto-fix.sh` rewrote
  `max_position_value` upward and force-re-enabled strategies the lifecycle
  manager had retired, bypassing admission — forbidden by AGENTS.md §6.
- **A dead daemon reported healthy for 97 days** and passed 112 consecutive
  automated reviews, because `--status` replayed a file and always exited 0.
- **No fill feedback.** Orders filled; the journal recorded `filled_quantity
  0.0`; per-strategy PnL was therefore never computable.
- **The LLM made no trade decisions at all** in daemon mode, and the curriculum
  prompt contained a hard-coded example price that the model copied into nine
  admitted strategies.

All fifteen are fixed, and so were nine more found by a full trace of the
SELL → closed-lot → per-strategy-PnL path. 384 tests pass, up from 175, and
`tools/audit_defects.py` passes 91/91 across the 15 original defects. The nine:

- **The lot ledger was per-strategy.** The selector returns one strategy per cycle,
  so the strategy that opened a lot and the strategy that closed it were always
  different, and a SELL could only match lots in its own tracker. All four SELL
  decisions this agent ever made closed nothing, so `strategy_realized_pnl` was
  empty and could never be otherwise. Lots are now account-level, matched FIFO per
  symbol, attributed to the strategy that *opened* each lot.
- **The PnL evidence window was 24h** while the lots were opened weeks earlier, so
  a SELL inside the window matched nothing. `confirmed_fill_activities()` rebuilds
  broker-confirmed fills from the journal with no time limit.
- **Verified PnL was dropped before it could be used.** `if strategy_id in buckets`
  discarded it for any strategy without a cycle in the current window — computed,
  reported, then thrown away with no log line. The lifecycle manager's `cycles > 0`
  gate then ran before the PnL rule, making the only PnL-driven transition in the
  system unreachable for exactly the strategies that had real PnL.
- **`max_position_value` was applied to SELL.** A cap on new exposure silently
  rewrote an exit to HOLD, or truncated it. Now BUY-gated, matching Guardian.
- **`Guardian.min_confidence` was a hardcoded 0.5 that admission never checked**,
  so a strategy could be admitted that could never place an order.
- **The curriculum could not encode a conditional schema.** ollama's constrained
  decoder honours a flat `required` list and `enum` but ignores `const` and
  `additionalProperties` inside a `oneOf`; a `oneOf` schema was accepted in 6.4s
  and then ignored. Generation is now two flat constrained calls.
- **The model had to invent `created_at`.** A strategy admitted today came back
  with `created_at 2024-06-14`, and that field orders probation. The system owns
  timestamps now.
- **The decision context omitted the position's cost and the account's exposure.**
  `PositionSnapshot` carries only quantity and market_value, so the model was asked
  to decide about a position it had no way to evaluate, with no idea that every BUY
  was already impossible. It held for 26 consecutive cycles and bought 49 times,
  rejected 49 times.
- **No LLM decision was ever attributed to a strategy.** All 908 cycles carried
  `strategy_id=None`, so per-strategy metrics could not accumulate for the LLM path
  at all.

A tenth fix, found by running `--ingest-evidence`: a 30-day window spans 31
calendar dates, so Alpaca rejects the `15Min` timeframe and portfolio history came
back empty. Verified against the live API before changing anything.

## What is still required

1. **Rotate the Alpaca paper credentials.** They were pasted into this session and
   are in the transcript. The key pair should be regenerated in the Alpaca paper
   console and the external env file updated.

2. **A closed lot.** `proof: per-strategy pnl` stays empty until one exists. The
   path is open and reachable; see "What is still missing, and why" above. This
   needs time in market hours, not another fix.

3. **Reboot-persistent units.** The systemd units are installed under
   `/run/user/$UID/systemd/user` and do not survive a reboot, because `$HOME` is
   out of quota and the user manager cannot create `~/.config/systemd/user`.
   Freeing a few MB fixes it: `./minictrl install-service` then works.
   ./minictrl install-service && ./minictrl service enable --now
   ```

2. ~~A reachable Ollama~~ — **done.** The port mismatch recorded in
   `ollama.log` (`:11435`, `CUDA_VISIBLE_DEVICES=3`) is resolved: Ollama now
   serves on `127.0.0.1:11434` with `deepseek-r1:8b` on GPU 0. Both LLM paths
   were then verified against the real model:

   - trade decision, 8.4 s, valid JSON, `decision_source: "llm"`, grounded in
     the supplied context (`"No positions, no open orders... Holding position."`)
   - curriculum proposal, 2.5 s, parsed to a valid `CurriculumTask` with
     `source: "llm"`

   Start it again after a reboot with:

   ```bash
   OLLAMA_MODELS=/localscratch/liuyime2/ollama_local/models \
   OLLAMA_HOST=127.0.0.1:11434 CUDA_VISIBLE_DEVICES=0 \
     /localscratch/liuyime2/ollama_local/bin/ollama serve
   ```

## The only success criterion

A complete paper session in which, read from the journal:

- `submitted > 0` — **met**, 23 broker-confirmed
- **and** `filled > 0` — **met**, 23 shares, `fill_quantity_ratio` 1.0
- **and** per-strategy PnL attribution is non-empty and broker-derived
- **and** at least one lifecycle transition was driven by that PnL

The last two are unmet and need a closed lot. Cycle counts, green review files, and
"the daemon is running" are **not** evidence. `minictrl doctor` prints these as
`proof: submitted>0`, `proof: filled>0` and `proof: per-strategy pnl` for exactly
that reason.

---

## What is NOT working, stated plainly

**The agent has never closed a lot, so per-strategy PnL is still empty.** The
account holds 23 SPY bought in June. Four SELL decisions were attempted across the
whole history and all four were rejected; the causes were real bugs, now fixed, but
no SELL has reached the broker since. The agent has also bought 49 times and been
rejected 49 times on `total exposure exceeds hard limit` — the account carries
~$76k of mostly non-allowlist positions against a $20k cap, so **every BUY is
structurally impossible** until those are reduced. That limit has not been touched.

**The LLM holds, and mostly for reasons that are its own.** 26 consecutive
`llm`-sourced HOLD cycles before the recent BUY, with `hold_reason` of
`market_uncertain` or `no_signal`. The context has since been corrected to carry
cost basis, exposure, the hard limits and the selected strategy's mandate — the
model previously had none of these and could not have reasoned otherwise. What it
does with them is its own judgment, and it is not something to tune.

**The strategy library is accumulating near-duplicates.** 23 strategies, 10
selectable, 7 in PROBATION. The probation queue is served oldest-first, so a newly
proposed SELL strategy is 6th in line behind five BUY strategies. This is fair
rotation, not a bug, but it means a new capability takes roughly 90 minutes of
5-minute cycles to be exercised.

**The systemd units are not reboot-persistent.** They live in `/run/user/$UID`
because `$HOME` is at user quota, so the manager cannot create
`$HOME/.config/systemd/user`. Freeing a few MB under `$HOME` makes
`minictrl install-service` permanent.

**The key pair was pasted into a conversation and should be rotated** in the
Alpaca UI.
