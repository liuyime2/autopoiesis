#!/usr/bin/env python
"""Read-only whole-account inventory, independent of stock quotes and models.

Financial output belongs in private runtime evidence. Account permissions and
broker tradability are observations, not proof of an implemented trading adapter.
"""
import sys

sys.path.insert(0, 'src')

from autopoiesis.broker_client import create_alpaca_client, list_open_orders
from autopoiesis.config import AgentConfig


def _observed(record, field):
    value = getattr(record, field, None)
    return "UNKNOWN" if value is None or value == "" else value


def main():
    config = AgentConfig.from_env()
    client = create_alpaca_client(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
    )
    print("Read-only Alpaca paper whole-account inventory")
    clock = client.get_clock()
    print("\nMarket clock:")
    for field in ("timestamp", "is_open", "next_open", "next_close"):
        print(f"  {field}: {_observed(clock, field)}")

    account = client.get_account()
    print("\nAccount:")
    for field in (
        "status", "equity", "cash", "buying_power", "non_marginable_buying_power",
        "options_buying_power", "crypto_status", "portfolio_value", "last_equity",
        "trading_blocked", "account_blocked", "trade_suspended_by_user",
        "pattern_day_trader", "shorting_enabled", "options_approved_level", "options_trading_level",
    ):
        print(f"  {field}: {_observed(account, field)}")

    positions = client.list_positions()
    if not isinstance(positions, list):
        print("Positions unavailable or invalid")
        return 1
    print(f"\nWhole-account positions: {len(positions)}")
    held = set()
    for position in positions:
        symbol = _observed(position, "symbol")
        held.add(str(symbol))
        print(f"  {symbol}:")
        for field in ("asset_class", "side", "qty", "avg_entry_price", "market_value", "unrealized_pl"):
            print(f"    {field}: {_observed(position, field)}")

    configured = set(config.symbols) | set(getattr(config, "allowlist", ()))
    print("\nAsset metadata for every held and configured symbol:")
    print("  us_equity: EQUITY_ADAPTER_AVAILABLE; Guardian, market and coverage checks still required")
    print("  options/crypto/unknown classes: UNSUPPORTED; no execution adapter")
    for symbol in sorted(held | configured):
        print(f"  {symbol} (held={symbol in held}, configured={symbol in configured}):")
        try:
            asset = client.get_asset(symbol)
        except Exception as exc:
            print(f"    metadata: UNKNOWN ({type(exc).__name__})")
            print("    implementation_support: UNSUPPORTED")
            continue
        for field in ("class", "name", "exchange", "status", "tradable", "marginable",
                      "shortable", "easy_to_borrow", "fractionable"):
            print(f"    {field}: {_observed(asset, field)}")
        support = "EQUITY_ADAPTER_AVAILABLE" if getattr(asset, "class", None) == "us_equity" else "UNSUPPORTED"
        print(f"    implementation_support: {support}")

    try:
        orders = list_open_orders(client)
    except RuntimeError as exc:
        print(f"\nOpen orders complete: False ({exc})")
        return 1
    print(f"\nOpen orders complete: True; count: {len(orders)}")
    for order in orders:
        print(f"  {order.side} {order.qty} {order.symbol} ({order.status})")
    print("\nRead-only inventory finished; trading readiness remains separately audited.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
