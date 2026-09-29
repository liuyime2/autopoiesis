"""Lineage, search effort, and champion-challenger, derived from the journal.

Three things the objective requires and the system did not record. These tests pin
the derived view and, more importantly, pin that it is a *view*: a second persisted
ledger about the same events would drift from them, and drift in the trial count is
exactly the failure the trial count exists to prevent.

The findings these produce on the live journal are uncomfortable and are the reason
the module exists: 33 of 33 candidates cannot say what observation motivated them,
and one task produced three variants whose surviving sibling inherits that selection.
"""

from datetime import datetime, timezone

import pytest

from min_agent import lineage
from min_agent.models import JournalEvent, StrategySpec

TS = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _event(event_type, strategy_id=None, task_id=None, payload=None):
    return JournalEvent(
        event_id=f"{event_type}-{strategy_id}-{task_id}",
        event_type=event_type,
        timestamp=TS,
        status="SUCCESS",
        message="m",
        strategy_id=strategy_id,
        task_id=task_id,
        payload=payload or {},
    )


def _spec(strategy_id, lifecycle="PROBATION", kind="FIXED_SIZE"):
    return StrategySpec(
        strategy_id=strategy_id, name=strategy_id, kind=kind, symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=1000, lifecycle=lifecycle,
        created_at=TS, rationale="r",
    )


class _Result:
    def __init__(self, realized_pnl=None, pnl_evidence=None):
        self.realized_pnl = realized_pnl
        self.pnl_evidence = pnl_evidence


VERIFIED = "broker_strategy_closed_lot_pnl_verified"


# --------------------------------------------------------------------------
# it is a view
# --------------------------------------------------------------------------

def test_the_module_writes_nothing():
    for name in ("save", "write", "append", "persist", "commit"):
        assert not hasattr(lineage, name), (
            f"lineage.{name} exists, which would make this a second store to drift"
        )


# --------------------------------------------------------------------------
# lineage
# --------------------------------------------------------------------------

def test_a_candidate_is_linked_to_the_observation_that_produced_it():
    events = [
        _event(lineage.PROPOSAL, "s1", "t1", {
            "strategy_id": "s1", "rationale": "no exit for the open lot",
            "source": "llm", "task_type": "STRATEGY_SPEC",
            "recent_exploration_summary": {"uncovered_actions_now": ["SELL"]},
        }),
    ]
    origins = lineage.build_origins(events)

    assert origins["s1"].task_id == "t1"
    assert origins["s1"].observation == {"uncovered_actions_now": ["SELL"]}
    assert origins["s1"].source == "llm"


def test_a_candidate_with_no_recorded_observation_is_reported_as_such():
    """Not defaulted to "fine". The live journal shows 33 of 33 in this state."""
    events = [_event(lineage.PROPOSAL, "s1", "t1", {"strategy_id": "s1"})]
    origins = lineage.build_origins(events)

    assert "no recorded observation that motivated it" in (
        origins["s1"].missing_justification()
    )


def test_an_unproposed_strategy_has_no_origin_rather_than_a_hollow_one():
    assert lineage.build_origins([]) == {}


# --------------------------------------------------------------------------
# search effort
# --------------------------------------------------------------------------

def test_variants_from_one_task_are_counted_and_marked_as_a_search():
    """The selection-bias hole. Keep the best of three and report it as the only
    candidate, and the reported number is not the number the system searched."""
    events = [
        _event(lineage.PROPOSAL, f"s{i}", "t1", {"strategy_id": f"s{i}"})
        for i in range(3)
    ]
    origins = lineage.build_origins(events)
    effort = lineage.search_effort(origins.values())

    assert effort["proposals"] == 3
    assert effort["distinct_tasks"] == 1
    assert effort["tasks_that_searched"] == 1
    assert effort["max_variants_from_one_task"] == 3
    assert effort["searched_tasks"] == {"t1": 3}
    assert all(o.is_search_result for o in origins.values())


def test_one_candidate_per_task_is_not_called_a_search():
    events = [
        _event(lineage.PROPOSAL, "a", "t1", {"strategy_id": "a"}),
        _event(lineage.PROPOSAL, "b", "t2", {"strategy_id": "b"}),
    ]
    effort = lineage.search_effort(lineage.build_origins(events).values())

    assert effort["tasks_that_searched"] == 0
    assert effort["max_variants_from_one_task"] == 1


def test_search_effort_on_nothing_does_not_explode():
    effort = lineage.search_effort([])
    assert effort == {
        "candidates": 0, "distinct_tasks": 0, "proposals": 0,
        "tasks_that_searched": 0, "max_variants_from_one_task": 0,
        "searched_tasks": {},
    }


# --------------------------------------------------------------------------
# champion / challenger
# --------------------------------------------------------------------------

def test_the_champion_is_the_strategy_with_verified_positive_pnl():
    specs = [_spec("winner", lifecycle="ACTIVE"), _spec("loser", lifecycle="ACTIVE")]
    results = {
        "winner": _Result(realized_pnl=120.37, pnl_evidence=VERIFIED),
        "loser": _Result(realized_pnl=-25.0, pnl_evidence=VERIFIED),
    }
    outcome = lineage.designate_champion(specs, results)

    assert outcome.champion == "winner"
    assert "+120.37" in outcome.champion_reason
    assert "only strategy on record" in outcome.champion_reason


