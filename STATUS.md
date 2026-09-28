# QuantGroup — status

Last updated: 2026-09-28. This file replaces `LEARNING_STATUS.md`,
`learning_daemon_summary.md`, `findings.md`, `progress.md` and `task_plan.md`,
all of which described the system as running when it had been dead for 97 days.

## Where things stand

`minictrl doctor` is the source of truth. Run it; do not trust this file over it.

```
./minictrl doctor
```

Ollama has since been started on `:11434` serving `deepseek-r1:8b`, and the
LLM paths were verified live against the real model (see below). It currently
reports two blocking problems:

| Check | State |
|---|---|
| alpaca credentials | **FAIL** — `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` are absent from the environment and from every `.env` in the tree. The paper API itself is reachable (`GET /v2/clock` → HTTP 401, i.e. only the credential is missing) |
| daemon | **FAIL** — `heartbeat.json` claims `RUNNING`, but pid 2590767 is dead and the heartbeat is 97 days stale |
| proof: filled>0 | **WARN** — 23 orders were submitted, 0 fills were ever recorded |
| proof: per-strategy pnl | **WARN** — empty; needs broker closed-lot evidence |
| journal | OK — 16.9 MB, 5387 lines, 851 cycles, 0 unparseable |
| strategy library | OK — 15 strategies, 4 selectable, all parse |

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

All fifteen are fixed. 317 tests pass, up from 175.

## What is still required

1. **Alpaca paper credentials** — the one thing that cannot be done without
   you:

   ```bash
   export ALPACA_API_KEY="..." ALPACA_SECRET_KEY="..."
   ./minictrl doctor        # credentials check flips to PASS
   ./minictrl once          # one real paper cycle
   ./minictrl loop          # bounded run
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

- `submitted > 0`
- **and** `filled > 0`
- **and** per-strategy PnL attribution is non-empty and broker-derived
- **and** at least one lifecycle transition was driven by that PnL

Cycle counts, green review files, and "the daemon is running" are **not**
evidence. `minictrl doctor` prints these as `proof: submitted>0`,
`proof: filled>0` and `proof: per-strategy pnl` for exactly that reason.
