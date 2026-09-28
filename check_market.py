#!/usr/bin/env python3
from datetime import datetime, timezone
from min_agent.config import AgentConfig
import alpaca_trade_api as tradeapi

cfg = AgentConfig.from_env()

# Get market status from Alpaca directly
try:
    client = tradeapi.REST(
        key_id=cfg.alpaca_api_key,
        secret_key=cfg.alpaca_secret_key,
        base_url=cfg.alpaca_base_url,
        api_version="v2",
    )
    clock = client.get_clock()

    print(f"当前 UTC 时间: {datetime.now(timezone.utc)}")
    print(f"市场状态: {'开盘' if clock.is_open else '收盘'}")
    print(f"下次开盘 UTC: {clock.next_open}")
    print(f"下次收盘 UTC: {clock.next_close}")

    if not clock.is_open:
        now = datetime.now(timezone.utc)
        next_open = clock.next_open
        if next_open.tzinfo is None:
            next_open = next_open.replace(tzinfo=timezone.utc)
        delta = next_open - now
        print(f"距离下次开盘: {delta}")

except Exception as e:
    print(f"获取市场状态失败: {e}")
