#!/usr/bin/env python3
import alpaca_trade_api as tradeapi
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('ALPACA_API_KEY')
secret_key = os.getenv('ALPACA_SECRET_KEY')
base_url = os.getenv('ALPACA_BASE_URL')

print(f'Original ALPACA_BASE_URL: {base_url}')

# Fix the base URL if it has double /v2
if base_url and '/v2/v2' in base_url:
    base_url = base_url.replace('/v2/v2', '/v2')

# If it ends with /v2, remove it because Alpaca API adds it
if base_url and base_url.endswith('/v2'):
    base_url = base_url[:-3]

print(f'Fixed ALPACA_BASE_URL: {base_url}')

client = tradeapi.REST(api_key, secret_key, base_url)

# Get orders
print('\n=== Orders ===')
try:
    orders = client.list_orders(status='all')
    print(f'Orders count: {len(orders)}')
    for i, order in enumerate(orders):
        print(f'  {i+1}. {order.id} - {order.status} - {order.symbol} - {order.qty} - filled: {order.filled_qty} - avg: {order.filled_avg_price}')
except Exception as e:
    print(f'Error getting orders: {e}')

# Get activities
print('\n=== Activities (FILL) ===')
try:
    activities = client.get_activities(activity_types=['FILL'])
    print(f'Activities count: {len(activities)}')
    for i, activity in enumerate(activities):
        print(f'  {i+1}. {activity.id} - {activity.symbol} - {activity.side} - {activity.qty} - {activity.price} - {activity.transaction_time}')
except Exception as e:
    print(f'Error getting activities: {e}')

# Get all activities
print('\n=== All Activities ===')
try:
    activities = client.get_activities()
    print(f'Total activities count: {len(activities)}')
    for i, activity in enumerate(activities[:10]):
        print(f'  {i+1}. {activity.activity_type} - {activity.id} - {getattr(activity, "symbol", "N/A")}')
except Exception as e:
    print(f'Error getting all activities: {e}')

# Get portfolio history
print('\n=== Portfolio History ===')
try:
    hist = client.get_portfolio_history()
    print(f'History count: {len(hist.equity) if hist.equity else 0}')
    if hist.equity:
        print(f'Latest equity: {hist.equity[-1]}')
        print(f'Latest profit_loss: {hist.profit_loss[-1] if hist.profit_loss else "N/A"}')
        print(f'Latest profit_loss_pct: {hist.profit_loss_pct[-1] if hist.profit_loss_pct else "N/A"}')
except Exception as e:
    print(f'Error getting portfolio history: {e}')

# Get account
print('\n=== Account ===')
try:
    account = client.get_account()
    print(f'Equity: {account.equity}')
    print(f'Cash: {account.cash}')
    print(f'Portfolio value: {account.portfolio_value}')
    print(f'Buying power: {account.buying_power}')
    print(f'Last equity: {account.last_equity}')
    print(f'Equity change since yesterday: {float(account.equity) - float(account.last_equity)}')
except Exception as e:
    print(f'Error getting account: {e}')
