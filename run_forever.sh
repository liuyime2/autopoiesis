#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
export MIN_AGENT_MODE="${MIN_AGENT_MODE:-paper}"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"
export APCA_API_BASE_URL="https://paper-api.alpaca.markets"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export MIN_AGENT_MODEL="${MIN_AGENT_MODEL:-deepseek-r1:8b}"
export MIN_AGENT_GPU_DEVICES="${MIN_AGENT_GPU_DEVICES:-0}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export MIN_AGENT_CURRICULUM_ENABLED="${MIN_AGENT_CURRICULUM_ENABLED:-true}"
export MIN_AGENT_MAINTENANCE_INTERVAL_SECONDS="${MIN_AGENT_MAINTENANCE_INTERVAL_SECONDS:-900}"
export MIN_AGENT_EVIDENCE_INTERVAL_SECONDS="${MIN_AGENT_EVIDENCE_INTERVAL_SECONDS:-3600}"
export MIN_AGENT_REFLECTION_INTERVAL_SECONDS="${MIN_AGENT_REFLECTION_INTERVAL_SECONDS:-1800}"
export MIN_AGENT_CURRICULUM_INTERVAL_SECONDS="${MIN_AGENT_CURRICULUM_INTERVAL_SECONDS:-3600}"

mkdir -p runtime/min_agent

echo "Starting min_agent 24x7 paper daemon in conda env: llm"
echo "Logs: runtime/min_agent/daemon.log"

while true; do
    echo "[$(date --iso-8601=seconds)] starting min_agent daemon" | tee -a runtime/min_agent/daemon.log
    conda run -n llm python -m min_agent.cli --daemon >> runtime/min_agent/daemon.log 2>&1 || true
    echo "[$(date --iso-8601=seconds)] daemon exited; restarting in 30 seconds" | tee -a runtime/min_agent/daemon.log
    sleep 30
done
