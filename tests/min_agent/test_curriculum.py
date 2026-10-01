import json
from datetime import datetime, timezone

import pytest

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
    assert len(huge["strategies"]) == 10, "the per-strategy list must be capped"
    assert huge["strategy_count_total"] == 500, "the true library size must still be stated"
    # 10 detailed entries is what an 8B model follows; ids are cheap and are what
    # stops the model proposing a duplicate, so those are listed separately.
    assert len(huge["strategy_ids_in_use"]) == 40
    assert len(json.dumps(realistic)) < 9000, "the live library is now 16 strategies"

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


def test_generation_requires_complete_objects_by_schema_not_by_prose():
    """The model copies whatever shape it is shown.

    Completeness used to depend on prompt prose ("every required field must be
    present"), and the model omitted the top-level summary and rationale, which
    is a validation failure and therefore a silent fallback. Completeness is now
    enforced by a flat schema with a required list, which the constrained decoder
    does honour - so the guarantee no longer rests on the model following an
    instruction.
    """
    from min_agent.curriculum import kind_selection_schema, strategy_spec_schema

    for kind in ("HOLD_BASELINE", "FIXED_SIZE", "TREND_FOLLOW"):
        schema = strategy_spec_schema(kind)
        assert "oneOf" not in schema and "anyOf" not in schema
        assert set(schema["required"]) == set(schema["properties"]), (
            f"{kind}: every field must be required, otherwise the decoder will omit it"
        )
        assert schema["properties"]["kind"]["enum"] == [kind]

    select = kind_selection_schema()
    assert select["required"] == ["kind", "why"]
    assert select["properties"]["kind"]["enum"] == ["HOLD_BASELINE", "FIXED_SIZE", "TREND_FOLLOW"]


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


def test_a_rejected_parse_is_retried_before_falling_back():
    """ollama's constrained decoding does not enforce `const` or
    `additionalProperties` inside a oneOf schema - measured: a conditional schema
    was accepted in 6.4s and then ignored, producing task_type
    'strategy_generation' and arbitrary parameters. So the kind-conditional
    requirement on strategy_spec parameters cannot be encoded, and the model
    produces a usable task some of the time and not others. Retry once with the
    failure reason, and still validate the retry.
    """
    outputs = [
        '{"task_id":"t1","task_type":"STRATEGY_SPEC","summary":"s","strategy_spec":'
        '{"strategy_id":"a","name":"A","kind":"FIXED_SIZE","symbols":["SPY"],'
        '"max_position_value":100,"created_at":"2026-09-28T00:00:00Z",'
        '"rationale":"r","parameters":{}},"parameters":{},"rationale":"r"}',
        '{"task_id":"t2","task_type":"EVALUATE","summary":"ok","strategy_spec":null,'
        '"parameters":{},"rationale":"r"}',
    ]
    seen = []

    def transport(ctx):
        seen.append(ctx)
        return outputs[len(seen) - 1]

    agent = StructuredCurriculumAgent(transport=transport)
    task = agent.propose(
        reflection=_simple_reflection(),
        current_strategies=[],
        guardian_max_position_value=1000,
    )

    assert task.source == "llm"
    assert task.task_id == "t2"
    assert agent.last_failure is None
    assert len(seen) == 2, "the retry must actually call the model again"
    assert "previous_output_rejected" in seen[1]
    assert "FIXED_SIZE" in seen[1]["previous_output_rejected"]["instruction"]


def test_the_retry_is_still_validated_not_trusted():
    """A retry must not lower the bar: two bad outputs still fall back, and the
    reason records both attempts."""
    bad = ('{"task_id":"t","task_type":"STRATEGY_SPEC","summary":"s","strategy_spec":'
           '{"strategy_id":"a","name":"A","kind":"FIXED_SIZE","symbols":["SPY"],'
           '"max_position_value":100,"created_at":"2026-09-28T00:00:00Z",'
           '"rationale":"r","parameters":{}},"parameters":{},"rationale":"r"}')
    calls = []

    def transport(ctx):
        calls.append(ctx)
        return bad

    agent = StructuredCurriculumAgent(transport=transport)
    task = agent.propose(
        reflection=_simple_reflection(), current_strategies=[], guardian_max_position_value=1000
    )

    assert task.source == "fallback"
    assert len(calls) == 2
    assert agent.last_failure is not None
    assert "2 attempt(s)" in agent.last_failure


