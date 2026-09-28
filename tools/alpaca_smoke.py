#!/usr/bin/env python
"""Test real Alpaca connection and market status."""
import os
import sys
sys.path.insert(0, 'src')

import alpaca_trade_api as tradeapi
from min_agent.config import AgentConfig
from min_agent.data_gateway import AlpacaDataGateway

def main():
    print("=" * 60)
    print("Testing Real Alpaca Connection & Market Status")
    print("=" * 60)

    config = AgentConfig.from_env()
    client = tradeapi.REST(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
        api_version="v2",
    )

    # Test 1: Get Clock (market open/closed status)
    print("\n[1/5] Getting Market Clock...")
    clock = client.get_clock()
    print(f"  Timestamp: {clock.timestamp}")
    print(f"  Market OPEN: {clock.is_open}")
    print(f"  Next Open: {clock.next_open}")
    print(f"  Next Close: {clock.next_close}")

    # Test 2: Get Account
    print("\n[2/5] Getting Account...")
    account = client.get_account()
    print(f"  Status: {account.status}")
    print(f"  Equity: ${account.equity}")
    print(f"  Cash: ${account.cash}")
    print(f"  Buying Power: ${account.buying_power}")
    print(f"  Portfolio Value: ${account.portfolio_value}")
    print(f"  Last Equity: ${account.last_equity}")

    # Test 3: Get Positions
    print("\n[3/5] Getting Positions...")
    positions = client.get_all_positions()
    print(f"  Position count: {len(positions)}")
    for pos in positions:
        print(f"    {pos.side} {pos.qty} {pos.symbol}")
        print(f"      Avg Entry: ${pos.avg_entry_price}")
        print(f"      Market Value: ${pos.market_value}")
        print(f"      Unrealized PnL: ${pos.unrealized_pl}")

    # Test 4: Get Recent Orders
    print("\n[4/5] Getting Recent Orders...")
    orders = client.list_orders(status='all', limit=10)
    print(f"  Order count: {len(orders)}")
    for order in orders:
        print(f"    {order.side} {order.qty} {order.symbol} @ {order.filled_avg_price} ({order.status})")
        print(f"      Submitted: {order.submitted_at}")
        if order.filled_at:
            print(f"      Filled: {order.filled_at}")

    # Test 5: Full Data Snapshot
    print("\n[5/5] Full Data Snapshot...")
    gateway = AlpacaDataGateway(client=client)
    snapshot = gateway.snapshot("SPY")
    print(f"  Symbol: {snapshot.symbol}")
    print(f"  Timestamp: {snapshot.timestamp}")
    print(f"  Market Open: {snapshot.market_open}")
    print(f"  Last Price: ${snapshot.last_price}")
    print(f"  Account Equity: ${snapshot.account.equity}")
    print(f"  Positions: {len(snapshot.positions)}")
    print(f"  Open Orders: {len(snapshot.open_orders)}")

    print("\n" + "=" * 60)
    print("✅ All Alpaca tests passed!")
    print("=" * 60)
    return 0

if __name__ == "__main__":
    sys.exit(main())
