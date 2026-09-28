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


def test_curriculum_context_is_bounded_and_compact():
    """Regression, with the measured failure point.

    The old context embedded the whole ReflectionRecord dump plus a full
    model_dump of every strategy: ~14kB with only 15 strategies. At that size
    deepseek-r1:8b stopped following the instruction and emitted a meta-refusal
    about "extracting the 'query' field", so every daemon curriculum call fell
    back to a hard-coded EVALUATE. The self-evolution loop was dead while every
    health check reported green.

    Verified live: 15 strategies -> ~6kB parses 2/2; the old 14kB shape parsed
    0/2. This pins that the payload stays bounded and that growth is capped.
    """
    import json
    from datetime import datetime, timezone

    from min_agent.models import ReflectionRecord, StrategySpec

    def spec(i):
        return StrategySpec(
            strategy_id=f"strategy-{i:03d}", name="T", kind="FIXED_SIZE", symbols=("SPY",),
            parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
            max_position_value=1000, enabled=True, lifecycle="ACTIVE",
            created_at=datetime.now(tz=timezone.utc), rationale="t",
        )

    def reflection_for(n):
        return ReflectionRecord(
            generated_at=datetime.now(tz=timezone.utc), window_cycles=200,
            submitted_orders=40, rejected_orders=3, error_count=0,
            guardian_rejections={}, summary="s",
            strategy_scores={f"strategy-{i:03d}": 0.5 for i in range(n)},
            strategy_metrics={
                f"strategy-{i:03d}": {
                    "cycles": 20, "trade_attempts": 5, "submitted_orders": 5,
                    "filled_quantity": 5.0, "action_counts": {"BUY": 5}, "score": 0.5,
                }
                for i in range(n)
            },
        )

    def measure(count):
        captured = {}

        def transport(ctx):
            captured["ctx"] = ctx
            return (
                '{"task_id":"t","task_type":"EVALUATE","summary":"s","strategy_spec":null,'
                '"parameters":{},"rationale":"r"}'
            )

        StructuredCurriculumAgent(transport=transport).propose(
            reflection=reflection_for(count), current_strategies=[spec(i) for i in range(count)]
        )
        return captured["ctx"]

    realistic = measure(15)
    # Measured: 15 strategies -> ~8.3kB with the explicit schema block, and
    # that parses. The old shape was ~14kB with the same 15 strategies and
    # produced a meta-refusal 0/2 times.
    assert len(json.dumps(realistic)) < 10000, (
        f"realistic 15-strategy context is {len(json.dumps(realistic))} chars; "
        "deepseek-r1:8b failed to follow it at ~14kB"
    )

    # Growth is capped, so a runaway library cannot re-break the prompt.
    huge = measure(500)
    assert len(json.dumps(huge)) < 20000, "context must not grow without bound"
    assert len(huge["strategies"]) == 40, "the per-strategy list must be capped"
    assert huge["strategy_count_total"] == 500, "the true library size must still be stated"

    # The duplicated full-reflection blob must be gone.
    assert "evaluation" not in realistic
    # One entry per strategy, carrying its own evidence, rather than four maps.
    first = realistic["strategies"][0]
    for key in ("strategy_id", "kind", "lifecycle", "parameters", "score",
                "action_counts", "trade_attempts", "submitted_orders", "filled_quantity"):
        assert key in first, f"merged strategy entry is missing {key!r}"
    for gone in ("strategy_scores", "strategy_actions", "strategy_trade_evidence", "reflection"):
        assert gone not in realistic, f"{gone!r} duplicated the strategy data"


def test_curriculum_context_omits_the_duplicate_evaluation_blob():
    """ReflectionRecord carries both strategy_metrics and a full nested
    `evaluation`; sending the whole record doubled the payload for no gain."""
    import json
    from datetime import datetime, timezone

    from min_agent.models import ReflectionRecord

    reflection = ReflectionRecord(
        generated_at=datetime.now(tz=timezone.utc), window_cycles=10,
        submitted_orders=1, rejected_orders=0, error_count=0,
        guardian_rejections={}, summary="s", strategy_scores={"a": 0.5},
        strategy_metrics={"a": {"cycles": 1, "trade_attempts": 1, "action_counts": {"BUY": 1}}},
    )
    captured = {}

    def transport(ctx):
        captured["ctx"] = ctx
        return (
            '{"task_id":"t","task_type":"EVALUATE","summary":"s","strategy_spec":null,'
            '"parameters":{},"rationale":"r"}'
        )

    StructuredCurriculumAgent(transport=transport).propose(
        reflection=reflection, current_strategies=[]
    )

    assert "evaluation" not in captured["ctx"]
    # The reflection-derived slice must be far smaller than the whole record.
    reflection_only = {
        k: v for k, v in captured["ctx"].items()
        if k in ("window_cycles", "submitted_orders", "rejected_orders",
                 "error_count", "pnl_evidence", "strategies", "strategy_count_total")
    }
    assert len(json.dumps(reflection_only)) < len(json.dumps(reflection.model_dump(mode="json")))


def test_prompt_requires_complete_objects_and_forbids_literal_placeholders():
    """The model copies whatever example it is given.

    The context carries a worked_example showing the required shape, and without
    an explicit instruction the model copied its task_id verbatim ('task-0001')
    and omitted the top-level rationale - which is a validation failure, and
    therefore a silent fallback. Both halves of this were real: the prompt was
    rewritten at one point and lost its "placeholders are examples only" line.
    """
    import inspect

    import min_agent.cli as cli

    source = inspect.getsource(cli._ollama_curriculum_transport)
    assert "worked_example" in source or "SHAPE only" in source
    assert "SHAPE only" in source, "the prompt must say the example is a shape, not a template"
    assert "must not duplicate" in source or "not duplicate" in source
    assert "must be present" in source, "the prompt must state required fields are mandatory"


def test_curriculum_context_carries_an_explicit_strategy_spec_shape():
    """Compacting the context removed the full model_dump of every strategy,
    which had been serving as the de-facto schema example. The model then emitted
    a strategy_spec with no strategy_id, name, symbols or max_position_value and
    every call fell back. The shape must be stated, not shown by example."""
    import json
    from datetime import datetime, timezone

    from min_agent.models import ReflectionRecord, StrategySpec

    reflection = ReflectionRecord(
        generated_at=datetime.now(tz=timezone.utc), window_cycles=10,
        submitted_orders=1, rejected_orders=0, error_count=0,
        guardian_rejections={}, summary="s", strategy_scores={},
    )
    captured = {}

    def transport(ctx):
        captured["ctx"] = ctx
        return (
            '{"task_id":"t","task_type":"EVALUATE","summary":"s","strategy_spec":null,'
            '"parameters":{},"rationale":"r"}'
        )

    StructuredCurriculumAgent(transport=transport).propose(
        reflection=reflection,
        current_strategies=[
            StrategySpec(
                strategy_id="s1", name="S", kind="FIXED_SIZE", symbols=("SPY",),
                parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
                max_position_value=100, created_at=datetime.now(tz=timezone.utc), rationale="t",
            )
        ],
    )

    fields = captured["ctx"]["strategy_spec_fields"]
    for required in ("strategy_id", "name", "kind", "symbols", "max_position_value", "created_at", "rationale"):
        assert required in fields["required"], f"{required} must be listed as required"
    assert captured["ctx"]["worked_example"]["strategy_spec"]["kind"] == "FIXED_SIZE"
    assert json.dumps(captured["ctx"]["schema"]).count("required") >= 4