def test_retries_can_be_disabled():
    calls = []

    def transport(ctx):
        calls.append(ctx)
        return '{"task_id":"t","task_type":"EVALUATE","summary":"s","strategy_spec":null,"parameters":{},"rationale":"r"}'

    agent = StructuredCurriculumAgent(transport=transport, max_parse_retries=0)
    agent.propose(reflection=_simple_reflection(), current_strategies=[], guardian_max_position_value=1000)

    assert len(calls) == 1


def _simple_reflection():
    from datetime import datetime, timezone

    from min_agent.models import ReflectionRecord

    return ReflectionRecord(
        generated_at=datetime.now(tz=timezone.utc), window_cycles=10,
        submitted_orders=1, rejected_orders=0, error_count=0,
        guardian_rejections={}, summary="s", strategy_scores={},
    )


def test_generation_never_trusts_a_model_supplied_timestamp():
    """The model had to supply created_at because the schema requires it, and it
    duly invented one: a strategy admitted today came back with created_at
    2024-06-14. That field orders probation - StrategySelector sorts PROBATION
    candidates by created_at - so a fabricated timestamp silently reorders
    promotion. A timestamp is the system's to record.
    """
    import json as _json
    from datetime import datetime, timezone

    from min_agent.curriculum import generate_curriculum_task_json
    from min_agent.models import CurriculumTask

    def call(prompt, schema):
        if "action" not in schema.get("required", []):
            return '{"kind": "FIXED_SIZE", "why": "w"}'
        return _json.dumps({
            "strategy_id": "probe-1", "name": "P", "symbols": ["SPY"],
            "max_position_value": 1000,
            "created_at": "2024-06-14T00:00:00+00:00",   # fabricated
            "rationale": "r", "enabled": True, "kind": "FIXED_SIZE",
            "action": "BUY", "quantity": 1, "confidence": 0.6,
        })

    before = datetime.now(tz=timezone.utc)
    task = CurriculumTask.model_validate_json(
        generate_curriculum_task_json(call=call, prompt_context={})
    )
    after = datetime.now(tz=timezone.utc)

    created = task.strategy_spec.created_at
    assert before <= created <= after, f"created_at came from the model: {created}"
    assert created.year >= 2026, f"a pre-2026 timestamp is a fabrication: {created}"


def test_generated_task_ids_are_unique_per_call():
    """A repeated proposal must not collide, or the duplicate-id admission rule
    would reject a legitimate second attempt."""
    import json as _json

    from min_agent.curriculum import generate_curriculum_task_json
    from min_agent.models import CurriculumTask

    def call(prompt, schema):
        if "action" not in schema.get("required", []):
            return '{"kind": "FIXED_SIZE", "why": "w"}'
        return _json.dumps({
            "strategy_id": "probe-1", "name": "P", "symbols": ["SPY"],
            "max_position_value": 1000, "created_at": "2026-09-28T00:00:00Z",
            "rationale": "r", "enabled": True, "kind": "FIXED_SIZE",
            "action": "BUY", "quantity": 1, "confidence": 0.6,
        })

    ids = set()
    for _ in range(6):
        task = CurriculumTask.model_validate_json(
            generate_curriculum_task_json(call=call, prompt_context={})
        )
        ids.add(task.task_id)
    # The spec is identical each time, so ids must still be distinguishable.
    assert len(ids) > 1, f"task_id is not unique per call: {ids}"