def test_the_champion_reason_counts_the_other_qualifiers():
    """"The only such strategy on record" was the first wording and it was false:
    two ACTIVE strategies carry verified positive PnL. A health report that
    overstates its own case is the same defect as one that understates it."""
    specs = [_spec("a", lifecycle="ACTIVE"), _spec("b", lifecycle="ACTIVE")]
    results = {
        "a": _Result(realized_pnl=120.0, pnl_evidence=VERIFIED),
        "b": _Result(realized_pnl=81.0, pnl_evidence=VERIFIED),
    }
    reason = lineage.designate_champion(specs, results).champion_reason

    assert "highest of 2" in reason
    assert "b" in reason and "+81.00" in reason
    assert "only strategy on record" not in reason


def test_the_highest_verified_pnl_wins_regardless_of_input_order():
    specs = [_spec("low", lifecycle="ACTIVE"), _spec("high", lifecycle="ACTIVE")]
    results = {
        "low": _Result(realized_pnl=1.0, pnl_evidence=VERIFIED),
        "high": _Result(realized_pnl=900.0, pnl_evidence=VERIFIED),
    }
    assert lineage.designate_champion(specs, results).champion == "high"


def test_with_nothing_verified_there_is_no_champion_and_it_says_so():
    """The honest answer. Naming a champion from cycles, or an in-sample backtest, or
    the model's opinion would be choosing a benchmark on evidence the system already
    distrusts."""
    specs = [_spec("a", lifecycle="ACTIVE"), _spec("b", lifecycle="PROBATION")]
    outcome = lineage.designate_champion(specs, {})

    assert outcome.champion is None
    assert "none" in outcome.champion_reason
    assert "no evidence-based benchmark" in outcome.champion_reason


def test_a_probation_strategy_is_a_challenger_not_unrated():
    specs = [_spec("c1", lifecycle="PROBATION")]
    outcome = lineage.designate_champion(specs, {})

    assert outcome.challengers == ["c1"]
    assert outcome.unrated == []


def test_a_retired_strategy_with_verified_pnl_cannot_be_champion():
    """A strategy that was retired on its own verified losses is not the benchmark
    to beat, however well it once did."""
    specs = [_spec("ex", lifecycle="RETIRED")]
    results = {"ex": _Result(realized_pnl=500.0, pnl_evidence=VERIFIED)}
    outcome = lineage.designate_champion(specs, results)

    assert outcome.champion is None


def test_unverified_pnl_cannot_make_a_champion():
    """Only broker-verified PnL counts. A number the system computed about itself
    is not the same as a number the broker confirmed."""
    specs = [_spec("a", lifecycle="ACTIVE")]
    results = {"a": _Result(realized_pnl=90.0, pnl_evidence="broker_portfolio_history_verified")}
    outcome = lineage.designate_champion(specs, results)

    assert outcome.champion is None, "account-level PnL is not strategy-level proof"


def test_admission_and_validation_are_folded_into_the_origin():
    events = [
        _event(lineage.PROPOSAL, "s1", "t1", {"strategy_id": "s1", "rationale": "r"}),
        _event(lineage.ADMISSION, "s1", None, {"accepted": False, "reason": "duplicate"}),
        _event(lineage.VALIDATION, "s1", None, {"verdict": "REJECT_POOR_DECISIONS",
                                                "scored": 12, "good_hold_ratio": 0.1}),
    ]
    origin = lineage.build_origins(events)["s1"]

    assert origin.accepted is False
    assert origin.admission_reason == "duplicate"
    assert origin.offline_verdict == "REJECT_POOR_DECISIONS"
    assert "screened as REJECT_POOR_DECISIONS" in origin.missing_justification()


def test_a_never_screened_candidate_reports_that():
    events = [_event(lineage.PROPOSAL, "s1", "t1", {"strategy_id": "s1",
                                                    "recent_exploration_summary": {"x": 1},
                                                    "rationale": "r"})]
    assert "never screened" in lineage.build_origins(events)["s1"].missing_justification()


def test_only_applied_lifecycle_transitions_count():
    """Journal-first means a 'decided' event can exist for a transition that never
    happened. Reading it as the new state would have the view claiming a change the
    registry never received."""
    events = [
        _event(lineage.PROPOSAL, "s1", "t1", {"strategy_id": "s1"}),
        _event(lineage.LIFECYCLE, "s1", None,
               {"new_lifecycle": "ACTIVE", "phase": "decided"}),
    ]
    assert lineage.build_origins(events)["s1"].lifecycle == ""


@pytest.mark.parametrize("event_type", [lineage.ADMISSION, lineage.VALIDATION])
def test_a_validation_or_admission_without_a_proposal_creates_no_origin(event_type):
    """Otherwise a strategy with no hypothesis behind it would acquire a lineage."""
    events = [_event(event_type, "orphan", None, {"accepted": True,
                                                  "verdict": "PASS_SCREENED"})]
    assert lineage.build_origins(events) == {}
