#!/usr/bin/env python3
import json
import sys
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

    records = journal.last_n(100)
    events = journal.read_events(limit=50)

    actions = Counter(r.decision.action for r in records)
    statuses = Counter(r.execution.status for r in records)
    strategy_counts = Counter((r.strategy_id or r.decision.strategy_id or 'none') for r in records)
    market_open_cycles = sum(1 for r in records if r.snapshot.market_open)

    buy_sell_intent = actions.get('BUY', 0) + actions.get('SELL', 0)
    intended_notional = sum(
        float(r.decision.quantity or 0) * float(r.snapshot.last_price or 0)
        for r in records
    )
    no_exploration = bool(records) and market_open_cycles > 0 and buy_sell_intent == 0 and (
        statuses.get('SUBMITTED', 0) + statuses.get('REJECTED', 0) == 0
    )

    latest_pnl = None
    latest_broker = None
    for e in reversed(events):
        if e.event_type in {'PNL_EVIDENCE_RECORDED', 'PNL_EVIDENCE_FAILED'}:
            latest_pnl = e
        if e.event_type == 'BROKER_EVIDENCE_INGESTED':
            latest_broker = e
        if latest_pnl and latest_broker:
            break

    out = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'daemon_status': 'RUNNING',  # We'll check separately
        'strategy_count': len(strategies),
        'strategies': [
            {
                'id': s.strategy_id,
                'kind': s.kind,
                'lifecycle': s.lifecycle,
                'enabled': s.enabled
            }
            for s in strategies
        ],
        'knowledge_count': len(knowledge),
        'knowledge_tail': [
            {'id': k.artifact_id, 'type': k.artifact_type, 'status': k.status, 'tags': k.tags}
            for k in knowledge[-10:]
        ],
        'recent_cycles': len(records),
        'market_open_cycles': market_open_cycles,
        'actions': dict(actions),
        'execution_statuses': dict(statuses),
        'strategy_counts': dict(strategy_counts),
        'no_exploration': no_exploration,
        'latest_pnl_audit': None if not latest_pnl or not latest_pnl.payload else {
            'status': latest_pnl.payload.get('status'),
            'account_return_pct': latest_pnl.payload.get('pnl', {}).get('account_return_pct'),
            'strategy_realized_pnl': latest_pnl.payload.get('pnl', {}).get('strategy_realized_pnl'),
            'linked_fill_count': latest_pnl.payload.get('pnl', {}).get('linked_fill_count'),
            'unlinked_fill_count': latest_pnl.payload.get('pnl', {}).get('unlinked_fill_count'),
            'order_derived_fill_count': latest_pnl.payload.get('pnl', {}).get('order_derived_fill_count'),
            'closed_lot_count': latest_pnl.payload.get('pnl', {}).get('closed_lot_count'),
            'open_lot_quantity': latest_pnl.payload.get('pnl', {}).get('open_lot_quantity'),
        },
    }

    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