def test_curriculum_sees_the_open_book_and_the_capability_gap():
    """The context described only the strategies, so the engine could not see that
    the book carried 23 shares bought in June while no selectable strategy could
    express a SELL at all. A curriculum engine needs to know the gap it is being
    asked to fill. These are facts about the account and the library, read from
    the broker snapshot - not an instruction to trade."""
    from datetime import datetime, timezone

    from min_agent.models import ReflectionRecord, StrategySpec

    def spec(sid, kind, action=None, lifecycle="ACTIVE"):
        parameters = {"action": action, "quantity": 1, "confidence": 0.6} if action else {}
        return StrategySpec(
            strategy_id=sid, name=sid, kind=kind, symbols=("SPY",), parameters=parameters,
            max_position_value=1000, enabled=True, lifecycle=lifecycle,
            created_at=datetime.now(tz=timezone.utc), rationale="t",
        )

    strategies = [
        spec("buy-1", "FIXED_SIZE", "BUY"),
        spec("retired-sell", "FIXED_SIZE", "SELL", lifecycle="RETIRED"),
        spec("paused-sell", "FIXED_SIZE", "SELL", lifecycle="PAUSED"),
    ]
    captured = {}

    def transport(ctx):
        captured["ctx"] = ctx
        return (
            '{"kind": "FIXED_SIZE", "why": "w"}'
        )

    agent = StructuredCurriculumAgent(transport=transport)
    agent.propose(
        reflection=ReflectionRecord(
            generated_at=datetime.now(tz=timezone.utc), window_cycles=10, submitted_orders=1,
            rejected_orders=0, error_count=0, guardian_rejections={}, summary="s",
        ),
        current_strategies=strategies,
        open_positions=({"symbol": "SPY", "quantity": 23, "market_value": 17500.0},),
    )
    # The transport is invoked twice (kind, then spec); the context is shared.
    ctx = agent.last_context

    assert ctx["open_positions"] == [{"symbol": "SPY", "quantity": 23, "market_value": 17500.0}]
    coverage = ctx["capability_coverage"]
    assert coverage["declared_actions"] == {"BUY": ["buy-1"]}, (
        "PAUSED and RETIRED strategies cannot express an action"
    )
    assert coverage["selectable_strategy_count"] == 1
    assert "retired-sell" not in coverage["selectable_strategy_ids"]


def test_a_trend_follow_strategy_counts_as_covering_both_sides():
    from datetime import datetime, timezone

    from min_agent.models import StrategySpec

    strategy = StrategySpec(
        strategy_id="tf", name="tf", kind="TREND_FOLLOW", symbols=("SPY",),
        parameters={
            "threshold_pct": 0.02, "confidence": 0.6, "quantity": 1,
            "reference_price": 700.0,
        },
        max_position_value=1000, enabled=True, lifecycle="ACTIVE",
        created_at=datetime.now(tz=timezone.utc), rationale="t",
    )

    from min_agent.curriculum import _capability_coverage

    # Anchored at 700, the same strategy sells at 650 and buys at 750, so the
    # actions available depend on where the market actually is.
    assert set(_capability_coverage([strategy], 650.0, "SPY")["executable_actions_now"]) == {"SELL"}
    assert set(_capability_coverage([strategy], 750.0, "SPY")["executable_actions_now"]) == {"BUY"}
    assert _capability_coverage([strategy], None, "SPY")["uncovered_actions_now"] is None


def test_a_capability_that_cannot_fire_now_is_not_counted_as_covered():
    """The live library had an exit on paper and none in practice: TREND_FOLLOW is
    anchored to a June reference of 735.01, SPY trades at 767, so it emits BUY and
    would keep emitting BUY while price stays above 727.6. A coverage report based
    on declared actions said "SELL: covered" anyway, so the curriculum had no reason
    to propose a strategy that could actually exit."""
    from datetime import datetime, timezone

    from min_agent.curriculum import _capability_coverage
    from min_agent.models import StrategySpec

    stale = StrategySpec(
        strategy_id="trend-follow-buy-001", name="tf", kind="TREND_FOLLOW", symbols=("SPY",),
        parameters={"threshold_pct": 0.01, "confidence": 0.55, "quantity": 1,
                    "reference_price": 735.0057},
        max_position_value=5000, enabled=True, lifecycle="ACTIVE",
        created_at=datetime.now(tz=timezone.utc), rationale="t",
    )

    coverage = _capability_coverage([stale], 767.0, "SPY")

    assert coverage["executable_actions_now"] == {"BUY": ["trend-follow-buy-001"]}
    assert coverage["uncovered_actions_now"] == ["SELL"], (
        "a nominal SELL that cannot fire is not an exit"
    )


