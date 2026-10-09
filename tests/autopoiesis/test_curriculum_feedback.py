"""The curriculum must be able to see what it already got refused for.

On 2026-09-29 the model proposed fixed-size-sell-002 four times in a row. Each
time admission returned the identical 212-character refusal naming the colliding
strategy and the dimensions that must differ. The curriculum never saw any of it:
the exploration summary it was given described trading activity - HOLD counts,
submitted orders, last prices - and contained no record of a single spec the model
had itself authored. A refused id therefore looked exactly as available as a new
one, and renaming the same trade never helped.

These tests pin the wiring, not the model's behaviour. Whether qwen3.8:27b
actually proposes something new is a question for the journal, and the admission
guard already fails closed either way.
"""
from __future__ import annotations

import json

import pytest

from autopoiesis.curriculum import (
    StructuredCurriculumAgent,
    _model_facing_summary,
)
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import JournalEvent, ReflectionRecord

TS = "2026-09-29T23:09:00Z"


def _agent(captured: dict) -> StructuredCurriculumAgent:
    def transport(context):
        captured["context"] = context
        return json.dumps({
            "task_id": "t-1", "task_type": "EVALUATE", "summary": "s",
            "strategy_spec": None, "parameters": {}, "rationale": "r",
        })

    return StructuredCurriculumAgent(transport=transport)


def _reflection() -> ReflectionRecord:
    return ReflectionRecord.model_validate({
        "summary": "50 cycles", "window_cycles": 50, "submitted_orders": 10,
        "rejected_orders": 9, "skipped_orders": 31, "error_count": 0,
        "evaluation": {"pnl_evidence": "broker_strategy_closed_lot_pnl_verified"},
        "generated_at": "2026-09-29T23:09:00Z", "cycle_ids": [],
    })


def test_prompt_carries_prior_rejections_and_forbids_reuse():
    captured: dict = {}
    rejections = [
        {"strategy_id": "fixed-size-sell-002",
         "rejected_because": "behaviourally identical to existing strategy "
                             "'fixed-size-sell-001': same kind, symbols, action, "
                             "quantity and max_position_value."},
    ]
    _agent(captured).propose(
        reflection=_reflection(), current_strategies=[],
        recent_exploration_summary={"window_cycles": 50, "no_exploration": False},
        prior_rejections=rejections, guardian_max_position_value=5000.0,
    )
    context = captured["context"]
    assert context["recently_rejected"] == rejections
    assert context["recently_rejected_ids_must_not_be_reused"] == ["fixed-size-sell-002"]
    policy = " ".join(context["exploration_policy"])
    assert "recently_rejected" in policy, "the policy must name the feedback it gives"
    for dimension in ("kind", "symbols", "action", "quantity", "max_position_value"):
        assert dimension in policy, f"the refusal is useless without the {dimension} it names"


def test_no_rejections_is_not_an_error_state():
    captured: dict = {}
    _agent(captured).propose(
        reflection=_reflection(), current_strategies=[],
        recent_exploration_summary={"window_cycles": 50},
        guardian_max_position_value=5000.0,
    )
    assert captured["context"]["recently_rejected"] == []
    assert captured["context"]["recently_rejected_ids_must_not_be_reused"] == []


def test_daemon_deduplicates_repeated_verdicts_for_one_id(tmp_path):
    """A refused id is reviewed on every retry; six copies teach nothing."""
    from autopoiesis.daemon import AgentDaemon

    journal_path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(journal_path)
    for _idx in range(6):
        journal.append_event(JournalEvent(
            event_id=f"e{_idx}", event_type="STRATEGY_ADMISSION_REVIEWED",
            timestamp=TS, status="SUCCESS", message="reviewed",
            strategy_id="fixed-size-sell-002",
            payload={"accepted": False,
                     "reason": "behaviourally identical to existing strategy "
                               "'fixed-size-sell-001'"},
        ))

    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.journal = journal
    out = daemon._recent_rejections()
    assert len(out) == 1, f"one id reviewed six times must yield one lesson: {out}"
    assert out[0]["strategy_id"] == "fixed-size-sell-002"
    assert "behaviourally identical" in out[0]["rejected_because"]


def test_accepted_strategies_are_not_reported_as_rejections(tmp_path):
    from autopoiesis.daemon import AgentDaemon

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append_event(JournalEvent(
        event_id="acc1", event_type="STRATEGY_ADMISSION_REVIEWED",
        timestamp=TS, status="SUCCESS", message="reviewed",
        strategy_id="trend-follow-20260612-001",
        payload={"accepted": True, "reason": "admitted"},
    ))
    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.journal = journal
    assert daemon._recent_rejections() == []


def test_cycle_ids_stay_with_the_daemon_and_not_the_model():
    """Provenance the model cannot use must not cost it prompt budget."""
    summary = {
        "window_cycles": 50, "action_counts": {"HOLD": 47, "BUY": 2},
        "cycle_ids": [f"e475e3c1-a0b8-4376-aa41-2925976adac{i}" for i in range(50)],
    }
    captured: dict = {}
    _agent(captured).propose(
        reflection=_reflection(), current_strategies=[],
        recent_exploration_summary=summary, guardian_max_position_value=5000.0,
    )
    sent = captured["context"]["recent_exploration_summary"]
    assert "cycle_ids" not in sent
    assert "action_counts" in sent, "the summary is for the model, not sanitised away"
    assert len(summary["cycle_ids"]) == 50, "the daemon keeps what the model is spared"
    assert _model_facing_summary(None) is None


def test_rejections_cannot_silently_empty_the_prompt():
    """A budget bug here would reintroduce the truncation this work fixed."""
    captured: dict = {}
    rejections = [
        {"strategy_id": f"fixed-size-buy-{i:03d}",
         "rejected_because": "behaviourally identical to existing strategy "
                             "'fixed-size-buy-001': same kind, symbols, action, "
                             "quantity and max_position_value."}
        for i in range(6)
    ]
    _agent(captured).propose(
        reflection=_reflection(), current_strategies=[],
        recent_exploration_summary={"window_cycles": 50},
        prior_rejections=rejections, guardian_max_position_value=5000.0,
    )
    from autopoiesis.curriculum import prompt_context_text
    from autopoiesis.llm_decision import CURRICULUM_NUM_CTX
    text = prompt_context_text(captured["context"])
    # chars/3.0 over-estimates tokens; the 3.6 estimate that hid the truncation
    # bug was 19% low. 1493 is the longest measured curriculum answer.
    assert int(len(text) / 3.0) + 1493 < CURRICULUM_NUM_CTX
