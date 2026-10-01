from datetime import datetime, timezone

from min_agent.journal import JsonlJournal
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)
from min_agent.trade_counter import TradeCounter


def record(*, cycle_id, timestamp, action="BUY", status="SUBMITTED"):
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=DataSnapshot(
            symbol="SPY",
            timestamp=timestamp,
            market_open=True,
            last_price=100,
            source="alpaca",
            account=AccountSnapshot(equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0),
        ),
        decision=TradeDecision(symbol="SPY", action=action, quantity=1 if action != "HOLD" else 0, confidence=0.8, rationale="test"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status=status, order_id="oid" if status == "SUBMITTED" else None, filled_quantity=0, message="test"),
    )


def test_trade_counter_counts_today_submitted_non_hold_orders(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(record(cycle_id="today-buy", timestamp=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)))
    journal.append(record(cycle_id="today-hold", timestamp=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc), action="HOLD", status="SKIPPED"))
    journal.append(record(cycle_id="old-buy", timestamp=datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)))
    counter = TradeCounter(journal=journal)

    assert counter.trades_today(now=datetime(2026, 6, 10, 20, 0, tzinfo=timezone.utc)) == 1
