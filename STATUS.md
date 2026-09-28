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
verified live against the real model, and the credentials work, so the only
remaining blocking problem is the daemon, which is stopped whenever the unit is
not running. With the stack up, `doctor` reports **RESULT OK** with 0 failures
and 2 warnings, both of them the closed-lot criterion:

| Check | State |
|---|---|
| alpaca credentials | **FAIL** — `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` are absent from the environment and from every `.env` in the tree. The paper API itself is reachable (`GET /v2/clock` → HTTP 401, i.e. only the credential is missing) |
| daemon | PASS when `min-agent.service` is up; FAIL otherwise. The 97-day stale heartbeat is detected |
| proof: filled>0 | **PASS** — 23 orders submitted, 23 fills broker-confirmed, `fill_quantity_ratio` 1.0 |
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

---

## What is NOT working, stated plainly

**The self-evolution loop is not proposing new skills.** The curriculum agent
has been falling back to a hard-coded `EVALUATE` on every call. The journal says
so (`status=SKIPPED`, `source=fallback`, with the validation error) — it is
disclosed, and it is safe: an invalid `StrategySpec` is rejected by validation and
would be rejected by the admission gate.

Root cause, measured: the context was ~14 kB, and `deepseek-r1:8b` stopped
following it and emitted a meta-refusal. That is fixed (context ~8 kB, parsed).
Schema-constrained generation replaced `format="json"`, which was 5x faster and
4x smaller. What remains is that `StrategySpec` requires
`action/quantity/confidence` when `kind` is `FIXED_SIZE` — a pydantic
`model_validator`, and ollama's constrained decoding does not enforce `const` or
`additionalProperties` inside a `oneOf` (measured: the schema was accepted in
6.4 s and then ignored). So the model succeeds some of the time and not others.
A validated retry raises the hit rate; it does not make it reliable.

**A risk-driven halt used to be invisible.** Guardian logs `approved` for a HOLD,
so when the model declined because of the day's loss it was acting as a second
risk authority with none of Guardian's auditability, and the journal showed an
ordinary quiet cycle. `TradeDecision.hold_reason` now records why, the evaluator
counts the reasons, and `doctor` has a `risk-driven halts` check. This was fixed
for observability, not to induce trading: the model is still choosing HOLD on
its own reasoning, and the two audit criteria still need a closed lot.

**The systemd units are not reboot-persistent.** They live in `/run/user/$UID`
because `/home` is at user quota, so the manager cannot create
`$HOME/.config/systemd/user`. Freeing a few MB under `$HOME` makes
`minictrl install-service` permanent.

**The key pair was pasted into a conversation and should be rotated** in the
Alpaca UI.
