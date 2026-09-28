import json
from datetime import datetime, timezone

from min_agent.curriculum import StructuredCurriculumAgent, parse_curriculum_task_json
from min_agent.models import CurriculumTask, ReflectionRecord, StrategySpec


def make_reflection(*, window_cycles=10, submitted_orders=2, rejected_orders=0, error_count=0, strategy_scores=None):
    return ReflectionRecord(
        generated_at=datetime.now(tz=timezone.utc),
        window_cycles=window_cycles,
        submitted_orders=submitted_orders,
        rejected_orders=rejected_orders,
        error_count=error_count,
        strategy_scores=strategy_scores or {},
        summary="test",
    )


def make_spec(*, strategy_id="s1"):
    return StrategySpec(
        strategy_id=strategy_id,
        name="Test",
        kind="HOLD_BASELINE",
        symbols=("SPY",),
        parameters={},
        max_position_value=10_000,
        created_at=datetime.now(tz=timezone.utc),
        rationale="test",
    )


def test_fallback_creates_hold_baseline_when_no_strategies():
    agent = StructuredCurriculumAgent()
    task = agent.propose(reflection=make_reflection(), current_strategies=[])

    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec is not None
    assert task.strategy_spec.kind == "HOLD_BASELINE"


def test_fallback_progresses_from_baseline_to_tiny_fixed_size():
    agent = StructuredCurriculumAgent()
    task = agent.propose(reflection=make_reflection(), current_strategies=[make_spec()], guardian_max_position_value=1000)

    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec.kind == "FIXED_SIZE"
    assert task.strategy_spec.parameters == {"action": "BUY", "quantity": 1, "confidence": 0.6}
    assert task.strategy_spec.max_position_value == 1000


def test_fallback_suggests_evaluate_when_trading_strategies_exist():
    agent = StructuredCurriculumAgent()
    trading = StrategySpec(
        strategy_id="fixed",
        name="Fixed",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        max_position_value=1000,
        created_at=datetime.now(tz=timezone.utc),
        rationale="test",
    )
    task = agent.propose(reflection=make_reflection(), current_strategies=[make_spec(), trading])

    assert task.task_type == "EVALUATE"


def test_fallback_breaks_out_after_three_consecutive_no_exploration_evaluates():
    agent = StructuredCurriculumAgent()
    trading = StrategySpec(
        strategy_id="fixed",
        name="Fixed",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        max_position_value=1000,
        created_at=datetime.now(tz=timezone.utc),
        rationale="test",
    )
    strategies = [make_spec(), trading]
    summary = {"no_exploration": True, "window_cycles": 5}

    first = agent.propose(reflection=make_reflection(), current_strategies=strategies, recent_exploration_summary=summary)
    second = agent.propose(reflection=make_reflection(), current_strategies=strategies, recent_exploration_summary=summary)
    third = agent.propose(reflection=make_reflection(), current_strategies=strategies, recent_exploration_summary=summary)
    fourth = agent.propose(reflection=make_reflection(), current_strategies=strategies, recent_exploration_summary=summary)

    assert first.task_type == "EVALUATE"
    assert second.task_type == "EVALUATE"
    assert third.task_type == "EVALUATE"
    assert fourth.task_type == "STRATEGY_SPEC"
    assert fourth.strategy_spec is not None
    assert fourth.strategy_spec.kind == "FIXED_SIZE"
    assert fourth.strategy_spec.parameters["action"] == "BUY"
    assert fourth.strategy_spec.strategy_id.startswith("fallback-breakout-fixed-size-")


def test_parse_curriculum_task_from_json():
    payload = {
        "task_id": "test-task",
        "task_type": "EVALUATE",
        "summary": "evaluate",
        "parameters": {},
        "rationale": "test",
    }
    task = parse_curriculum_task_json(json.dumps(payload))
    assert task.task_id == "test-task"
    assert task.task_type == "EVALUATE"


