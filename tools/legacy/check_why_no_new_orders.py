
#!/usr/bin/env python3
"""
检查为什么没有近期订单
"""
from min_agent.config import AgentConfig
from min_agent.journal import JsonlJournal
from min_agent.strategy_engine import StrategyLibrary
from datetime import datetime, timezone, timedelta
from collections import Counter

cfg = AgentConfig.from_env()
journal = JsonlJournal(cfg.journal_path)

print('='*70)
print('🔍 检查为什么没有近期订单')
print('='*70)
print(f'🕐 当前时间: {datetime.now(timezone.utc)}')

print('\n[1/6] 最近100个周期')
records = journal.last_n(100)
print(f'  总周期数: {len(records)}')
if records:
    print(f'  最新周期时间: {records[-1].snapshot.timestamp}')
    print(f'  市场最新状态: {"开盘" if records[-1].snapshot.market_open else "收盘"}')

print('\n[2/6] 最近事件统计')
events = list(reversed(journal.read_events(limit=50)))
event_counts = Counter(e.event_type for e in events)
for evt, cnt in sorted(event_counts.items()):
    print(f'  {evt:35} {cnt:3} 次')

print('\n[3/6] 最近50个周期的决策')
actions = Counter(r.decision.action for r in records)
print(f'  {actions}')

exec_statuses = Counter(r.execution.status for r in records)
print(f'  {exec_statuses}')

print('\n[4/6] 策略库状态')
strategies = StrategyLibrary(cfg.strategy_dir).list()
print(f'  策略总数: {len(strategies)}')
for s in sorted(strategies, key=lambda x: x.lifecycle):
    enabled = "✅" if s.enabled else "❌"
    print(f'    {enabled} {s.strategy_id:35} {s.kind:15} {s.lifecycle:10}')

print('\n[5/6] tiny-fixed-size-001状态')
for s in strategies:
    if s.strategy_id == 'tiny-fixed-size-001':
        print(f'  Strategy ID: {s.strategy_id}')
        print(f'  Enabled: {s.enabled}')
        print(f'  Lifecycle: {s.lifecycle}')
        print(f'  Kind: {s.kind}')
        if s.parameters:
            print(f'  Parameters: {s.parameters}')

print('\n[6/6] 最后一个提交订单的时间')
submitted_records = [r for r in records if r.execution.status == 'SUBMITTED']
if submitted_records:
    last_submitted = submitted_records[-1]
    print(f'  最后提交: {last_submitted.snapshot.timestamp}')
    print(f'  策略: {last_submitted.strategy_id or last_submitted.decision.strategy_id}')
    print(f'  操作: {last_submitted.decision.action} {last_submitted.decision.quantity}')
    print(f'  距现在: {datetime.now(timezone.utc) - last_submitted.snapshot.timestamp}')
else:
    print('  近期没有提交订单')

print('\n' + '='*70)
print('📋 分析结果')
print('='*70)

