#!/usr/bin/env python3
import alpaca_trade_api as tradeapi
import json
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from typing import Optional, Dict, Any

from min_agent.config import AgentConfig
from min_agent.journal import JsonlJournal
from min_agent.broker_evidence import BrokerEvidenceProvider


def main():
    cfg = AgentConfig.from_env()
    journal = JsonlJournal(cfg.journal_path)

    # Get client
    client = tradeapi.REST(
        cfg.alpaca_api_key,
        cfg.alpaca_secret_key,
        cfg.alpaca_base_url,
    )

    # Get a much larger window - last 30 days
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=30)

    print(f'Window start: {window_start}')
    print(f'Window end: {window_end}')

    # Ingest broker evidence
    print("\n=== Ingesting broker evidence ===")
    provider = BrokerEvidenceProvider(client=client)
    evidence = provider.ingest(window_start=window_start, window_end=window_end)
    print(f'Orders: {len(evidence.orders)}')
    print(f'Activities: {len(evidence.activities)}')

    # Print all orders with their IDs
    print("\n=== Broker Orders ===")
    for i, order in enumerate(evidence.orders):
        print(f'{i+1}. order_id={order.order_id}')
        print(f'   client_order_id={order.client_order_id}')
        print(f'   symbol={order.symbol}')
        print(f'   side={order.side}')
        print(f'   status={order.status}')
        print(f'   filled_quantity={order.filled_quantity}')
        print(f'   filled_avg_price={order.filled_avg_price}')
        print(f'   filled_at={order.filled_at}')

    # Print all activities
    print("\n=== Broker Activities ===")
    for i, activity in enumerate(evidence.activities):
        print(f'{i+1}. activity_id={activity.activity_id}')
        print(f'   order_id={activity.order_id}')
        print(f'   symbol={activity.symbol}')
        print(f'   side={activity.side}')
        print(f'   quantity={activity.quantity}')
        print(f'   price={activity.price}')

    # Read journal cycles to get our order IDs
    print("\n=== Journal Cycles ===")
    cycles = journal.read_since(window_start - timedelta(days=5))
    print(f'Found {len(cycles)} cycles')

    print("\n=== Journal Submitted Orders ===")
    journal_order_ids = []
    journal_client_order_ids = []
    for cycle in cycles:
        if cycle.execution and cycle.execution.status == 'SUBMITTED' and cycle.execution.order_id:
            print(f'cycle={cycle.cycle_id}')
            print(f'  strategy_id={cycle.strategy_id or cycle.decision.strategy_id}')
            print(f'  order_id={cycle.execution.order_id}')
            print(f'  client_order_id={cycle.execution.client_order_id}')
            journal_order_ids.append(cycle.execution.order_id)
            if cycle.execution.client_order_id:
                journal_client_order_ids.append(cycle.execution.client_order_id)

    print(f'\nJournal order_ids: {journal_order_ids}')
    print(f'Journal client_order_ids: {journal_client_order_ids}')

    # Now check matches
    print("\n=== Checking Matches ===")
    matched_by_order_id = 0
    matched_by_client_order_id = 0
    for order in evidence.orders:
        if order.order_id in journal_order_ids:
            print(f'MATCH by order_id: {order.order_id}')
            matched_by_order_id += 1
        if order.client_order_id in journal_client_order_ids:
            print(f'MATCH by client_order_id: {order.client_order_id}')
            matched_by_client_order_id += 1

    print(f'\nTotal matched by order_id: {matched_by_order_id}')
    print(f'Total matched by client_order_id: {matched_by_client_order_id}')


if __name__ == '__main__':
    main()
