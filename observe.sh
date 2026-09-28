#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"
export APCA_API_BASE_URL="https://paper-api.alpaca.markets"

OBSERVATION_DIR="$ROOT_DIR/runtime/min_agent/observations"
mkdir -p "$OBSERVATION_DIR"

TIMESTAMP="$(date --utc +%Y%m%dT%H%M%SZ)"
OBSERVATION_FILE="$OBSERVATION_DIR/observation_${TIMESTAMP}.json"

echo "=== 执行观察: $TIMESTAMP ==="

# Daemon status
echo -e "\n[1/5] 检查 Daemon 状态..."
PYTHONPATH=src conda run -n llm python -m min_agent.cli --status | tee "$OBSERVATION_DIR/daemon_${TIMESTAMP}.json"

# Broker reconcile
echo -e "\n[2/5] 检查 Broker 对账..."
PYTHONPATH=src ALPACA_BASE_URL=https://paper-api.alpaca.markets conda run -n llm python -m min_agent.cli --reconcile | tee "$OBSERVATION_DIR/reconcile_${TIMESTAMP}.json"

# Evidence report
echo -e "\n[3/5] 生成证据报告..."
PYTHONPATH=src ALPACA_BASE_URL=https://paper-api.alpaca.markets conda run -n llm python -m min_agent.cli --evidence-report | tee "$OBSERVATION_DIR/evidence_${TIMESTAMP}.json"

# Profit target
echo -e "\n[4/5] 验证盈利目标..."
PYTHONPATH=src ALPACA_BASE_URL=https://paper-api.alpaca.markets conda run -n llm python -m min_agent.cli --verify-profit-target || true

# Full snapshot
echo -e "\n[5/5] 生成完整快照..."
PYTHONPATH=src conda run -n llm python observe.py | tee "$OBSERVATION_FILE"

echo -e "\n✅ 观察完成: $OBSERVATION_FILE"