def test_a_trend_follow_buy_only_proposal_does_not_count_as_filling_the_sell_gap():
    """Treating every TREND_FOLLOW as covering SELL let three consecutive
    BUY-only proposals count as filling the gap, and the library stayed
    exitless. Covering the action means producing it at the real price."""
    from datetime import datetime, timezone

    from min_agent.curriculum import _unmet_capability
    from min_agent.models import CurriculumTask, StrategySpec

    spec = StrategySpec(
        strategy_id="trend-follow-buy-004", name="tf", kind="TREND_FOLLOW", symbols=("SPY",),
        parameters={"threshold_pct": 0.01, "confidence": 0.6, "quantity": 1,
                    "reference_price": 735.0},
        max_position_value=5000, enabled=True, lifecycle="ACTIVE",
        created_at=datetime.now(tz=timezone.utc), rationale="t",
    )
    task = CurriculumTask(
        task_id="t", task_type="STRATEGY_SPEC", summary="s", strategy_spec=spec,
        rationale="r", source="llm",
    )
    context = {
        "capability_coverage": {
            "uncovered_actions_now": ["SELL"],
            "evaluated_at_last_price": 767.0,
        }
    }

    # At 767 it buys, so it does not supply an exit.
    assert _unmet_capability(task, context) == "SELL"

    # The same strategy below its reference does sell, and then it does.
    context["capability_coverage"]["evaluated_at_last_price"] = 700.0
    assert _unmet_capability(task, context) is None


def test_the_required_action_is_stated_in_the_prompts_that_choose_and_fill():
    """The library could not sell and the model chose FIXED_SIZE then wrote
    action=BUY three times running. Phase A picks a kind from a three-item enum, so
    a gap buried in a nested capability_coverage report cannot steer it. The demand
    has to be a flat statement in the prompt for both the kind choice and the spec
    that writes the action."""
    from datetime import datetime, timezone

    from min_agent.curriculum import generate_curriculum_task_json, kind_selection_schema
    from min_agent.models import CurriculumTask, ReflectionRecord, StrategySpec

    def spec(sid, action):
        return StrategySpec(
            strategy_id=sid, name=sid, kind="FIXED_SIZE", symbols=("SPY",),
            parameters={"action": action, "quantity": 1, "confidence": 0.7},
            max_position_value=1000, enabled=True, lifecycle="ACTIVE",
            created_at=datetime.now(tz=timezone.utc), rationale="t",
        )

    prompts = []

    def call(prompt, schema):
        prompts.append(prompt)
        if set(schema.get("required", [])) == {"kind", "why"}:
            return '{"kind": "FIXED_SIZE", "why": "w"}'
        return json.dumps({
            "strategy_id": "exit-1", "name": "Exit", "symbols": ["SPY"],
            "max_position_value": 1000, "rationale": "r", "enabled": True,
            "kind": "FIXED_SIZE", "action": "SELL", "quantity": 1, "confidence": 0.7,
        })

    context = {
        "required_action": "SELL",
        "capability_coverage": {"uncovered_actions_now": ["SELL"]},
        "open_positions": [{"symbol": "SPY", "quantity": 23}],
    }
    task = CurriculumTask.model_validate_json(
        generate_curriculum_task_json(call=call, prompt_context=context)
    )

    assert len(prompts) == 2
    # Phase A chooses the kind; phase B writes the action. Both must name SELL.
    for index, prompt in enumerate(prompts):
        assert "SELL" in prompt, f"phase {index} prompt never mentions the required action"
    assert "parameters.action to SELL" in prompts[1]
    assert task.strategy_spec.parameters["action"] == "SELL"


