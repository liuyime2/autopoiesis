
#!/usr/bin/env python3
"""
直接连接真实Alpaca验证 - 100%真实，无Mock/Placeholder
"""
import alpaca_trade_api as tradeapi
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

print('='*70)
print('🔍 直接连接真实Alpaca验证 - 100%真实，无Mock/Placeholder')
print('='*70)
print(f'🕐 时间: {datetime.now(timezone.utc)}')

api_key = os.getenv('ALPACA_API_KEY') or os.getenv('APCA_API_KEY_ID')
secret_key = os.getenv('ALPACA_SECRET_KEY') or os.getenv('APCA_API_SECRET_KEY')
base_url = os.getenv('ALPACA_BASE_URL') or os.getenv('APCA_API_BASE_URL') or 'https://paper-api.alpaca.markets'

if base_url.endswith('/v2'):
    base_url = base_url[:-3]

print(f'\n[1/5] 连接配置')
print(f'  Base URL: {base_url}')
print(f'  API Key: (set, {len(api_key)} chars, not printed)')
print(f'  Secret Key: (set, {len(secret_key)} chars, not printed)')

print(f'\n[2/5] 连接Alpaca...')
client = tradeapi.REST(api_key, secret_key, base_url, api_version='v2')
print(f'  ✅ 连接成功!')

print(f'\n[3/5] 获取账户信息')
account = client.get_account()
print(f'  Account ID: {account.id}')
print(f'  Account Number: {account.account_number}')
print(f'  Status: {account.status}')
print(f'  Cash: ${float(account.cash):,.2f}')
print(f'  Portfolio Value: ${float(account.portfolio_value):,.2f}')
print(f'  Equity: ${float(account.equity):,.2f}')
print(f'  Last Equity: ${float(account.last_equity):,.2f}')
print(f'  Equity Change: ${float(account.equity) - float(account.last_equity):,.2f}')
print(f'  Buying Power: ${float(account.buying_power):,.2f}')

print(f'\n[4/5] 获取当前持仓')
positions = client.list_positions()
print(f'  持仓数: {len(positions)}')
for pos in positions:
    print(f'\n    Symbol: {pos.symbol}')
    print(f'    Qty: {pos.qty}')
    print(f'    Avg Entry Price: ${float(pos.avg_entry_price):,.2f}')
    print(f'    Current Price: ${float(pos.current_price):,.2f}')
    print(f'    Market Value: ${float(pos.market_value):,.2f}')
    print(f'    Unrealized P/L: ${float(pos.unrealized_pl):,.2f}')
    print(f'    Unrealized P/L %: {float(pos.unrealized_plpc)*100:,.2f}%')

print(f'\n[5/5] 获取历史订单')
orders = client.list_orders(status='all', limit=20)
print(f'  订单数: {len(orders)}')
for order in orders[:13]:
    print(f'\n    Order ID: {order.id}')
    print(f'    Client Order ID: {order.client_order_id}')
    print(f'    Symbol: {order.symbol}')
    print(f'    Side: {order.side}')
    print(f'    Qty: {order.qty}')
    print(f'    Filled Qty: {order.filled_qty}')
    if order.filled_avg_price:
        print(f'    Filled Avg Price: ${float(order.filled_avg_price):,.2f}')
    print(f'    Status: {order.status}')
    print(f'    Created At: {order.created_at}')
    if order.filled_at:
        print(f'    Filled At: {order.filled_at}')

print('\n' + '='*70)
print('✅ 验证完成 - 100%真实Alpaca，无Mock/Placeholder!')
print('='*70)

