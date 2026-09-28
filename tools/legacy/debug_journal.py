#!/usr/bin/env python3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter

from min_agent.config import AgentConfig
from min_agent.journal import JsonlJournal


def main():
    cfg = AgentConfig.from_env()
    journal = JsonlJournal(cfg.journal_path)

    # Read last 200 cycles
    print("=== Last 200 Journal Cycles ===")
    cycles = journal.last_n(200)
    print(f'Got {len(cycles)} cycles')

    # Look for cycles with SUBMITTED status
    print("\n=== SUBMITTED Execution Results ===")
    submitted_count = 0
    for cycle in cycles:
        if cycle.execution and cycle.execution.status == 'SUBMITTED':
            print(f'cycle={cycle.cycle_id}')
            print(f'  timestamp={cycle.snapshot.timestamp}')
            print(f'  strategy_id={cycle.strategy_id or cycle.decision.strategy_id}')
            print(f'  execution.order_id={cycle.execution.order_id}')
            print(f'  execution.client_order_id={cycle.execution.client_order_id}')
            print(f'  decision.action={cycle.decision.action}')
            print(f'  decision.quantity={cycle.decision.quantity}')
            submitted_count += 1

    print(f'\nTotal SUBMITTED: {submitted_count}')

    # Check if the journal file exists
    print("\n=== Journal File ===")
    journal_path = Path(cfg.journal_path)
    print(f'Path: {journal_path}')
    print(f'Exists: {journal_path.exists()}')
    if journal_path.exists():
        print(f'Size: {journal_path.stat().st_size} bytes')


if __name__ == '__main__':
    main()
