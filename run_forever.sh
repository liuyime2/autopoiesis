#!/bin/bash
# Foreground supervisor for the min_agent paper daemon.
#
# Prefer a real service manager (`systemd --user`, see
# docs/superpowers/specs/2026-09-28-min-agent-runbook.md). This wrapper exists
# for interactive use and adds the two things the original version lacked:
#
#   - crash-loop detection. A broker blip used to kill the daemon every 30s
#     forever, appending ~2,880 log lines a day with no signal that anything was
#     wrong, while heartbeat.json kept claiming "RUNNING".
#   - a real interpreter path. `conda` is not on PATH here.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || exit 2

CONDA_BIN="${CONDA_BIN:-/home/liuyime2/miniconda3/bin/conda}"
if [ ! -x "$CONDA_BIN" ]; then
  echo "FATAL: conda not found at $CONDA_BIN (set CONDA_BIN)" >&2
  exit 2
fi

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

RESTART_DELAY_SECONDS="${RESTART_DELAY_SECONDS:-30}"
MAX_FAST_RESTARTS="${MAX_FAST_RESTARTS:-5}"
FAST_EXIT_WINDOW_SECONDS="${FAST_EXIT_WINDOW_SECONDS:-300}"
CRASH_LOOP_EXIT_CODE=75

mkdir -p runtime/min_agent
LOG=runtime/min_agent/daemon.log

echo "Starting min_agent paper daemon (conda env: llm)"
echo "Logs: $LOG"

fast_restarts=0

while true; do
  started_at=$(date +%s)
  echo "[$(date --iso-8601=seconds)] starting min_agent daemon" | tee -a "$LOG"

  "$CONDA_BIN" run --no-capture-output -n llm python -m min_agent.cli --daemon >> "$LOG" 2>&1
  rc=$?
  ended_at=$(date +%s)
  uptime=$((ended_at - started_at))

  echo "[$(date --iso-8601=seconds)] daemon exited rc=$rc after ${uptime}s" | tee -a "$LOG"

  if [ "$uptime" -lt "$FAST_EXIT_WINDOW_SECONDS" ]; then
    fast_restarts=$((fast_restarts + 1))
    echo "[$(date --iso-8601=seconds)] fast exit #$fast_restarts (under ${FAST_EXIT_WINDOW_SECONDS}s)" | tee -a "$LOG"
    if [ "$fast_restarts" -ge "$MAX_FAST_RESTARTS" ]; then
      echo "CRASH LOOP: $fast_restarts restarts within ${FAST_EXIT_WINDOW_SECONDS}s each." | tee -a "$LOG"
      echo "Refusing to restart again. Diagnose before relaunching:" | tee -a "$LOG"
      echo "  tail -n 60 $LOG" | tee -a "$LOG"
      echo "  PYTHONPATH=src $CONDA_BIN run -n llm python -m min_agent.cli --check-env" | tee -a "$LOG"
      exit "$CRASH_LOOP_EXIT_CODE"
    fi
  else
    fast_restarts=0
  fi

  echo "[$(date --iso-8601=seconds)] restarting in ${RESTART_DELAY_SECONDS}s" | tee -a "$LOG"
  sleep "$RESTART_DELAY_SECONDS"
done
