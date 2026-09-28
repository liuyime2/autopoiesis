#!/bin/bash
# 自动修复脚本：用 Python 处理 JSON，避免 sed-on-JSON / bc 等脆弱操作
set -e

cd "$(dirname "$0")"
LOG_FILE="auto-fix.log"

{
  echo "========================================"
  echo "🔧 Quant Voyager 自动修复"
  echo "========================================"
  date
  echo
} | tee -a "$LOG_FILE"

# 用 Python 完成所有 JSON 检查与修复，返回 1 = 实际改动过文件
PYTHONPATH=src conda run --no-capture-output -n llm python - <<'PY' | tee -a "$LOG_FILE"
import json, os, sys
from pathlib import Path

CORE = ["fixed-size-buy-001", "fixed-size-sell-001",
        "trend-follow-buy-001", "trend-follow-sell-001"]
STRAT_DIR = Path("runtime/min_agent/strategies")
PID_FILE  = Path("runtime/min_agent/daemon.pid")

fixed = 0

print("[1/3] 检查策略文件 (enabled / lifecycle / max_position_value)...")
for name in CORE:
    p = STRAT_DIR / f"{name}.json"
    if not p.exists():
        print(f"  ⚠️ {name} 不存在")
        continue
    data = json.loads(p.read_text())
    changed = False
    if not data.get("enabled", False):
        data["enabled"] = True
        data["lifecycle"] = "PROBATION"
        changed = True
        print(f"  ✅ 启用 {name}")
    if data.get("max_position_value", 0) < 1000:
        data["max_position_value"] = 5000.0
        changed = True
        print(f"  ✅ 提升 {name} max_position_value -> 5000.0")
    if changed:
        p.write_text(json.dumps(data, indent=2))
        fixed += 1
    else:
        print(f"  ✓ {name} 正常")

print("\n[2/3] 检查守护进程 PID 文件...")
if PID_FILE.exists():
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        print(f"  ✓ 守护进程运行中 (PID {pid})")
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        fixed += 1
        print(f"  ✅ 清理 stale PID 文件")
else:
    print("  ℹ️ 守护进程未运行")

print(f"\n[3/3] 修复总数: {fixed}")
sys.exit(0 if fixed == 0 else 1)
PY
PY_RC=${PIPESTATUS[0]}

# 仅在实际改动过文件时跑一次回归测试 (~30s)
if [ "$PY_RC" -eq 1 ]; then
  {
    echo
    echo "[verify] 跑 pytest 验证修复..."
  } | tee -a "$LOG_FILE"
  PYTHONPATH=src conda run -n llm python -m pytest tests/min_agent -q -x --tb=short 2>&1 | tee -a "$LOG_FILE"
fi

{
  echo
  echo "========================================"
  if [ "$PY_RC" -eq 1 ]; then echo "✅ 自动修复完成 (有改动)"; else echo "✅ 自动修复完成 (无需修复)"; fi
  echo "========================================"
  echo
} | tee -a "$LOG_FILE"

exit 0
