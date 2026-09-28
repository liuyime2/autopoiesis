#!/bin/bash
# Read-only monitoring sweep over the min_agent CLI.
#
# `conda` is not on PATH in this environment (~/.bashrc is a single newline), so
# every script here used bare `conda`. Combined with `set -e` (not
# `pipefail`) and a `| tee` pipeline, a missing interpreter produced
# `command not found` on stderr, `tee` swallowed it, the step "succeeded", and
# the script printed a green banner. That is how `auto-fix.log` and
# `market-check.log` ended up containing a banner and a footer with nothing in
# between, while reporting success.
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

agent() {
  timeout 180 "$CONDA_BIN" run --no-capture-output -n llm python -m min_agent.cli "$@"
}

FAILED=0

echo "========================================"
echo "Quant Voyager monitoring sweep (read-only)"
echo "========================================"
date

run_step() {
  local label="$1"
  shift
  echo
  echo "--- $label ---"
  # `local rc=$?` would report local's own status, not the step's. Assign on the
  # right of `||` instead, where $? is still the command's.
  local rc=0
  "$@" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "!! $label FAILED (rc=$rc)"
    FAILED=$((FAILED + 1))
  fi
  return "$rc"
}

# --status now exits non-zero when the daemon is dead or its heartbeat is stale.
# That is the signal we want, so do not swallow it.
run_step "daemon liveness" agent --status
run_step "broker reconciliation" agent --reconcile
run_step "broker evidence ingest" agent --ingest-evidence
run_step "evidence report" agent --evidence-report
run_step "profit target" agent --verify-profit-target

echo
echo "========================================"
if [ "$FAILED" -eq 0 ]; then
  echo "OK: all ${#} checks reported healthy"
else
  echo "DRIFT: $FAILED check(s) reported a fault - see above"
fi
echo "========================================"

exit "$FAILED"
