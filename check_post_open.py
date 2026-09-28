#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from collections import Counter

from min_agent.config import AgentConfig
from min_agent.journal import JsonlJournal
from min_agent.strategy_engine import StrategyLibrary
from min_agent.knowledge_library import KnowledgeLibrary


def main():
    cfg = AgentConfig.from_env()
    journal = JsonlJournal(cfg.journal_path)
    strategies = StrategyLibrary(cfg.strategy_dir).list()
    knowledge = KnowledgeLibrary(cfg.knowledge_dir).list()

    print("=== 开盘后状态审查 ===\n")

    # === 1. 检查 Daemon 状态 ===
    print("[1/8] 检查最近的 journal 记录...")
    records = journal.last_n(100)
    if records:
        last_record = records[-1]
        print(f"  最新周期: {last_record.cycle_id}")
        print(f"  最新时间: {last_record.snapshot.timestamp}")
        print(f"  市场状态: {'开盘' if last_record.snapshot.market_open else '收盘'}")
        print(f"  最新价格: {last_record.snapshot.last_price}")
        print(f"  决策: {last_record.decision.action} {last_record.decision.quantity}")
        print(f"  执行: {last_record.execution.status}")
        print(f"  策略: {last_record.strategy_id or last_record.decision.strategy_id}")

    # === 2. 检查最近事件 ===
    print("\n[2/8] 检查最近的事件...")
    events = list(reversed(journal.read_events(limit=50)))
    event_counts = Counter(e.event_type for e in events)
    for event_type, count in sorted(event_counts.items()):
        print(f"  {event_type:30} {count:3} 次")

    # === 3. 检查策略库 ===
    print("\n[3/8] 检查策略库...")
    print(f"  策略总数: {len(strategies)}")
    lifecycle_counts = Counter(s.lifecycle for s in strategies)
    for lifecycle, count in sorted(lifecycle_counts.items()):
        print(f"    {lifecycle:10} {count:2} 个")
    print("  策略列表:")
    for s in strategies:
        enabled = "✅" if s.enabled else "❌"
        print(f"    {enabled} {s.strategy_id:30} {s.kind:15} {s.lifecycle:10}")

    # === 4. 检查知识库 ===
    print("\n[4/8] 检查知识库...")
    print(f"  知识总数: {len(knowledge)}")
    if knowledge:
        print("  最近知识:")
        for k in knowledge[-5:]:
            print(f"    {k.artifact_id:30} {k.artifact_type:15} {k.status:10}")

    # === 5. 检查最近 100 周期活动 ===
    print("\n[5/8] 检查最近 100 周期...")
    actions = Counter(r.decision.action for r in records)
    statuses = Counter(r.execution.status for r in records)
    strategy_counts = Counter((r.strategy_id or r.decision.strategy_id or 'none') for r in records)
    market_open_cycles = sum(1 for r in records if r.snapshot.market_open)

    buy_sell_intent = actions.get('BUY', 0) + actions.get('SELL', 0)
    no_exploration = bool(records) and market_open_cycles > 0 and buy_sell_intent == 0 and (
        statuses.get('SUBMITTED', 0) + statuses.get('REJECTED', 0) == 0
    )

    print(f"  市场开盘周期: {market_open_cycles}")
    print(f"  动作: {actions}")
    print(f"  执行: {statuses}")
    print(f"  无探索: {no_exploration}")
    print(f"  策略使用:")
    for s, c in strategy_counts.most_common():
        print(f"    {s:30} {c:3} 次")

    # === 6. 检查关键学习事件 ===
    print("\n[6/8] 检查关键学习事件...")
    for e in events[:15]:
        if e.event_type in {'CURRICULUM_PROPOSED', 'REFLECTION_GENERATED', 'STRATEGY_EVALUATION_RECORDED', 'PNL_EVIDENCE_RECORDED', 'BROKER_EVIDENCE_INGESTED'}:
            print(f"  {e.event_type:30} {e.status:10} {e.timestamp}")
            if e.message:
                print(f"    Message: {e.message[:150]}")

    # === 7. 检查最新 Broker Evidence ===
    print("\n[7/8] 检查最新 Broker Evidence...")
    latest_broker = None
    for e in events:
        if e.event_type == 'BROKER_EVIDENCE_INGESTED':
            latest_broker = e
            break
    if latest_broker and latest_broker.payload:
        print(f"  时间: {latest_broker.timestamp}")
        print(f"  订单数: {len(latest_broker.payload.get('orders', []))}")
        print(f"  活动数: {len(latest_broker.payload.get('activities', []))}")
        print(f"  组合历史: {len(latest_broker.payload.get('portfolio_history', []))}")

    # === 8. 检查最新 PnL ===
    print("\n[8/8] 检查最新 PnL Evidence...")
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
            print(f"  Order derived: {pnl.get('order_derived_fill_count')}")
            print(f"  Open lots: {pnl.get('open_lot_quantity')}")

    print("\n=== 审查完成 ===")


if __name__ == '__main__':
    main()