def test_parse_curriculum_task_rejects_strategy_spec_without_spec():
    payload = {
        "task_id": "bad-task",
        "task_type": "STRATEGY_SPEC",
        "summary": "missing spec",
        "rationale": "test",
    }
    try:
        parse_curriculum_task_json(json.dumps(payload))
    except Exception:
        pass
    else:
        raise AssertionError("expected validation error for missing strategy_spec")


def test_parse_curriculum_task_fills_missing_strategy_created_at():
    now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
    payload = {
        "task_id": "strategy-task",
        "task_type": "STRATEGY_SPEC",
        "summary": "new fixed strategy",
        "strategy_spec": {
            "strategy_id": "new-fixed",
            "name": "New Fixed",
            "kind": "FIXED_SIZE",
            "symbols": ["SPY"],
            "parameters": {"action": "BUY", "quantity": 1, "confidence": 0.6},
            "max_position_value": 1000,
            "rationale": "test",
        },
        "parameters": {},
        "rationale": "test",
    }

    task = parse_curriculum_task_json(json.dumps(payload), now=lambda: now)

    assert task.strategy_spec.created_at == now


def test_parse_curriculum_task_preserves_existing_strategy_created_at():
    created_at = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    payload = {
        "task_id": "strategy-task",
        "task_type": "STRATEGY_SPEC",
        "summary": "new fixed strategy",
        "strategy_spec": {
            "strategy_id": "new-fixed",
            "name": "New Fixed",
            "kind": "FIXED_SIZE",
            "symbols": ["SPY"],
            "parameters": {"action": "BUY", "quantity": 1, "confidence": 0.6},
            "max_position_value": 1000,
            "created_at": created_at.isoformat(),
            "rationale": "test",
        },
        "parameters": {},
        "rationale": "test",
    }

    task = parse_curriculum_task_json(json.dumps(payload), now=lambda: datetime(2026, 6, 11, tzinfo=timezone.utc))

    assert task.strategy_spec.created_at == created_at


def test_parse_curriculum_task_still_rejects_invalid_strategy_params():
    payload = {
        "task_id": "strategy-task",
        "task_type": "STRATEGY_SPEC",
        "summary": "bad fixed strategy",
        "strategy_spec": {
            "strategy_id": "bad-fixed",
            "name": "Bad Fixed",
            "kind": "FIXED_SIZE",
            "symbols": ["SPY"],
            "parameters": {"fixed_size": 1},
            "max_position_value": 1000,
            "rationale": "test",
        },
        "parameters": {},
        "rationale": "test",
    }

    try:
        parse_curriculum_task_json(json.dumps(payload))
    except Exception:
        pass
    else:
        raise AssertionError("expected invalid params to fail")


def test_parse_curriculum_task_skips_unrelated_json_before_valid_task():
    unrelated = json.dumps({"temperature": 0.2, "max_tokens": 512})
    payload = json.dumps(
        {
            "task_id": "eval-task",
            "task_type": "EVALUATE",
            "summary": "evaluate",
            "parameters": {},
            "rationale": "test",
        }
    )

    task = parse_curriculum_task_json(f"{unrelated}\n{payload}")

    assert task.task_id == "eval-task"


def test_parse_curriculum_task_rejects_only_unrelated_json():
    try:
        parse_curriculum_task_json(json.dumps({"temperature": 0.2, "max_tokens": 512}))
    except ValueError as exc:
        assert "curriculum task" in str(exc)
    else:
        raise AssertionError("expected no-task error")


def test_parse_curriculum_task_does_not_skip_malformed_task_like_object():
    malformed = json.dumps(
        {
            "task_id": "bad-task",
            "task_type": "STRATEGY_SPEC",
            "summary": "bad fixed strategy",
            "strategy_spec": {
                "strategy_id": "bad-fixed",
                "name": "Bad Fixed",
                "kind": "FIXED_SIZE",
                "symbols": ["SPY"],
                "parameters": {"fixed_size": 1},
                "max_position_value": 1000,
                "rationale": "test",
            },
            "parameters": {},
            "rationale": "test",
        }
    )
    valid = json.dumps(
        {
            "task_id": "eval-task",
            "task_type": "EVALUATE",
            "summary": "evaluate",
            "parameters": {},
            "rationale": "test",
        }
    )

    try:
        parse_curriculum_task_json(f"{malformed}\n{valid}")
    except Exception:
        pass
    else:
        raise AssertionError("expected malformed task-like object to fail")


