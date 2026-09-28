#!/bin/bash
# 市场开盘检查：用 Alpaca clock，输出 JSON 到 runtime/min_agent/market-state.json
set -e
cd "$(dirname "$0")"

LOG_FILE="market-check.log"
{
  echo "========================================"
  echo "📈 市场开盘检查"
  echo "========================================"
  date
  echo
} | tee -a "$LOG_FILE"

PYTHONPATH=src conda run --no-capture-output -n llm python - <<'PY' | tee -a "$LOG_FILE"
import json, sys
from pathlib import Path

sys.path.insert(0, "src")
from min_agent.config import AgentConfig

try:
    import alpaca_trade_api as tradeapi
except ImportError:
    print("alpaca_trade_api not available")
    sys.exit(1)

cfg = AgentConfig.from_env()
# tradeapi.REST 会自动追加 api_version，所以剥掉 base_url 末尾可能带的 /v2
base = cfg.alpaca_base_url.rstrip("/")
if base.endswith("/v2"):
    base = base[:-3]
client = tradeapi.REST(
    key_id=cfg.alpaca_api_key,
    secret_key=cfg.alpaca_secret_key,
    base_url=base,
    api_version="v2",
)
clock = client.get_clock()

state = {
    "is_open": bool(clock.is_open),
    "timestamp": str(clock.timestamp),
    "next_open": str(clock.next_open),
    "next_close": str(clock.next_close),
}
print(json.dumps(state, indent=2))
print(f"\n当前市场状态: {'✅ 开盘' if state['is_open'] else '❌ 收盘'}")

Path("runtime/min_agent/market-state.json").write_text(json.dumps(state, indent=2))
PY
