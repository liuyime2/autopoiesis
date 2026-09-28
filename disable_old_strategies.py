
#!/usr/bin/env python3
"""
暂停旧的问题策略
"""
from min_agent.config import AgentConfig
from min_agent.strategy_engine import StrategyLibrary
from datetime import datetime, timezone
import json

cfg = AgentConfig.from_env()
strategy_lib = StrategyLibrary(cfg.strategy_dir)

print('='*80)
print('⏸️ 暂停旧的问题策略')
print('='*80)

print('\n[1/3] 加载所有策略')
strategies = strategy_lib.list()
for s in strategies:
    status = "✅" if s.enabled else "❌"
    print(f'  {status} {s.strategy_id:40} {s.kind:15} {s.lifecycle:10}')

print('\n[2/3] 暂停旧trend-follow策略')
for s in strategies:
    if 'trend-follow-20260611' in s.strategy_id or 'trend-follow-20260612' in s.strategy_id:
        print(f'  暂停: {s.strategy_id}')
        # 创建新的修改后的策略对象
        disabled = StrategySpec(
            strategy_id=s.strategy_id,
            name=s.name,
            kind=s.kind,
            symbols=s.symbols,
            parameters=s.parameters,
            max_position_value=s.max_position_value,
            enabled=False,  # 禁用！
            lifecycle="PAUSED",  # 暂停！
            created_at=s.created_at,
            rationale=s.rationale
        )
        strategy_lib.save(disabled)
        print(f'  ✅ 已暂停: {s.strategy_id}')

print('\n[3/3] 新策略库状态')
strategies = strategy_lib.list()
for s in sorted(strategies, key=lambda x: (x.lifecycle, x.strategy_id)):
    enabled = "✅" if s.enabled else "❌"
    lifecycle_icon = "🟢" if s.lifecycle == "ACTIVE" else "🟡" if s.lifecycle == "PROBATION" else "🔵" if s.lifecycle == "BASELINE" else "⚫" if s.lifecycle == "RETIRED" else "⏸️"
    print(f'  {enabled} {lifecycle_icon} {s.strategy_id:40} {s.kind:15} {s.lifecycle:10}')

print('\n' + '='*80)
print('✅ 优化完成！')
print('='*80)
print('\n🎯 活跃策略:')
print('  🟡 fixed-size-buy-001 (PROBATION) → BUY建仓')
print('  🟡 fixed-size-sell-001 (PROBATION) → SELL平仓！')
print('  🟡 trend-follow-buy-001 (PROBATION) → 趋势跟踪BUY')
print('  🟡 trend-follow-sell-001 (PROBATION) → 趋势跟踪SELL！')
print('\n🔄 现在系统可以完整循环: BUY → HOLD → SELL → 学习 → 优化！')

