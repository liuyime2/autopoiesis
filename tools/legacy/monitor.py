#!/usr/bin/env python3
"""
系统监控脚本 - 每小时运行，检查学习进度和系统状态
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from min_agent.config import AgentConfig
from min_agent.journal import JsonlJournal
from min_agent.strategy_engine import StrategyLibrary
from min_agent.knowledge_library import KnowledgeLibrary


def main():
    cfg = AgentConfig.from_env()
    journal = JsonlJournal(cfg.journal_path)
    strategies = StrategyLibrary(cfg.strategy_dir).list()
    knowledge = KnowledgeLibrary(cfg.knowledge_dir).list()

    print("=" * 60)
    print("📊 Quant Voyager 系统监控")
    print("=" * 60)
    print(f"🕐 时间: {datetime.now(timezone.utc).isoformat()}")

    # 检查最近记录
    print("\n[1/7] 最近周期状态")
    records = journal.last_n(10)
    if records:
        last = records[-1]
        print(f"  最新周期: {last.cycle_id}")
        print(f"  时间: {last.snapshot.timestamp}")
        print(f"  市场: {'开盘' if last.snapshot.market_open else '收盘'}")
        print(f"  价格: {last.snapshot.last_price}")
        print(f"  决策: {last.decision.action} {last.decision.quantity}")
        print(f"  执行: {last.execution.status}")
        print(f"  策略: {last.strategy_id or last.decision.strategy_id}")

    # 最近事件
    print("\n[2/7] 最近事件")
    events = list(reversed(journal.read_events(limit=20)))
    event_counts = {}
    for e in events:
        key = e.event_type
        event_counts[key] = event_counts.get(key, 0) + 1
    for evt, cnt in sorted(event_counts.items()):
        print(f"  {evt:30} {cnt:3} 次")

    # 策略库
    print("\n[3/7] 策略库")
    lifecycle_counts = {}
    for s in strategies:
        lifecycle_counts[s.lifecycle] = lifecycle_counts.get(s.lifecycle, 0) + 1
    for lc, cnt in sorted(lifecycle_counts.items()):
        print(f"  {lc:10} {cnt:2} 个")
    print("  策略列表:")
    for s in strategies[:5]:
        enabled = "✅" if s.enabled else "❌"
        print(f"    {enabled} {s.strategy_id:30} {s.kind:15} {s.lifecycle:10}")
    if len(strategies) > 5:
        print(f"    ... 还有 {len(strategies) - 5} 个策略")

    # 知识库
    print("\n[4/7] 知识库")
    print(f"  知识总数: {len(knowledge)}")
    if knowledge:
        type_counts = {}
        for k in knowledge:
            type_counts[k.artifact_type] = type_counts.get(k.artifact_type, 0) + 1
        for kt, cnt in sorted(type_counts.items()):
            print(f"  {kt:15} {cnt:2} 个")
        print("  最近知识:")
        for k in knowledge[-3:]:
            print(f"    {k.artifact_id:30} {k.artifact_type:15} {k.status:10}")

    # 最近活动分析
    print("\n[5/7] 最近100周期分析")
    records_100 = journal.last_n(100)
    market_open = sum(1 for r in records_100 if r.snapshot.market_open)
    print(f"  市场开盘周期: {market_open}")
    if records_100:
        from collections import Counter
        actions = Counter(r.decision.action for r in records_100)
        statuses = Counter(r.execution.status for r in records_100)
        print(f"  动作: {actions}")
        print(f"  执行: {statuses}")

        no_exploration = (market_open > 0 and
                         actions.get('BUY', 0) + actions.get('SELL', 0) == 0)
        print(f"  无探索: {no_exploration}")

    # 关键学习事件
    print("\n[6/7] 关键学习事件")
    learning_events = [
        e for e in events
        if e.event_type in {'CURRICULUM_PROPOSED', 'REFLECTION_GENERATED',
                          'STRATEGY_EVALUATION_RECORDED', 'PNL_EVIDENCE_RECORDED',
                          'KNOWLEDGE_ARTIFACT_PROPOSED', 'KNOWLEDGE_ARTIFACT_ADMISSION_REVIEWED'}
    ]
    for e in learning_events[:5]:
        print(f"  {e.event_type:30} {e.status:10} {e.timestamp}")
        if e.message:
            print(f"    {e.message[:80]}")

    # 最新 PnL
    print("\n[7/7] 最新 PnL 证据")
    latest_pnl = None
    for e in events:
        if e.event_type in {'PNL_EVIDENCE_RECORDED', 'PNL_EVIDENCE_FAILED'}:
            latest_pnl = e
            break
    if latest_pnl and latest_pnl.payload:
        pnl = latest_pnl.payload.get('pnl', {})
        if pnl:
            print(f"  状态: {pnl.get('status')}")
            print(f"  账户回报: {pnl.get('account_return_pct')}")
            print(f"  策略 PnL: {len(pnl.get('strategy_realized_pnl', {}))} 个策略")
            print(f"  Linked fills: {pnl.get('linked_fill_count')}")
            print(f"  Unlinked fills: {pnl.get('unlinked_fill_count')}")
            print(f"  Closed lots: {pnl.get('closed_lot_count')}")
            print(f"  Open lots: {pnl.get('open_lot_quantity')}")

    print("\n" + "=" * 60)
    print("监控完成")
    print("=" * 60)


if __name__ == '__main__':
    main()
