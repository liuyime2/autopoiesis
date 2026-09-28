
#!/usr/bin/env python3
"""
完整修复和优化策略系统
"""
from min_agent.config import AgentConfig
from min_agent.strategy_engine import StrategyLibrary, StrategySpec
from min_agent.models import DataSnapshot
from datetime import datetime, timezone
import alpaca_trade_api as tradeapi
import json

cfg = AgentConfig.from_env()
strategy_lib = StrategyLibrary(cfg.strategy_dir)

print('='*80)
print('🔧 完整修复和优化策略系统')
print('='*80)
print(f'🕐 时间: {datetime.now(timezone.utc)}')

print('\n[1/7] 连接Alpaca获取当前价格')
client = tradeapi.REST(
    cfg.alpaca_api_key,
    cfg.alpaca_secret_key,
    cfg.alpaca_base_url,
)
trade = client.get_latest_trade('SPY')
current_price = float(trade.price)
print(f'  当前SPY价格: ${current_price:.2f}')

print('\n[2/7] 当前策略库状态')
strategies = strategy_lib.list()
for s in strategies:
    status = "✅" if s.enabled else "❌"
    print(f'  {status} {s.strategy_id:35} {s.kind:15} {s.lifecycle:10}')

print('\n[3/7] 问题分析')
print('  1. trend-follow策略的max_position_value只有$1.0 → 太小！')
print('  2. trend-follow策略的reference_price是$100.0 → 不符合当前市场！')
print('  3. 只有BUY没有SELL → 无法平仓！')
print('  4. tiny-fixed-size-001已RETIRED → 没有简单策略了！')

print('\n[4/7] 备份现有问题策略')
problems = []
for s in strategies:
    if 'trend-follow' in s.strategy_id:
        print(f'  备份: {s.strategy_id}')
        backup = s.model_copy(deep=True)
        backup.strategy_id = f"{s.strategy_id}-backup"
        backup.enabled = False
        backup.lifecycle = "PAUSED"
        strategy_lib.save(backup)
        problems.append(s)

print('\n[5/7] 创建修复和优化后的策略')

FIXED_MAX_POSITION_VALUE = 5000.0  # $5k

# 策略1: 改进的Fixed-Size策略（有BUY和SELL）
fixed_size_buy = StrategySpec(
    strategy_id="fixed-size-buy-001",
    name="Fixed Size Buy Strategy",
    kind="FIXED_SIZE",
    symbols=["SPY"],
    parameters={
        "action": "BUY",
        "quantity": 1,
        "confidence": 0.6
    },
    max_position_value=FIXED_MAX_POSITION_VALUE,
    enabled=True,
    lifecycle="PROBATION",
    created_at=datetime.now(timezone.utc),
    rationale="Fixed size BUY strategy for exploration, with proper position limit."
)
strategy_lib.save(fixed_size_buy)
print(f'  ✅ Created: {fixed_size_buy.strategy_id}')

# 策略2: 改进的Fixed-Size SELL策略
fixed_size_sell = StrategySpec(
    strategy_id="fixed-size-sell-001",
    name="Fixed Size Sell Strategy",
    kind="FIXED_SIZE",
    symbols=["SPY"],
    parameters={
        "action": "SELL",
        "quantity": 1,
        "confidence": 0.6
    },
    max_position_value=FIXED_MAX_POSITION_VALUE,
    enabled=True,
    lifecycle="PROBATION",
    created_at=datetime.now(timezone.utc),
    rationale="Fixed size SELL strategy for closing positions, with proper position limit."
)
strategy_lib.save(fixed_size_sell)
print(f'  ✅ Created: {fixed_size_sell.strategy_id}')

# 策略3: 改进的Trend-Follow策略（合理参数）
reference_price = current_price
print(f'  使用参考价格: ${reference_price:.2f}')

trend_follow_v2 = StrategySpec(
    strategy_id="trend-follow-v2-001",
    name="Improved Trend Following Strategy",
    kind="TREND_FOLLOW",
    symbols=["SPY"],
    parameters={
        "reference_price": reference_price,
        "threshold_pct": 0.02,
        "quantity": 1,
        "confidence": 0.6
    },
    max_position_value=FIXED_MAX_POSITION_VALUE,
    enabled=True,
    lifecycle="PROBATION",
    created_at=datetime.now(timezone.utc),
    rationale=f"Improved trend-follow strategy with reference_price=${reference_price:.2f} and max_position_value=${FIXED_MAX_POSITION_VALUE:.2f}"
)
strategy_lib.save(trend_follow_v2)
print(f'  ✅ Created: {trend_follow_v2.strategy_id}')

# 策略4: 简化的Trend-Follow（更灵敏的阈值）
trend_follow_sensitive = StrategySpec(
    strategy_id="trend-follow-sensitive-001",
    name="Sensitive Trend Following Strategy",
    kind="TREND_FOLLOW",
    symbols=["SPY"],
    parameters={
        "reference_price": reference_price,
        "threshold_pct": 0.005,
        "quantity": 1,
        "confidence": 0.55
    },
    max_position_value=FIXED_MAX_POSITION_VALUE,
    enabled=True,
    lifecycle="PROBATION",
    created_at=datetime.now(timezone.utc),
    rationale=f"Sensitive trend-follow with 0.5% threshold for more frequent trading."
)
strategy_lib.save(trend_follow_sensitive)
print(f'  ✅ Created: {trend_follow_sensitive.strategy_id}')

print('\n[6/7] 禁用旧的问题策略')
for s in problems:
    s.enabled = False
    s.lifecycle = "PAUSED"
    strategy_lib.save(s)
    print(f'  ✅ Paused: {s.strategy_id}')

print('\n[7/7] 新策略库状态')
strategies = strategy_lib.list()
for s in sorted(strategies, key=lambda x: (x.lifecycle, x.strategy_id)):
    enabled = "✅" if s.enabled else "❌"
    lifecycle_icon = "🟢" if s.lifecycle == "ACTIVE" else "🟡" if s.lifecycle == "PROBATION" else "🔵" if s.lifecycle == "BASELINE" else "⚫" if s.lifecycle == "RETIRED" else "⏸️"
    print(f'  {enabled} {lifecycle_icon} {s.strategy_id:40} {s.kind:15} {s.lifecycle:10}')

print('\n' + '='*80)
print('✅ 完整修复和优化完成！')
print('='*80)
print('\n📋 修复内容:')
print('  1. max_position_value: $1.0 → $5000.0')
print('  2. reference_price: $100.0 → $' + f'{reference_price:.2f}')
print('  3. 新增 fixed-size-sell-001 → 提供SELL机制！')
print('  4. 新增 fixed-size-buy-001 → 改进的BUY策略')
print('  5. 新增 trend-follow-v2-001 → 合理参数的趋势策略')
print('  6. 新增 trend-follow-sensitive-001 → 更灵敏的0.5%阈值')
print('  7. 暂停旧的问题策略')
print('\n🔄 现在系统可以:')
print('  ✅ BUY建仓')
print('  ✅ SELL平仓')
print('  ✅ 合理的风险控制')
print('  ✅ 策略多样化')