def test_transport_unrelated_json_falls_back_to_hold_baseline():
    agent = StructuredCurriculumAgent(transport=lambda context: json.dumps({"temperature": 0.2}))

    task = agent.propose(reflection=make_reflection(), current_strategies=[])

    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec.kind == "HOLD_BASELINE"


def test_transport_unrelated_json_falls_back_to_tiny_fixed_size_after_baseline():
    agent = StructuredCurriculumAgent(transport=lambda context: json.dumps({"temperature": 0.2}))

    task = agent.propose(reflection=make_reflection(), current_strategies=[make_spec()], guardian_max_position_value=1000)

    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec.kind == "FIXED_SIZE"
    assert task.strategy_spec.parameters == {"action": "BUY", "quantity": 1, "confidence": 0.6}
    assert task.strategy_spec.max_position_value == 1000


def test_transport_malformed_task_like_json_falls_back_safely():
    malformed = json.dumps(
        {
            "task_id": "bad-task",
            "task_type": "STRATEGY_SPEC",
            "summary": "bad fixed strategy",
            "strategy_spec": {
                "strategy_id": "bad-fixed",
                "name": "Bad Fixed",
                "kind": "FIXED_SIZE",
                "symbols": ["SPY"],
                "parameters": {"fixed_size": 1},
                "max_position_value": 1000,
                "rationale": "test",
            },
            "parameters": {},
            "rationale": "test",
        }
    )
    agent = StructuredCurriculumAgent(transport=lambda context: malformed)

    task = agent.propose(reflection=make_reflection(), current_strategies=[])

    assert task.strategy_spec.strategy_id == "hold-baseline"


def test_curriculum_with_transport():
    spec_json = {
        "strategy_id": "transport-spec",
        "name": "Transport Test",
        "kind": "TREND_FOLLOW",
        "symbols": ["SPY"],
        "parameters": {"reference_price": 100, "threshold_pct": 0.02, "quantity": 2, "confidence": 0.6},
        "max_position_value": 5000,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "rationale": "transport test",
    }
    task_payload = {
        "task_id": "transport-task",
        "task_type": "STRATEGY_SPEC",
        "summary": "test strategy from transport",
        "strategy_spec": spec_json,
        "parameters": {},
        "rationale": "transport test",
    }

    def fake_transport(context):
        return json.dumps(task_payload)

    agent = StructuredCurriculumAgent(transport=fake_transport)
    task = agent.propose(reflection=make_reflection(), current_strategies=[])
    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec.strategy_id == "transport-spec"


def test_curriculum_context_includes_schema_progression_and_forbidden_outputs():
    captured = {}

    def fake_transport(context):
        captured.update(context)
        return json.dumps(
            {
                "task_id": "eval-task",
                "task_type": "EVALUATE",
                "summary": "evaluate",
                "parameters": {},
                "rationale": "test",
            }
        )

    agent = StructuredCurriculumAgent(transport=fake_transport)
    summary = {"no_exploration": True, "window_cycles": 3}
    task = agent.propose(
        reflection=make_reflection(),
        current_strategies=[make_spec()],
        recent_exploration_summary=summary,
        guardian_max_position_value=1000,
    )

    assert task.task_type == "EVALUATE"
    assert captured["supported_strategy_schemas"]["FIXED_SIZE"]["allowed_params"] == ["action", "quantity", "confidence"]
    assert captured["recent_exploration_summary"] == summary
    assert captured["guardian_max_position_value"] == 1000
    assert "no exploration" in " ".join(captured["exploration_policy"])
    assert "tiny FIXED_SIZE" in " ".join(captured["curriculum_progression"])
    assert "Unsupported strategy parameters" in " ".join(captured["forbidden_outputs"])