def test_no_capability_gap_means_no_demand_in_the_prompt():
    from min_agent.curriculum import generate_curriculum_task_json, kind_selection_schema

    prompts = []

    def call(prompt, schema):
        prompts.append(prompt)
        if set(schema.get("required", [])) == {"kind", "why"}:
            return '{"kind": "FIXED_SIZE", "why": "w"}'
        return json.dumps({
            "strategy_id": "b-1", "name": "B", "symbols": ["SPY"],
            "max_position_value": 1000, "rationale": "r", "enabled": True,
            "kind": "FIXED_SIZE", "action": "BUY", "quantity": 1, "confidence": 0.7,
        })

    generate_curriculum_task_json(call=call, prompt_context={"required_action": None})

    assert all("no working exit" not in prompt for prompt in prompts)
    assert all("parameters.action to" not in prompt for prompt in prompts)
    assert "Choose exactly one strategy kind" in prompts[0]


def test_a_required_action_is_never_hold_baseline():
    from min_agent.curriculum import _required_action

    assert _required_action({"uncovered_actions_now": ["SELL"]}) == "SELL"
    assert _required_action({"uncovered_actions_now": ["BUY", "SELL"]}) == "BUY|SELL"
    assert _required_action({"uncovered_actions_now": None}) is None


def test_a_percentage_threshold_is_rescaled_to_the_fraction_the_schema_wants():
    """Measured on the real models: the constrained decoder enforces `required` and
    `enum` but ignores numeric `minimum`/`maximum`. A schema bounding
    threshold_pct to 0.2 did not stop the model answering 1.5, 0.5 or 2.5 - it
    reads the field as a percentage every time, and saying "0.02 means 2 percent"
    in the prompt only moved it from 1.5 to 0.5. So every TREND_FOLLOW proposal was
    rejected by StrategySpec and the curriculum fell back.

    Rescaling is a unit conversion, not an invented value: 1.5 means 1.5 percent.
    """
    from min_agent.curriculum import _normalise_units

    for given, expected in ((1.5, 0.015), (0.5, 0.005), (2.5, 0.025), (20, 0.2)):
        params = {"threshold_pct": given}
        _normalise_units(params)
        assert params["threshold_pct"] == pytest.approx(expected)

    # Values already in range, and nonsense, are left alone for the validator.
    for given in (0.02, 0.1, 0.2):
        params = {"threshold_pct": given}
        _normalise_units(params)
        assert params["threshold_pct"] == given
    for given in (150.0, -1.0, 0):
        params = {"threshold_pct": given}
        _normalise_units(params)
        assert params["threshold_pct"] == given


def test_a_percentage_threshold_actually_produces_an_admissible_strategy():
    """End to end: the model answers 1.5, and what reaches StrategySpec validates."""
    import json as _json
    from datetime import datetime, timezone

    from min_agent.curriculum import generate_curriculum_task_json, kind_selection_schema
    from min_agent.models import CurriculumTask, StrategySpec

    def call(prompt, schema):
        if set(schema.get("required", [])) == {"kind", "why"}:
            return '{"kind": "TREND_FOLLOW", "why": "w"}'
        return _json.dumps({
            "strategy_id": "tf-1", "name": "TF", "symbols": ["SPY"],
            "max_position_value": 5000, "rationale": "r", "enabled": True,
            "kind": "TREND_FOLLOW",
            "threshold_pct": 1.5,          # the model answers in percent
            "reference_price": 766.0,
            "quantity": 1, "confidence": 0.6,
        })

    task = CurriculumTask.model_validate_json(
        generate_curriculum_task_json(call=call, prompt_context={})
    )
    spec = task.strategy_spec

    # Re-validated through the model that admission uses.
    StrategySpec.model_validate(spec.model_dump())
    assert spec.parameters["threshold_pct"] == pytest.approx(0.015)
