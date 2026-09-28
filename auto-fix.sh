#!/bin/bash
# Detect-and-report for core strategy-file drift.
#
# This script deliberately has NO repair capability.
#
# It used to rewrite strategy JSON files to force `enabled=true`, force
# `lifecycle="PROBATION"`, and RAISE `max_position_value` to a hard-coded 5000.0.
# Every one of those is forbidden by AGENTS.md section 6:
#
#   - raising a risk limit automatically weakens a hard risk control;
#   - forcing `lifecycle` overrode a StrategyLifecycleManager decision made
#     from real evidence;
#   - both bypassed StrategyAdmission and Guardian.review_strategy entirely.
#
# It also reported `fixed-size-sell-001` as "normal" while that file was
# `enabled=true, lifecycle=RETIRED` and therefore unselectable, because it only
# ever checked `enabled`.
#
# Risk-limit and lifecycle changes must flow through Guardian + StrategyAdmission
# as an explicit, journaled operation. This script only observes.
#
# Exit codes: 0 = healthy, 1 = drift detected, 2 = could not check.
set -uo pipefail

cd "$(dirname "$0")" || exit 2
LOG_FILE="auto-fix.log"

CONDA_BIN="${CONDA_BIN:-/home/liuyime2/miniconda3/bin/conda}"
if [ ! -x "$CONDA_BIN" ]; then
  echo "FATAL: conda not found at $CONDA_BIN (set CONDA_BIN)" | tee -a "$LOG_FILE"
  exit 2
fi

{
  echo "========================================"
  echo "QuantVoyager strategy drift check (read-only)"
  echo "========================================"
  date
  echo
} | tee -a "$LOG_FILE"

PYTHONPATH=src "$CONDA_BIN" run --no-capture-output -n llm python - <<'PY' 2>&1 | tee -a "$LOG_FILE"
import json
import sys
from pathlib import Path

CORE = ["fixed-size-buy-001", "fixed-size-sell-001",
        "trend-follow-buy-001", "trend-follow-sell-001"]
STRAT_DIR = Path("runtime/min_agent/strategies")
PID_FILE = Path("runtime/min_agent/daemon.pid")

problems = 0

print("[1/2] Core strategy files (read-only)...")
if not STRAT_DIR.is_dir():
    print(f"  ERROR: {STRAT_DIR} does not exist")
    sys.exit(2)
for name in CORE:
    path = STRAT_DIR / f"{name}.json"
    if not path.exists():
        print(f"  DRIFT {name}: file missing")
        problems += 1
        continue
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        print(f"  DRIFT {name}: unreadable ({exc})")
        problems += 1
        continue
    enabled = data.get("enabled", False)
    lifecycle = data.get("lifecycle", "<unset>")
    max_pos = data.get("max_position_value")
    selectable = bool(enabled) and lifecycle not in {"PAUSED", "RETIRED"}
    if not selectable:
        print(f"  DRIFT {name}: enabled={enabled} lifecycle={lifecycle} -> unselectable")
        problems += 1
    else:
        print(f"  ok    {name}: enabled={enabled} lifecycle={lifecycle} max_position_value={max_pos}")

print("\n[2/2] Daemon pidfile...")
if PID_FILE.exists():
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        print(f"  DRIFT pidfile is not an integer: {PID_FILE.read_text().strip()!r}")
        problems += 1
    else:
        try:
            import os

            os.kill(pid, 0)
            print(f"  ok    daemon pid {pid} is running")
        except ProcessLookupError:
            print(f"  DRIFT stale pidfile: pid {pid} is not running")
            problems += 1
        except PermissionError:
            print(f"  ok    daemon pid {pid} exists (not signalable)")
        except Exception as exc:
            print(f"  DRIFT pid probe failed: {exc}")
            problems += 1
else:
    print("  info  daemon not running (no pidfile)")

print(f"\ndrift_count: {problems}")
print("NOTE: this script no longer repairs anything by design (AGENTS.md 6).")
print("      To change a risk limit or a lifecycle state, use StrategyAdmission")
print("      via the curriculum path so the change is journaled and Guardian-reviewed.")
sys.exit(1 if problems else 0)
PY
RC=${PIPESTATUS[0]}

{
  echo
  echo "========================================"
  case "$RC" in
    0) echo "OK: no drift detected" ;;
    1) echo "DRIFT DETECTED: $RC issue(s) - see above. Not auto-repaired by design." ;;
    *) echo "CHECK FAILED (rc=$RC): could not verify state" ;;
  esac
  echo "========================================"
  echo
} | tee -a "$LOG_FILE"

exit "$RC"
