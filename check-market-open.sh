#!/bin/bash
# Market-open check via the Alpaca clock.
#
# Writes runtime/min_agent/market-state.json.
#
# This previously used `set -e` (not `pipefail`) around
# `python - <<'PY' | tee -a "$LOG_FILE"`, so the exit status of the whole script
# was `tee`'s -- always 0. `market-check.log` records a run on 2026-09-28
# containing the banner and the footer and none of the JSON body.
set -uo pipefail

cd "$(dirname "$0")" || exit 2

LOG_FILE="market-check.log"
CONDA_BIN="${CONDA_BIN:-/home/liuyime2/miniconda3/bin/conda}"
if [ ! -x "$CONDA_BIN" ]; then
  echo "FATAL: conda not found at $CONDA_BIN (set CONDA_BIN)" | tee -a "$LOG_FILE"
  exit 2
fi

{
  echo "========================================"
  echo "Market open check"
  echo "========================================"
  date
  echo
} | tee -a "$LOG_FILE"

PYTHONPATH=src timeout 120 "$CONDA_BIN" run --no-capture-output -n llm python - <<'PY' 2>&1 | tee -a "$LOG_FILE"
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

try:
    import alpaca_trade_api as tradeapi
except ImportError as exc:
    print(f"alpaca_trade_api not available: {exc}")
    sys.exit(2)

from min_agent.config import AgentConfig

cfg = AgentConfig.from_env()
missing = cfg.missing_alpaca_credentials()
if missing:
    print(f"missing Alpaca credentials: {missing}")
    sys.exit(2)

# tradeapi.REST appends api_version itself, so strip a trailing /v2.
base = cfg.alpaca_base_url.rstrip("/")
if base.endswith("/v2"):
    base = base[:-3]

client = tradeapi.REST(
    key_id=cfg.alpaca_api_key,
    secret_key=cfg.alpaca_secret_key,
    base_url=base,
    api_version="v2",
)
try:
    clock = client.get_clock()
except Exception as exc:
    print(f"could not read the broker clock: {type(exc).__name__}: {exc}")
    sys.exit(1)

state = {
    "is_open": bool(clock.is_open),
    "timestamp": str(clock.timestamp),
    "next_open": str(clock.next_open),
    "next_close": str(clock.next_close),
}
print(json.dumps(state, indent=2))
print(f"\nmarket: {'OPEN' if state['is_open'] else 'CLOSED'}")

out = Path("runtime/min_agent/market-state.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(state, indent=2))
PY
RC=${PIPESTATUS[0]}

{
  echo
  case "$RC" in
    0) echo "OK: clock read, market-state.json written" ;;
    1) echo "FAULT: broker clock unreachable (rc=1)" ;;
    *) echo "FAULT: check could not run (rc=$RC)" ;;
  esac
  echo "========================================"
  echo
} | tee -a "$LOG_FILE"

exit "$RC"
