"""The replay harness, and the four properties that keep it out of the PnL ledger.

A replayed fill leaking into the ledger would be the most dangerous bug available: the
account would show profit from money never risked, and an unvalidated path would look like
the best one on record. So the tests here are mostly about what must NOT happen, in the
style `test_shadow.py` established - assert the account says zero, not merely that some
component appears to ignore the value.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from min_agent.evaluator import DeterministicEvaluator
from min_agent.journal import JsonlJournal
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)
from min_agent.replay import (
    REPLAY_SOURCE,
    REPLAYED,
    ReplayBar,
    ReplayBook,
    ReplayDataGateway,
    ReplayExecutor,
    load_bars,
    save_bars,
)

BASE = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)


def bars(count: int = 4) -> list[ReplayBar]:
    """Rising real-shaped bars. Values are irrelevant; the properties are structural."""
    out = []
    for index in range(count):
        price = 766.0 + index
        out.append(
            ReplayBar(
                timestamp=BASE + timedelta(minutes=5 * index),
                open=price,
                high=price + 1,
                low=price - 0.5,
                close=price + 0.5,
                volume=1000,
            )
        )
    return out


def decision(action: str = "BUY", quantity: int = 2) -> TradeDecision:
    return TradeDecision(
        symbol="SPY",
        action=action,
        quantity=quantity,
        confidence=0.5,
        rationale="replay test",
    )


def cycle(result: ExecutionResult, action: str = "BUY", quantity: int = 2) -> CycleRecord:
    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=BASE,
        market_open=True,
        last_price=766.0,
        source=REPLAY_SOURCE,
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000,
            portfolio_value=100_000, daily_loss=0,
        ),
    )
    return CycleRecord(
        cycle_id="c1",
        snapshot=snapshot,
        decision=decision(action, quantity),
        guardian=GuardianResult(approved=True, reason="ok"),
        execution=result,
    )


# --------------------------------------------------------------------------
# the bars are real, and a replay says so
# --------------------------------------------------------------------------

def test_a_replay_snapshot_says_where_its_data_came_from():
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars(), book=book)

    snapshot = gateway.snapshot("SPY")

    assert snapshot.source == REPLAY_SOURCE
    assert snapshot.market_open is True
    assert snapshot.last_price == 766.5


def test_bars_round_trip_through_disk_so_a_replay_needs_no_network(tmp_path):
    original = bars(3)
    path = tmp_path / "bars.json"

    save_bars(path, "SPY", original)
    restored = load_bars(path)

    assert [b.close for b in restored] == [b.close for b in original]
    assert restored[0].timestamp == original[0].timestamp
    assert json.loads(path.read_text())["provenance"].startswith("alpaca")


def test_a_replay_over_zero_bars_is_refused():
    """A harness that silently replays nothing would pass every test that follows."""
    with pytest.raises(ValueError):
        ReplayDataGateway(bars=[], book=ReplayBook())


# --------------------------------------------------------------------------
# property 1 and 2: it cannot be read as a broker fill
# --------------------------------------------------------------------------

def test_a_replayed_fill_is_never_a_broker_order():
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars(), book=book)
    executor = ReplayExecutor(gateway=gateway, book=book)

    result = executor.execute(decision(), GuardianResult(approved=True, reason="ok"), cycle_id="c1")

    assert result.status == REPLAYED
    assert result.status not in {"SUBMITTED", "REJECTED"}
    assert result.order_id is None, "an order_id would let reconciliation match a real order"


def test_the_executed_set_the_evaluator_uses_excludes_replayed():
    """The safe default: a value nothing branches on is not counted as executed."""
    field = ExecutionResult.model_fields["status"].annotation
    values = set(field.__args__)

    assert REPLAYED in values
    assert "SUBMITTED" in values
    # shadow.py's value stays distinct too; they must not be the same string.
    assert REPLAYED != "SHADOWED"


def test_a_journal_of_replayed_fills_reports_zero_realized_pnl(tmp_path):
    """The property that actually matters: the account says zero money was made."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars(8), book=book)
    executor = ReplayExecutor(gateway=gateway, book=book)

    for index in range(4):
        side = "BUY" if index % 2 == 0 else "SELL"
        result = executor.execute(
            decision(side), GuardianResult(approved=True, reason="ok"), cycle_id=f"c{index}"
        )
        gateway.advance()
        journal.append(cycle(result, side))

    report = DeterministicEvaluator().evaluate(journal.read_all())
    pnl = report.pnl

    assert report.submitted_orders == 0, "replay must not book a submitted order"
    assert pnl.account_realized_or_reported_pnl is None, (
        f"replayed fills booked {pnl.account_realized_or_reported_pnl!r} of realized PnL; "
        "that is profit from money that was never risked"
    )
    assert pnl.account_return_pct is None
    assert pnl.strategy_realized_pnl == {}
    assert pnl.closed_lot_count == 0
    assert pnl.linked_fill_count == 0
    assert pnl.unattributed_pnl is None


