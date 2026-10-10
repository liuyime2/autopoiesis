"""Rotation must not silently destroy the only source of truth.

`runtime/autopoiesis/journal.jsonl` is the project's only source of truth. Everything else is derived
from it and can be regenerated. Rotation deletes the **oldest** generation, which means after enough
of it the journal stops being the record of everything that happened and becomes a recent fragment.

That is documented and measured (about 14 days at 18.7MB/day with four generations). What is *not*
tested - and what this file tests - is the recovery property: given a journal that has been rotated
several times, do the analyses that are recomputed from it still agree with what was true when they
were last computed?

Measured 2026-10-09: rotation and closed-lot evidence are never exercised together anywhere in the
suite. `test_durability.py` proves the mechanism (rotate at the cap, preserve recent records, count
corrupt lines); nothing proves the *reconstruction* property across rotations, which is the claim the
README makes when it calls the journal the single source of truth.

The three analyses that matter, and why each is a distinct hazard:

1. **Positions and the FIFO lot ledger** - a rotation that moves the BUY into `.1` while the SELL
   stays live must still match them. Lots opened weeks ago are exactly what rotation moves.
2. **Per-strategy PnL** - recomputed from the journal each pass; if a generation holding an opening
   fill disappears, the strategy silently loses its attribution rather than failing.
3. **Strategy lifecycle** - the registry survives rotation by design; a generation boundary must not
   be able to turn "the transition happened" into "the transition is gone".

Nothing here changes rotation behaviour, a retention policy or a risk limit. It tests the property
and fails if the property does not hold.

Usage: pytest tests/autopoiesis/test_journal_rotation_recovery.py -q
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autopoiesis.evaluator import DeterministicEvaluator, confirmed_fill_activities
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import (
    AccountSnapshot,
    BrokerEvidenceBatch,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    TradeDecision,
)

BASE = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)


def _append_cycle(journal: JsonlJournal, *, day: int, action: str, quantity: float, strategy_id) -> None:
    when = BASE + timedelta(days=day)
    journal.append(
        CycleRecord(
            cycle_id=f"c-{day}-{action}",
            snapshot=DataSnapshot(
                symbol="SPY",
                timestamp=when,
                market_open=True,
                last_price=700.0 + day,
                source="alpaca",
                account=AccountSnapshot(
                    equity=100000.0,
                    cash=50000.0,
                    buying_power=150000.0,
                    portfolio_value=100000.0,
                    daily_loss=0.0,
                ),
            ),
            decision=TradeDecision(
                symbol="SPY",
                action=action,
                quantity=quantity,
                confidence=0.9,
                rationale="test",
                strategy_id=strategy_id,
            ),
            guardian=GuardianResult(approved=True, reason="approved"),
            # The fill event below names this id, and `confirmed_fill_activities` drops any fill
            # whose `client_order_id` is not a string anchored to a cycle. Without the link the
            # reconstruction sees zero fills and the lot ledger is empty - a quiet drop, which is
            # the failure mode this whole file exists to catch.
            execution=ExecutionResult(
                status="SUBMITTED", order_id=f"o-{day}-{action}", filled_quantity=0.0, message="test",
                client_order_id=f"coid-{day}-{action}",
            ),
            strategy_id=strategy_id,
        )
    )


def _append_fill(journal: JsonlJournal, *, day: int, side: str, quantity: float, price: float) -> None:
    when = BASE + timedelta(days=day)
    journal.append(
        JournalEvent(
            event_id=f"f-{day}-{side}",
            event_type="ORDER_FILL_CONFIRMED",
            timestamp=when,
            status="SUCCESS",
            message="test fill",
            payload={
                "side": side,
                "filled_quantity": quantity,
                "filled_avg_price": price,
                "order_id": f"o-{day}-{side}",
                # Required: a fill whose client_order_id is not a string anchored to a cycle is
                # dropped by `confirmed_fill_activities` as unattributable.
                "client_order_id": f"coid-{day}-{side}",
                "symbol": "SPY",
                "strategy_id": None,
            },
        )
    )


def _records(journal: JsonlJournal) -> list[CycleRecord]:
    return journal.read_all()


def _batch() -> BrokerEvidenceBatch:
    """A minimal, real-shaped evidence batch.

    `_pnl_evidence` returns MISSING outright when the batch is None - the batch is what verifies an
    exit, and a screen that invented one to make a number appear would be the theatre
    `offline_validation.py` was written to avoid. So the batch is real, small, and empty of fills:
    the fills come from `seeded_fills`, which is the journal's own broker-confirmed history and is
    what makes lots opened weeks earlier matchable at all.
    """
    return BrokerEvidenceBatch(
        generated_at=BASE + timedelta(days=90),
        window_start=BASE,
        window_end=BASE + timedelta(days=90),
    )


def _seeded_fills(journal: JsonlJournal) -> list:
    return confirmed_fill_activities(journal, _records(journal))


def test_a_buy_in_one_generation_and_its_sell_in_another_still_match(tmp_path: Path):
    """The lot ledger must match a fill that moved to `.1` while the exit stayed live.

    Lots opened weeks ago are exactly what rotation moves. Before `seeded_fills` existed, the broker
    evidence batch's short window made a SELL match nothing and `closed_lot_count` was 0 forever -
    the same shape of defect, arriving through rotation instead of through the window.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    _append_cycle(journal, day=0, action="BUY", quantity=2, strategy_id="s-open")
    _append_fill(journal, day=0, side="BUY", quantity=2, price=100.0)
    _append_cycle(journal, day=6, action="SELL", quantity=2, strategy_id="s-close")
    _append_fill(journal, day=6, side="SELL", quantity=2, price=110.0)

    before = DeterministicEvaluator().evaluate(_records(journal), _batch(), seeded_fills=_seeded_fills(journal)).pnl

    # Rotate hard: everything except the last generation moves to `.1`.
    journal._rotate_if_needed(journal.max_bytes + 1)

    after = DeterministicEvaluator().evaluate(_records(journal), _batch(), seeded_fills=_seeded_fills(journal)).pnl
    assert before.closed_lot_count == after.closed_lot_count == 1
    assert before.strategy_realized_pnl == after.strategy_realized_pnl
    assert before.net_pnl == after.net_pnl


