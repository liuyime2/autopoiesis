# Minimum Trading Agent Runbook

Date: 2026-06-03

## Purpose

Run the phase-1 autonomous trading MVP:

```text
Alpaca real data -> DeepSeek 8B decision -> Guardian -> Alpaca paper order -> JSONL journal
```

This runbook is paper-trading only. Live trading is rejected by configuration and
Guardian.

## Required Environment

```bash
export ALPACA_API_KEY="..."
export ALPACA_SECRET_KEY="..."
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"

export OLLAMA_BASE_URL="http://127.0.0.1:11434"
export MIN_AGENT_MODEL="deepseek-r1:8b"
export MIN_AGENT_GPU_DEVICES="2,3"
export CUDA_VISIBLE_DEVICES="2,3"

export MIN_AGENT_MODE="paper"
export MIN_AGENT_SYMBOLS="SPY"
export MIN_AGENT_ALLOWLIST="SPY,QQQ,AAPL,MSFT,NVDA"
```

`APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` are also accepted for Alpaca
compatibility.

## Start Local Model

```bash
CUDA_VISIBLE_DEVICES=2,3 OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

In another terminal:

```bash
curl http://127.0.0.1:11434/api/tags
```

Confirm `deepseek-r1:8b` is listed.

## Verify Environment

```bash
PYTHONPATH=src OLLAMA_BASE_URL=http://127.0.0.1:11434 python -m min_agent.cli --check-env
```

The command must report:

- `missing_alpaca_credentials: []`
- `ollama_tags_accessible: true`
- `ollama_model_present: true`
- `gpu_2_3_visible: true`

## Run One Paper-Trading Cycle

```bash
PYTHONPATH=src \
OLLAMA_BASE_URL=http://127.0.0.1:11434 \
CUDA_VISIBLE_DEVICES=2,3 \
python -m min_agent.cli --once
```

The cycle will stop before any order if:

- Alpaca credentials are missing.
- Alpaca base URL is not paper trading.
- Guardian rejects the decision.
- The market is closed and the model asks to BUY or SELL.

`HOLD` is allowed when the market is closed because it submits no order.

## Verification Commands

```bash
python -m pytest tests/min_agent -q
grep -R --exclude-dir='__pycache__' "shell=True\|FORCED TRUE\|return True" -n src/min_agent tests/min_agent || true
```

## Current Verified State

On 2026-06-03:

- Unit tests passed: `19 passed`.
- Ollama served locally on `127.0.0.1:11434`.
- `deepseek-r1:8b` was installed and returned JSON in a health check.
- Ollama logs showed `CUDA_VISIBLE_DEVICES=2,3` and two visible CUDA devices.
- The real one-cycle command correctly stopped because Alpaca credentials were
  not present in the environment.
