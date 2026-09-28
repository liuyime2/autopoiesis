# min_agent runbook

Supersedes `2026-06-09-min-agent-daemon-runbook.md`, which promised things the
code did not do: it described `data_gateway.py` as having "no fallbacks" when it
degraded every broker error to an empty position book, and documented a `cron`
entry that never existed.

Run `minictrl help` for the command list. `minictrl doctor` is the health
check, and it exits non-zero on any fault.

## Paper trading only

Live trading is rejected in three independent places and none of them may be
relaxed:

- `config.py` — `MIN_AGENT_MODE=live` raises
- `Guardian.review` — non-paper mode is rejected before any other check
- `AlpacaPaperExecutor.__init__` — a non-paper base URL refuses to construct
- `cli.py` — `--once` and `--daemon` both verify the URL before wiring anything

## Environment

`conda` is **not** on `PATH` in this environment (`~/.bashrc` is a single
newline). Every entrypoint uses the absolute path:

```bash
export CONDA_BIN=/home/liuyime2/miniconda3/bin/conda   # or let the scripts default
```

### Credentials

```bash
export ALPACA_API_KEY="..."     # paper key
export ALPACA_SECRET_KEY="..."
```

For the systemd unit, put them in `~/.config/min-agent/env` (`chmod 600`) so no
secret is baked into a unit file:

```bash
install -Dm600 /dev/null ~/.config/min-agent/env
printf 'ALPACA_API_KEY=...\nALPACA_SECRET_KEY=...\n' >> ~/.config/min-agent/env
```

### Local model

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama serve
curl -s http://127.0.0.1:11434/api/tags | grep deepseek-r1
```

**Port and GPU must match the agent's configuration.** The last recorded ollama
run served on `:11435` with `CUDA_VISIBLE_DEVICES=3` while the agent was
configured for `:11434` and GPU 0. Check both before blaming the model.

## Everyday use

```bash
./minictrl doctor          # everything; non-zero on any fault
./minictrl once            # one paper cycle
./minictrl loop            # bounded daemon run, default 5 cycles
./minictrl daemon          # foreground, for debugging
./minictrl status          # heartbeat liveness
./minictrl test            # the 317-test suite
```

## Running 24x7

```bash
./minictrl install-service
./minictrl service enable --now
./minictrl logs            # journalctl -f
```

The unit has `Restart=always` with `StartLimitBurst=5` inside 15 minutes, so a
permanently broken daemon stops being restarted instead of spinning forever.
`run_forever.sh` has the same protection for interactive use, and is otherwise a
thin wrapper kept for muscle memory.

## Tuning

All values are environment variables; see `src/min_agent/config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `MIN_AGENT_SYMBOLS` | `SPY` | instruments to trade |
| `MIN_AGENT_ALLOWLIST` | `SPY,QQQ,AAPL,MSFT,NVDA` | Guardian allowlist |
| `MIN_AGENT_MAX_POSITION_VALUE` | `5000` | per-order cap |
| `MIN_AGENT_MAX_TOTAL_EXPOSURE` | `4 × position` | **aggregate** cap; was never wired before |
| `MIN_AGENT_MAX_DAILY_LOSS` | `500` | daily loss kill switch |
| `MIN_AGENT_MAX_TRADES_PER_DAY` | `10` | daily order cap |
| `MIN_AGENT_STALE_AFTER_SECONDS` | `900` | heartbeat is dead past this |
| `MIN_AGENT_CURRICULUM_ENABLED` | `false` | turn the LLM curriculum on |

Risk limits may only be changed here, in config, or through
`Guardian` + `StrategyAdmission` as a journaled operation. A test in
`tests/min_agent/test_governance.py` enforces that no ops script may assign one.

## Reading the journal

```bash
./minictrl report                       # broker-backed PnL evidence
./minictrl evidence                     # re-ingest broker activities
grep '"event_type":"ORDER_FILL_CONFIRMED"' runtime/min_agent/journal.jsonl | tail
```

The journal rotates at 64 MB with 3 backups, and reports how many unparseable
lines it dropped.

## How the loop works

```
Alpaca paper API
  -> AlpacaDataGateway     real positions, open orders, prices. Fails loudly.
  -> HybridDecisionEngine  LLM decides; PolicyEngine is the disclosed fallback
  -> Guardian              hard risk gate; final authority
  -> AlpacaPaperExecutor   paper-only, idempotent client order ids
  -> JsonlJournal          append-only, rotated, corruption-counted
  -> FillReconciler        polls the broker for fills
  -> DeterministicEvaluator  reliability x exploration, broker PnL
  -> ReflectionMemory      rolling strategy results
  -> StrategyLifecycleManager  PROBATION -> ACTIVE -> PAUSED/RETIRED
  -> StructuredCurriculumAgent  LLM proposes new skills
  -> StrategyAdmission     Guardian-reviewed, progression-gated
```

Each decision records `decision_source` (`llm`, `fallback_policy_engine`,
`policy_engine`, `baseline`). Each curriculum task records `source` (`llm` or
`fallback`). Neither is ever disguised.

## Non-negotiable constraints

- Paper trading only.
- Real data only; missing broker data raises `BrokerDataUnavailable` rather than
  degrading to an empty result, and a failed cycle journals `CYCLE_FAILED`
  without inventing a snapshot.
- Guardian is the final authority and is not reachable from LLM output.
- No generated code is executed. Strategies are validated structured specs.
- Every cycle, error, rejection, fill, reflection, and lifecycle change is
  journaled.
- Fills are not PnL. `pnl_evidence` stays `missing_*` until closed-lot broker
  evidence exists.
