"""The loop's own audit trail, derived rather than stored.

Every link of the chain the objective names is already journalled, so no registry
needed to exist - building one would have created a second source of truth about
the same thing, which is the failure this refactor has been removing everywhere
else. These tests pin that it is a pure view, and that a broken chain is reported
rather than quietly omitted.
"""

from datetime import datetime, timezone

from min_agent import experiment_registry as er
from min_agent.models import JournalEvent, StrategySpec

TS = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _event(event_type, strategy_id=None, payload=None, n=0):
    return JournalEvent(
        event_id=f"e{n}-{event_type}-{strategy_id}",
        event_type=event_type,
        timestamp=TS,
        status="SUCCESS",
        strategy_id=strategy_id,
        payload=payload or {},
    )


def _spec(strategy_id, lifecycle="ACTIVE", kind="FIXED_SIZE"):
    return StrategySpec(
        strategy_id=strategy_id, name=strategy_id, kind=kind, symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=1000, lifecycle=lifecycle, created_at=TS, rationale="r",
    )


def _full_chain(strategy_id="s1"):
    return [
        _event(er.PROPOSAL, strategy_id, n=1),
        _event(er.ADMISSION, strategy_id, {"accepted": True, "reason": "approved"}, n=2),
        _event(er.VALIDATION, strategy_id, {"verdict": "PASS_SCREENED", "scored": 12,
                                            "good_hold_ratio": 0.8,
                                            "reason": "ok"}, n=3),
        _event(er.LIFECYCLE, strategy_id, {"new_lifecycle": "PROBATION",
                                           "phase": "decided"}, n=4),
        _event(er.LIFECYCLE, strategy_id, {"new_lifecycle": "PROBATION",
                                           "phase": "applied",
                                           "reason": "admitted"}, n=5),
        _event(er.EVALUATION, None, {"strategy_metrics": {strategy_id: {"cycles": 3}}}, n=6),
    ]


def test_a_complete_chain_has_no_missing_links():
    experiments = er.build(_full_chain(), [_spec("s1")])
    assert len(experiments) == 1
    assert experiments[0].complete
    assert experiments[0].missing_links == []
    assert experiments[0].offline_verdict == "PASS_SCREENED"
    assert experiments[0].lifecycle == "PROBATION"
    assert experiments[0].evaluations == 1


def test_the_view_writes_nothing():
    """It is a view. A registry that persisted would be a second source of truth."""
    assert not hasattr(er, "save")
    assert not hasattr(er, "write")
    assert not hasattr(er, "append")
    # Building twice from the same input gives the same answer.
    events = _full_chain()
    first = er.build(events, [_spec("s1")])
    second = er.build(events, [_spec("s1")])
    assert [e.to_dict() if hasattr(e, "to_dict") else e.__dict__ for e in first] == [
        e.to_dict() if hasattr(e, "to_dict") else e.__dict__ for e in second
    ]


def test_evaluation_evidence_is_read_from_strategy_metrics():
    """The event's own strategy_id is None; the per-strategy evidence is in
    payload["strategy_metrics"]. Reading the top-level field reported ACTIVE
    strategies as having no evaluation evidence at all."""
    experiments = er.build(_full_chain(), [_spec("s1")])
    assert experiments[0].evaluations == 1
    assert "tradable with no evaluation evidence" not in experiments[0].missing_links


def test_a_tradable_strategy_with_no_evaluation_evidence_is_flagged():
    events = _full_chain()[:-1]
    experiments = er.build(events, [_spec("s1")])
    assert "tradable with no evaluation evidence" in experiments[0].missing_links
    assert not experiments[0].complete


def test_an_admitted_strategy_never_screened_is_flagged():
    """The gap this stage was added to close, and it must stay visible."""
    events = [
        _event(er.ADMISSION, "s1", {"accepted": True, "reason": "approved"}),
    ]
    experiments = er.build(events, [_spec("s1", lifecycle="PROBATION")])
    assert "admitted but never screened" in experiments[0].missing_links


def test_a_strategy_in_the_registry_but_absent_from_the_journal_still_appears():
    """Otherwise the view hides exactly the case worth looking at."""
    experiments = er.build([], [_spec("orphan")])
    assert [e.strategy_id for e in experiments] == ["orphan"]
    assert not experiments[0].complete


def test_a_rejected_proposal_is_shown_as_rejected_not_admitted():
    events = [
        _event(er.ADMISSION, "s1", {"accepted": False, "reason": "behavioural duplicate"}),
    ]
    # A rejected proposal has no registry file, so it is built with no spec at
    # all - which is the shape that first made this check report the five
    # legitimately-rejected strategies as missing.
    experiments = er.build(events, [])
    assert experiments[0].admitted is False
    assert "behavioural duplicate" in experiments[0].admission_reason


def test_a_decided_lifecycle_event_does_not_count_until_it_is_applied():
    """Journal-first means a 'decided' event can exist for a transition that never
    happened. Reading it as the new state would make the view claim a state change
    that was never written."""
    events = _full_chain()[:4]  # proposed, admitted, validated, lifecycle decided
    experiments = er.build(events, [_spec("s1", lifecycle="PROBATION")])
    assert experiments[0].lifecycle == "PROBATION", "the registry value stands"
    assert experiments[0].lifecycle_reason == ""


def test_the_summary_counts_broken_chains_separately():
    experiments = er.build(
        _full_chain("good") + [_event(er.ADMISSION, "bad", {"accepted": True})],
        [_spec("good"), _spec("bad", lifecycle="PROBATION")],
    )
    summary = er.summarize(experiments)
    assert summary["strategies"] == 2
    assert summary["admitted"] == 2
    assert summary["incomplete_chains"] == 1
    assert summary["incomplete_strategy_ids"] == ["bad"]
