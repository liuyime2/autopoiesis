"""An agent sale beyond the agent's own lots, priced against the owner's recorded fills.

The 29 SPY shares sold on 2026-09-28 had no price on the record: the sale was real, the
cost basis was the account owner's. With the owner's fills ingested, those shares are
priced FIFO against the owner's lots as they stood at the sale - and reported as the owner's
exit, never as strategy PnL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis.evaluator import _apply_activity, _apply_owner_fill, _LotLedger
from autopoiesis.models import BrokerFillActivity

T0 = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)


def _fill(n, side, quantity, price, days=0):
    return BrokerFillActivity(
        activity_id=f"f{n}", order_id=f"o{n}", symbol="SPY", side=side, quantity=quantity,
        price=price, transaction_time=T0 + timedelta(days=days),
    )


def test_an_oversell_is_priced_against_the_owner_s_lots_not_booked_to_the_strategy():
    ledger = _LotLedger()
    _apply_owner_fill(ledger, _fill(1, "BUY", 20, 660.0))
    _apply_owner_fill(ledger, _fill(2, "BUY", 9, 680.0, 1))
    _apply_activity(ledger, _fill(3, "BUY", 23, 750.0, 2), strategy_id="s")
    _apply_activity(ledger, _fill(4, "SELL", 52, 766.57, 3), strategy_id="s")
    assert ledger.tracker("s").realized_pnl == pytest.approx(23 * (766.57 - 750.0))
    assert ledger.tracker("s").unmatched_sell_quantity == 0
    assert ledger.owner_exit_quantity == 29
    assert ledger.owner_exit_cost == pytest.approx(20 * 660.0 + 9 * 680.0)
    assert ledger.owner_exit_proceeds == pytest.approx(29 * 766.57)


def test_without_owner_history_the_shares_stay_unmatched():
    ledger = _LotLedger()
    _apply_activity(ledger, _fill(4, "SELL", 29, 766.57), strategy_id="s")
    assert ledger.owner_exit_quantity == 0
    assert ledger.tracker("s").unmatched_sell_quantity == 29


def test_the_owner_s_own_sales_reduce_their_book_first():
    ledger = _LotLedger()
    _apply_owner_fill(ledger, _fill(1, "BUY", 10, 600.0))
    _apply_owner_fill(ledger, _fill(2, "SELL", 4, 610.0, 1))
    _apply_activity(ledger, _fill(3, "SELL", 8, 700.0, 2), strategy_id="s")
    assert ledger.owner_exit_quantity == 6
    assert ledger.tracker("s").unmatched_sell_quantity == 2


def test_an_owner_history_ingest_is_not_mistaken_for_the_latest_evidence_batch(tmp_path):
    """It shares the event type; reading it as a window batch blanked every PnL figure."""
    from uuid import uuid4

    from autopoiesis.broker_evidence import latest_evidence_batch
    from autopoiesis.journal import JsonlJournal
    from autopoiesis.models import BrokerEvidenceBatch, JournalEvent

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    batch = BrokerEvidenceBatch(generated_at=T0, window_start=T0, window_end=T0, status="SUCCESS")
    for payload in (batch.model_dump(mode="json"), {"owner_fills": []}):
        journal.append_event(JournalEvent(
            event_id=str(uuid4()), event_type="BROKER_EVIDENCE_INGESTED", timestamp=T0,
            status="SUCCESS", message="m", payload=payload,
        ))
    assert latest_evidence_batch(journal) == batch
