#!/bin/bash
# Capture one observation snapshot of agent + broker state.
#
# `--status` exits non-zero when the daemon is dead or its heartbeat is stale.
# That is the interesting result, not a reason to abort, so each step records
# its own exit code instead of aborting the sweep.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || exit 2

CONDA_BIN="${CONDA_BIN:-/home/liuyime2/miniconda3/bin/conda}"
if [ ! -x "$CONDA_BIN" ]; then
  echo "FATAL: conda not found at $CONDA_BIN (set CONDA_BIN)" >&2
  exit 2
fi

export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"
export APCA_API_BASE_URL="https://paper-api.alpaca.markets"

OBSERVATION_DIR="$ROOT_DIR/runtime/min_agent/observations"
mkdir -p "$OBSERVATION_DIR"

TIMESTAMP="$(date --utc +%Y%m%dT%H%M%SZ)"
OBSERVATION_FILE="$OBSERVATION_DIR/observation_${TIMESTAMP}.json"

agent() {
  timeout 180 "$CONDA_BIN" run --no-capture-output -n llm python -m min_agent.cli "$@"
}

FAILED=0

capture() {
  local label="$1" dest="$2"
  shift 2
  echo
  echo "--- $label ---"
  # `local rc=$?` would report local's own status, not the step's.
  local rc=0
  timeout 180 "$@" > "$dest" 2>&1 || rc=$?
  cat "$dest"
  if [ "$rc" -ne 0 ]; then
    echo "!! $label reported a fault (rc=$rc)"
    FAILED=$((FAILED + 1))
  fi
  return "$rc"
}

echo "=== observation $TIMESTAMP ==="

capture "daemon liveness" "$OBSERVATION_DIR/daemon_${TIMESTAMP}.json" agent --status
capture "broker reconciliation" "$OBSERVATION_DIR/reconcile_${TIMESTAMP}.json" agent --reconcile
capture "evidence report" "$OBSERVATION_DIR/evidence_${TIMESTAMP}.json" agent --evidence-report
capture "profit target" "$OBSERVATION_DIR/profit_${TIMESTAMP}.txt" agent --verify-profit-target

{
  echo
  echo "observed_at: $TIMESTAMP"
  echo "faulty_checks: $FAILED"
  echo "files:"
  for f in "$OBSERVATION_DIR"/*"${TIMESTAMP}"*; do
    echo "  - $(basename "$f")"
  done
} > "$OBSERVATION_FILE"

echo
if [ "$FAILED" -eq 0 ]; then
  echo "OK: observation captured at $OBSERVATION_FILE"
else
  echo "captured with $FAILED faulty check(s): $OBSERVATION_FILE"
fi
exit "$FAILED"