def test_a_replay_never_reports_broker_verified_pnl(tmp_path):
    """Broker verification keys off `pnl_evidence`. A replay must never acquire it."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars(6), book=book)
    executor = ReplayExecutor(gateway=gateway, book=book)

    for index in range(3):
        side = "BUY" if index % 2 == 0 else "SELL"
        result = executor.execute(
            decision(side), GuardianResult(approved=True, reason="ok"), cycle_id=f"c{index}"
        )
        gateway.advance()
        journal.append(cycle(result, side))

    report = DeterministicEvaluator().evaluate(journal.read_all())

    assert report.pnl_evidence not in {
        "broker_strategy_closed_lot_pnl_verified",
        "broker_account_pnl_verified",
    }, f"replay claimed broker verification: {report.pnl_evidence!r}"


# --------------------------------------------------------------------------
# property 3 and 4: the real Guardian, and no look-ahead
# --------------------------------------------------------------------------

def test_the_real_guardian_still_refuses_in_a_replay():
    """A replay with a softer risk layer would test something nobody ships."""
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars(), book=book)
    executor = ReplayExecutor(gateway=gateway, book=book)

    result = executor.execute(
        decision(), GuardianResult(approved=False, reason="max trades per day reached"),
        cycle_id="c1",
    )

    assert result.status == "SKIPPED"
    assert result.filled_quantity == 0
    assert book.quantity == 0, "a refused decision must not move the book"


def test_a_fill_uses_the_next_bar_open_not_the_decision_bar_close():
    """Filling on the close of the bar the decision was made from is the classic way to
    manufacture a backtest: it assumes the close was knowable before it printed."""
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars(), book=book)
    executor = ReplayExecutor(gateway=gateway, book=book)

    decision_bar_close = gateway.snapshot("SPY").last_price
    result = executor.execute(decision(), GuardianResult(approved=True, reason="ok"), cycle_id="c1")

    assert result.filled_avg_price == gateway.next_open()
    assert result.filled_avg_price != decision_bar_close


def test_the_book_only_moves_on_a_filled_order():
    book = ReplayBook(starting_equity=100_000)
    gateway = ReplayDataGateway(bars=bars(), book=book)
    executor = ReplayExecutor(gateway=gateway, book=book)

    executor.execute(decision("HOLD", 0), GuardianResult(approved=True, reason="ok"), cycle_id="c1")
    assert book.quantity == 0
    assert book.cash == 100_000

    executor.execute(decision("BUY", 2), GuardianResult(approved=True, reason="ok"), cycle_id="c2")
    assert book.quantity == 2
    assert book.cash < 100_000


def test_the_simulated_account_moves_with_the_bars():
    """A book that ignored price would make a replay's equity meaningless."""
    book = ReplayBook(starting_equity=100_000)
    gateway = ReplayDataGateway(bars=bars(), book=book)

    executor = ReplayExecutor(gateway=gateway, book=book)
    executor.execute(decision("BUY", 2), GuardianResult(approved=True, reason="ok"), cycle_id="c1")

    marked = gateway.bars[-1].close
    assert book.equity(marked) != 100_000
    assert book.snapshot(marked)[0].equity == pytest.approx(book.equity(marked))


def test_the_gateway_tolerates_being_advanced_past_the_last_bar():
    """A caller may legitimately over-advance; asking for the time must still work.

    The daemon's sleep hook advances between cycles and it runs maintenance before it
    checks whether it has finished, so `current` was reached with the cursor one past the
    end. `snapshot` already clamped, which is why the same over-advance produced a valid
    last-bar snapshot and then raised IndexError the moment anything asked for the time.
    """
    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars(3), book=book)

    for _ in range(10):
        gateway.advance()

    assert gateway.exhausted
    assert gateway.current is gateway.bars[-1]
    assert gateway.snapshot("SPY").last_price == bars(3)[-1].close
