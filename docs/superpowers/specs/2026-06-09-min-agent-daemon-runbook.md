# Minimum Trading Agent Runbook

Date: 2026-06-09 (updated from 2026-06-03)

## Purpose

Run the 24x7 autonomous paper-trading daemon:

```text
Alpaca real data -> Strategy/LLM decision -> Guardian -> Alpaca paper order -> JSONL journal -> Reflection -> Curriculum
```

This runbook is **paper-trading only**. Live trading is rejected by configuration, Guardian, and executor. No arbitrary generated code is executed. Strategy specs are structured, validated, and Guardian-reviewed.

## Conda Environment

All commands must run in the `llm` conda environment:

```bash
conda activate llm
```

Or prefix with:

```bash
conda run -n llm ...
```

## Required Environment

```bash
export ALPACA_API_KEY="..."
export ALPACA_SECRET_KEY="..."
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"

export OLLAMA_BASE_URL="http://127.0.0.1:11434"
export MIN_AGENT_MODEL="deepseek-r1:8b"
export MIN_AGENT_GPU_DEVICES="0"
export CUDA_VISIBLE_DEVICES="0"

export MIN_AGENT_MODE="paper"
export MIN_AGENT_SYMBOLS="SPY"
export MIN_AGENT_ALLOWLIST="SPY,QQQ,AAPL,MSFT,NVDA"
```

Optional daemon settings:

```bash
export MIN_AGENT_DAEMON_INTERVAL_SECONDS="300"    # 5 min between cycles
export MIN_AGENT_MAX_TRADES_PER_DAY="10"
export MIN_AGENT_MAX_DAILY_CYCLES="288"
export MIN_AGENT_HEARTBEAT="runtime/min_agent/heartbeat.json"
export MIN_AGENT_PIDFILE="runtime/min_agent/daemon.pid"
export MIN_AGENT_STRATEGY_DIR="runtime/min_agent/strategies"
export MIN_AGENT_REFLECTION_WINDOW="50"
export MIN_AGENT_CURRICULUM_ENABLED="true"
```

`APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` are also accepted for Alpaca compatibility.

## Start Local Model

```bash
CUDA_VISIBLE_DEVICES=0 OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

In another terminal:

```bash
curl http://127.0.0.1:11434/api/tags
```

Confirm `deepseek-r1:8b` is listed.

## Verify Environment

```bash
PYTHONPATH=src conda run -n llm python -m min_agent.cli --check-env
```

The command must report:

- `missing_alpaca_credentials: []`
- `ollama_tags_accessible: true`
- `ollama_model_present: true`

## Run One Paper-Trading Cycle

```bash
PYTHONPATH=src \
OLLAMA_BASE_URL=http://127.0.0.1:11434 \
CUDA_VISIBLE_DEVICES=0 \
conda run -n llm python -m min_agent.cli --once
```

The cycle will stop before any order if:

- Alpaca credentials are missing.
- Alpaca base URL is not paper trading.
- Guardian rejects the decision.
- The market is closed and the model asks to BUY or SELL.

`HOLD` is allowed when the market is closed because it submits no order.

## Run the 24x7 Daemon

```bash
PYTHONPATH=src \
conda run -n llm python -m min_agent.cli --daemon
```

For a controlled smoke test with a cycle limit:

```bash
PYTHONPATH=src \
conda run -n llm python -m min_agent.cli --daemon --max-cycles 5
```

The daemon:

- Writes heartbeat to `runtime/min_agent/heartbeat.json`.
- Writes pidfile to `runtime/min_agent/daemon.pid`.
- Journals every cycle to `runtime/min_agent/journal.jsonl`.
- Reflects on recent cycles every 10 cycles (configurable).
- Proposes curriculum tasks every 50 cycles when curriculum is enabled.
- Survives single-cycle errors by journaling and backing off.
- Handles SIGTERM/SIGINT gracefully.

## Check Daemon Status

```bash
PYTHONPATH=src conda run -n llm python -m min_agent.cli --status
```

## Stop the Daemon

```bash
PYTHONPATH=src conda run -n llm python -m min_agent.cli --stop
```

Or directly:

```bash
kill -TERM $(cat runtime/min_agent/daemon.pid)
```

## Reconcile Orders

After crash/restart, compare journal orders with Alpaca open orders:

```bash
PYTHONPATH=src conda run -n llm python -m min_agent.cli --reconcile
```

## Use the Shell Wrapper

The daemon runs under systemd, which is the supported way to start it:

```bash
make run        # systemctl --user start min-agent.service
make status     # is it alive, and what is it doing
make stop
```

`run_forever.sh` was a foreground supervisor for the same job and has been deleted;
`min-agent.service` carries `Restart=always` and is `enabled`, which is what the
supervisor provided. For a bounded run without systemd, `make run` followed by
`make status` covers the same ground, and `min-agent --once` runs a single cycle
in the foreground.bash
conda run -n llm python -m pytest tests/min_agent -q
grep -R --exclude-dir='__pycache__' "exec(" -n src/min_agent || true
grep -R --exclude-dir='__pycache__' "shell=True\|return True" -n src/min_agent || true
```

These must find zero `exec(` calls and zero `shell=True` or forced `return True` in `src/min_agent`.

## Architecture Summary

| Component | File | Purpose |
|-----------|------|---------|
| Config | `config.py` | Frozen dataclass from env, paper-only |
| Models | `models.py` | Pydantic models, no mock data, structured strategies |
| DataGateway | `data_gateway.py` | Real Alpaca REST data, no fallbacks |
| DecisionEngine | `llm_decision.py` | Ollama structured JSON |
| Guardian | `guardian.py` | Hard risk gate, strategy review |
| Executor | `executor.py` | Paper-only, idempotent order IDs |
| Journal | `journal.py` | Append-only JSONL, corruption tolerant |
| StrategyEngine | `strategy_engine.py` | Structured specs, no exec() |
| ReflectionMemory | `reflection_memory.py` | Rolling outcome computation |
| Curriculum | `curriculum.py` | Structured task proposal |
| Daemon | `daemon.py` | 24x7 loop, pidfile, heartbeat, signal handling |
| Scheduler | `scheduler.py` | Market clock check |
| Health | `health.py` | Heartbeat read/write |
| OrderReconciler | `order_reconciler.py` | Journal vs broker open order check |
| CLI | `cli.py` | --check-env, --once, --daemon, --status, --stop, --reconcile |

## Non-Negotiable Constraints

- Paper trading only; live trading remains forbidden.
- Real data only; missing credentials/data fail closed, no mock/fallback.
- Guardian is the final hard safety authority; it must not be reachable/mutable by LLM output.
- No arbitrary generated code execution; strategies are structured specs.
- Every cycle, error, rejection, order, reflection, and strategy change is journaled.
- The daemon must recover safely after crash/restart and avoid duplicate orders.