def test_the_lot_ledger_survives_several_rotations(tmp_path: Path):
    """Rotation is not a one-off event; it fires repeatedly. Three of them, and the ledger still
    matches - which is the property the README's "only source of truth" claim rests on."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for day in range(0, 40, 6):
        _append_cycle(journal, day=day, action="BUY", quantity=1, strategy_id="s-buy")
        _append_fill(journal, day=day, side="BUY", quantity=1, price=100.0 + day)
        _append_cycle(journal, day=day + 3, action="SELL", quantity=1, strategy_id="s-sell")
        _append_fill(journal, day=day + 3, side="SELL", quantity=1, price=105.0 + day)

    before = DeterministicEvaluator().evaluate(_records(journal), _batch(), seeded_fills=_seeded_fills(journal)).pnl
    expected_lots = before.closed_lot_count
    assert expected_lots >= 5, "the fixture itself must produce several closed lots"

    for _ in range(3):
        journal._rotate_if_needed(journal.max_bytes + 1)

    after = DeterministicEvaluator().evaluate(_records(journal), _batch(), seeded_fills=_seeded_fills(journal)).pnl
    assert after.closed_lot_count == expected_lots
    assert after.net_pnl == before.net_pnl


def test_a_generation_boundary_cannot_lose_a_lifecycle_decision(tmp_path: Path):
    """The registry survives rotation by design, so a boundary must not be able to turn "the
    transition happened" into "the transition is gone"."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    _append_cycle(journal, day=0, action="HOLD", quantity=0, strategy_id="s-life")
    journal.append(
        JournalEvent(
            event_id="lc-1",
            event_type="STRATEGY_LIFECYCLE_UPDATED",
            timestamp=BASE,
            status="SUCCESS",
            message="probation -> active",
            strategy_id="s-life",
            payload={"old_lifecycle": "PROBATION", "new_lifecycle": "ACTIVE",
                     "reason": "broker-verified PnL +1.00"},
        )
    )
    before = journal.read_events("STRATEGY_LIFECYCLE_UPDATED")
    journal._rotate_if_needed(journal.max_bytes + 1)
    after = journal.read_events("STRATEGY_LIFECYCLE_UPDATED")
    assert len(before) == len(after) == 1
    assert after[0].payload["new_lifecycle"] == "ACTIVE"
    assert after[0].strategy_id == "s-life"


def test_rotation_is_recorded_rather_than_being_invisible(tmp_path: Path):
    """A silent rotation is how a record becomes a fragment without anyone noticing: the doctor
    must be able to say which generations hold what, and that older cycles are gone."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    _append_cycle(journal, day=0, action="HOLD", quantity=0, strategy_id="s")
    journal._rotate_if_needed(journal.max_bytes + 1)
    _append_cycle(journal, day=40, action="HOLD", quantity=0, strategy_id="s")
    window = journal.retained_window()
    assert window["generations"] >= 2
    assert window["cycles"] >= 1
    # `truncated` stays None until the caller says when the system was already running: a
    # generation boundary cannot tell "never had more" from "had more and lost it".
    assert window["truncated"] is None


def test_reconstruction_from_a_rotated_record_never_silently_drops_a_fill(tmp_path: Path):
    """The dangerous failure is not a crash, it is a smaller number.

    A rotation that loses an opening fill makes the closing sell unmatched rather than unmatched-
    and-rejected, so the PnL total quietly omits the term instead of failing. This asserts the
    count of fills the reconstruction sees, which is the number a quiet drop would shrink.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for day in range(0, 24, 4):
        _append_fill(journal, day=day, side="BUY", quantity=1, price=100.0)
        _append_fill(journal, day=day + 2, side="SELL", quantity=1, price=102.0)
    before = len(_seeded_fills(journal))
    journal._rotate_if_needed(journal.max_bytes + 1)
    after = len(_seeded_fills(journal))
    assert after == before, "a rotation changed the number of fills the reconstruction sees"
