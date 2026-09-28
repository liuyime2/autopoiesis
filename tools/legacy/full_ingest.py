#!/usr/bin/env python3
import alpaca_trade_api as tradeapi
import os
from datetime import datetime, timedelta, timezone
from min_agent.config import AgentConfig
from min_agent.models import BrokerEvidenceBatch
from min_agent.broker_evidence import BrokerEvidenceProvider
from min_agent.journal import JsonlJournal
import json

cfg = AgentConfig.from_env()

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

provider = BrokerEvidenceProvider(client=client)
print(f'Ingesting broker evidence...')
evidence_batch = provider.ingest(window_start=window_start, window_end=window_end)
print(f'Ingestion complete!')
print(f'  Status: {evidence_batch.status}')
print(f'  Orders: {len(evidence_batch.orders)}')
print(f'  Activities: {len(evidence_batch.activities)}')
print(f'  Portfolio history: {len(evidence_batch.portfolio_history)}')
print(f'  Missing reasons: {evidence_batch.missing_reasons}')

# Print first few orders with journal IDs
journal = JsonlJournal(cfg.journal_path)
print(f'\nChecking submitted order IDs...')
submitted_order_ids = {
    '9ca74a4e-8ed6-4dd7-bab8-b104d7b5523f',
    '5d4ec92c-870e-499f-89d1-60145b7dfadd',
    'f4e4bc27-2b6b-4aa6-b70e-4453e3e35d8f',
    'edd07c07-8efa-4561-8c0a-d18977a7709e',
    '78316ce9-4511-4cdc-a9e9-f571b0ba0ed6',
    'e1c80905-9b5f-40a6-b135-c78c483fe73b',
    'e833b92c-269e-4a46-bae1-179daf8bea1d',
    'dbf52e71-2d33-48ee-bafb-9133ffb30ec7',
    'dbb16ce2-0bab-4956-bb0-e9ef146157c9',
    '3c82472f-a9ae-4bc0-b864-a6ae8077a781',
    '19591da6-9227-4f22-b389-c5f9cead4e4b',
    '9182ee7a-9a8d-40ea-af79-e434fd926175',
    '838806b2-b6b2-4346-834f-b7aa2ab4d850',
}

found_count = 0
for order in evidence_batch.orders:
    if order.order_id in submitted_order_ids:
        print(f'✅ Found submitted order: {order.order_id} - {order.symbol} - {order.side} - {order.filled_qty} filled at {order.filled_avg_price}')
        found_count += 1

print(f'Total found: {found_count}/{len(submitted_order_ids)}')

# Save to journal
journal.append_event(
    type='BROKER_EVIDENCE_INGESTED',
    status='SUCCESS',
    message='Broker evidence ingested',
    payload=evidence_batch,
    window_start=window_start,
    window_end=window_end,
)
print(f'\nSaved broker evidence to journal!')

print(f'\nNow running evaluator...')
