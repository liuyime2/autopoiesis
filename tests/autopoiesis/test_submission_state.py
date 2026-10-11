from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    TradeDecision,
)
from autopoiesis.submission_state import SubmissionJournalError, project_submission_state

NOW = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)


def _cycle(status: str, *, client_order_id: str = "cid-legacy") -> CycleRecord:
    return CycleRecord(
        cycle_id="cycle-legacy",
        snapshot=DataSnapshot(
            symbol="SPY",
            timestamp=NOW,
            market_open=True,
            last_price=100,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000,
                cash=100_000,
                buying_power=100_000,
                portfolio_value=100_000,
                daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action="BUY", quantity=1, confidence=0.8, rationale="test",
            strategy_id="s1",
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status=status,
            order_id="oid-1" if status == "SUBMITTED" else None,
            client_order_id=client_order_id,
            filled_quantity=0,
            message="test",
        ),
        strategy_id="s1",
    )


def _event(event_type: str, *, client_order_id="cid-1", payload=None) -> JournalEvent:
    return JournalEvent(
        event_id=f"{event_type}-{client_order_id}",
        event_type=event_type,
        timestamp=NOW,
        status="SUCCESS" if event_type.endswith(("ACKNOWLEDGED", "RESOLVED")) else "ACCEPTED",
        cycle_id="cycle-1",
        strategy_id="s1",
        payload={"client_order_id": client_order_id, **(payload or {})},
    )


def _fresh_journal(path) -> JsonlJournal:
    journal = JsonlJournal(path)
    assert journal.archive_manifest_status()["trusted"]
    return journal


def test_unacknowledged_write_ahead_intent_is_unresolved_after_restart(tmp_path):
    journal = _fresh_journal(tmp_path / "journal.jsonl")
    journal.append_event(_event("ORDER_SUBMISSION_INTENT", payload={"symbol": "SPY", "quantity": 1}))

    state = project_submission_state(journal)

    assert set(state.unresolved) == {"cid-1"}
    assert state.unresolved["cid-1"]["symbol"] == "SPY"
    assert state.known_order_ids == frozenset()


def test_acknowledgement_without_cycle_is_known_and_not_ambiguous(tmp_path):
    journal = _fresh_journal(tmp_path / "journal.jsonl")
    journal.append_event(_event(
        "ORDER_SUBMISSION_INTENT", payload={"symbol": "SPY", "quantity": 1}
    ))
    journal.append_event(_event(
        "ORDER_SUBMISSION_ACKNOWLEDGED",
        payload={"order_id": "broker-1", "broker_status": "accepted"},
    ))

    state = project_submission_state(journal)

    assert state.unresolved == {}
    assert state.known_order_ids == frozenset({"broker-1"})
    assert state.attempt_client_order_ids == frozenset({"cid-1"})


def test_positive_resolution_clears_unknown_and_projects_order_id(tmp_path):
    journal = _fresh_journal(tmp_path / "journal.jsonl")
    journal.append_event(_event("ORDER_SUBMISSION_INTENT"))
    journal.append_event(_event("ORDER_SUBMISSION_UNKNOWN"))
    journal.append_event(_event(
        "ORDER_SUBMISSION_RESOLVED",
        payload={"order_id": "broker-2", "broker_status": "filled"},
    ))

    state = project_submission_state(journal)

    assert state.unresolved == {}
    assert state.known_order_ids == frozenset({"broker-2"})


def test_manual_resolution_event_does_not_clear_block_without_supported_workflow(tmp_path):
    journal = _fresh_journal(tmp_path / "journal.jsonl")
    journal.append_event(_event("ORDER_SUBMISSION_INTENT"))
    journal.append_event(_event("ORDER_SUBMISSION_UNKNOWN"))
    journal.append_event(_event(
        "ORDER_SUBMISSION_MANUALLY_RESOLVED",
        payload={
            "operator_id": "operator-1",
            "evidence_reference": "case-1",
            "terminal_disposition": "rejected",
        },
    ))

    state = project_submission_state(journal)

    assert "cid-1" in state.unresolved
    assert state.known_order_ids == frozenset()


def test_legacy_error_client_id_is_migrated_as_unknown(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(_cycle("ERROR"))
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="paper-journal-audit", reason="test migration"
    )

    state = project_submission_state(journal)

    assert set(state.unresolved) == {"cid-legacy"}
    assert state.unresolved["cid-legacy"]["legacy_cycle_id"] == "cycle-legacy"


def test_corrupt_journal_refuses_to_build_safety_projection(tmp_path):
    journal = _fresh_journal(tmp_path / "journal.jsonl")
    journal.append_event(_event("ORDER_SUBMISSION_INTENT"))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("torn-json\n")

    with pytest.raises(SubmissionJournalError, match="corrupt"):
        project_submission_state(journal)


def test_existing_history_without_manifest_refuses_safety_projection(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(_cycle("ERROR"))

    with pytest.raises(SubmissionJournalError, match="attest"):
        project_submission_state(journal)
