"""`screen-direction-neutral`: the gate must fail when a live strategy is rejected for its days.

Built on a fixture journal, so the check is proven able to fail - a check that only ever ran
against a journal where it passes would be believed rather than tested.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from min_agent.journal import JsonlJournal
from min_agent.models import JournalEvent, StrategySpec
from min_agent.strategy_engine import StrategyLibrary

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import verify

T = datetime(2026, 10, 5, 15, tzinfo=timezone.utc)


def _event(event_type, payload, strategy_id=None):
    return JournalEvent(
        event_id=str(uuid4()), event_type=event_type, timestamp=T, status="SUCCESS",
        message="m", strategy_id=strategy_id, payload=payload,
    )


def _workspace(tmp_path, verdict):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    rows = [
        {"cycle_id": f"c{i}", "action": "SELL", "strategy_id": "s", "verdict": "FALSE_TRADE",
         "decided_at": T.isoformat(), "net_return_pct": 0.8}
        for i in range(12)
    ]
    journal.append_event(_event("COUNTERFACTUAL_EVALUATED", {"rows": rows}))
    journal.append_event(_event("OFFLINE_VALIDATION_COMPLETED", {"verdict": verdict}, "s"))
    library = StrategyLibrary(tmp_path / "strategies")
    library.save(StrategySpec(
        strategy_id="s", name="s", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "SELL", "quantity": 1, "confidence": 0.6},
        max_position_value=1000, enabled=True, lifecycle="PROBATION",
        created_at=T, rationale="fixture",
    ))
    return tmp_path / "journal.jsonl", tmp_path / "strategies"


def test_a_live_strategy_rejected_only_for_a_rising_day_fails_the_gate(tmp_path):
    result = verify.check_screen_is_direction_neutral(*_workspace(tmp_path, "REJECT_POOR_DECISIONS"))
    assert result.status == verify.FAIL
    assert "rejected by the days" in result.detail


def test_the_same_record_without_a_rejection_passes(tmp_path):
    result = verify.check_screen_is_direction_neutral(*_workspace(tmp_path, "INCONCLUSIVE_INSUFFICIENT_EVIDENCE"))
    assert result.status == verify.PASS
    assert "SELL 0/12" in result.detail
