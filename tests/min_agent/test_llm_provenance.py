"""Regression tests for LLM provenance (D7).

The defects this file exists to prevent:

    The daemon injected PolicyEngine as its decision engine, so
    OllamaDecisionEngine.decide was reachable only from `--once`. Every trade
    decision in daemon mode was a static JSON lookup with no market reasoning,
    while the model was consulted only about curriculum.

    curriculum._fallback_task caught every LLM failure and returned a hard-coded
    task, which the daemon journaled as status="SUCCESS". 30 of the 168 recorded
    CURRICULUM_PROPOSED events were such constants and the journal could not
    distinguish them from model output.

    The curriculum prompt hard-coded `"created_at":"2026-06-10T00:00:00Z"` and an
    example `{"reference_price": 100.0}`. The model copied the example and nine
    TREND_FOLLOW strategies were admitted with reference_price in
    {100,120,150,170,180,200} against a SPY that traded 723.21-756.55 in the same
    journal, so every one of them was permanently degenerate.

    StructuredCurriculumAgent kept its consecutive-EVALUATE counter in instance
    state, so every daemon restart reset it to zero and the breakout at
    threshold 3 was unreachable.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

TS = datetime(2026, 6, 20, 15, 0, tzinfo=timezone.utc)

from min_agent.curriculum import StructuredCurriculumAgent
from min_agent.guardian import Guardian
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.llm_decision import HybridDecisionEngine, OllamaDecisionEngine, parse_decision_json
from min_agent.models import (
    AccountSnapshot,
    CurriculumTask,
    DataSnapshot,
    StrategySpec,
)
from min_agent.policy_engine import PolicyEngine
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_admission import StrategyAdmission
from min_agent.strategy_engine import StrategyLibrary

NOW = datetime.now(tz=timezone.utc)
REAL_SPY_PRICE = 744.27


def _snapshot(symbol="SPY", price=REAL_SPY_PRICE, positions=(), open_orders=()):
    return DataSnapshot(
        symbol=symbol,
        timestamp=NOW,
        market_open=True,
        last_price=price,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
        positions=tuple(positions),
        open_orders=tuple(open_orders),
    )


def _spec(strategy_id="s1", kind="FIXED_SIZE", lifecycle="ACTIVE", **params):
    base = {"action": "BUY", "quantity": 1, "confidence": 0.6}
    if kind == "HOLD_BASELINE":
        base = {}
    if kind == "TREND_FOLLOW":
        base = {"reference_price": REAL_SPY_PRICE, "threshold_pct": 0.02, "quantity": 1, "confidence": 0.55}
    return StrategySpec(
        strategy_id=strategy_id,
        name="S",
        kind=kind,
        symbols=("SPY",),
        parameters={**base, **params},
        max_position_value=1000,
        enabled=True,
        lifecycle=lifecycle,
        created_at=NOW,
        rationale="t",
    )


class ScriptedLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def decide(self, context):
        self.calls.append(context)
        if self.error is not None:
            raise self.error
        return self.response


def _engine(tmp_path, llm, *, strategies=("s1",), knowledge=None):
    library = StrategyLibrary(tmp_path / "strategies")
    for sid in strategies:
        library.save(_spec(sid))
    policy = PolicyEngine(
        strategy_library=library,
        reflection_memory=ReflectionMemory(tmp_path / "reflection.json"),
        knowledge_library=knowledge,
    )
    return HybridDecisionEngine(llm=llm, policy_engine=policy, lessons=policy.relevant_lessons), policy


# --- the LLM is actually in the decision path --------------------------------


def test_llm_decision_is_used_when_available(tmp_path):
    from min_agent.models import TradeDecision

    llm = ScriptedLLM(
        TradeDecision(
            symbol="SPY", action="BUY", quantity=3, confidence=0.9, rationale="breakout", decision_source="llm"
        )
    )
    engine, _ = _engine(tmp_path, llm)

    decision = engine.decide_snapshot(_snapshot())

    assert decision.action == "BUY"
    assert decision.quantity == 3
    assert decision.decision_source == "llm"
    assert len(llm.calls) == 1


def test_llm_receives_the_real_position_book_and_prices(tmp_path):
    from min_agent.models import PositionSnapshot, TradeDecision

    llm = ScriptedLLM(TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait"))
    engine, _ = _engine(tmp_path, llm)
    held = PositionSnapshot(symbol="SPY", quantity=4, market_value=2977.08)

    engine.decide_snapshot(_snapshot(positions=[held]))

    context = llm.calls[0]
    assert context["last_price"] == REAL_SPY_PRICE
    assert context["positions"] == [
        {"symbol": "SPY", "quantity": 4.0, "market_value": 2977.08}
    ]
    assert context["paper_only"] is True
    assert context["selected_strategy_id"] == "s1"


def test_llm_receives_admitted_lessons(tmp_path):
    from min_agent.models import KnowledgeArtifact, TradeDecision

    knowledge = KnowledgeLibrary(tmp_path / "knowledge")
    knowledge.save(
        KnowledgeArtifact(
            artifact_id="k1",
            artifact_type="LESSON",
            status="ACCEPTED",
            question="q",
            answer="a",
            summary="s1 stalls below 0.6 confidence",
            source_kind="journal",
            source_refs=["s1"],
            tags=["s1"],
            created_at=NOW,
            rationale="r",
        )
    )
    llm = ScriptedLLM(TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait"))
    engine, _ = _engine(tmp_path, llm, knowledge=knowledge)

    engine.decide_snapshot(_snapshot())

    assert llm.calls[0]["lessons"] == ["s1 stalls below 0.6 confidence"]


# --- the fallback is disclosed, never disguised -----------------------------


def test_llm_timeout_falls_back_and_says_so(tmp_path):
    llm = ScriptedLLM(error=TimeoutError("Read timed out. (read timeout=120)"))
    engine, _ = _engine(tmp_path, llm)

    decision = engine.decide_snapshot(_snapshot())

    assert decision.decision_source == "fallback_policy_engine"
    assert "llm_unavailable" in decision.rationale
    assert "Read timed out" in decision.rationale


def test_unparseable_llm_output_falls_back(tmp_path):
    class GarbageLLM:
        def decide(self, context):
            return parse_decision_json("I think you should probably buy, maybe 3 shares")

    engine, _ = _engine(tmp_path, GarbageLLM())

    decision = engine.decide_snapshot(_snapshot())

    assert decision.decision_source == "fallback_policy_engine"


def test_llm_naming_a_different_symbol_falls_back(tmp_path):
    from min_agent.models import TradeDecision

    llm = ScriptedLLM(TradeDecision(symbol="AAPL", action="BUY", quantity=1, confidence=0.9, rationale="x"))
    engine, _ = _engine(tmp_path, llm)

    decision = engine.decide_snapshot(_snapshot(symbol="SPY"))

    assert decision.decision_source == "fallback_policy_engine"
    assert "AAPL" in decision.rationale


def test_fallback_still_produces_a_real_trade(tmp_path):
    llm = ScriptedLLM(error=ConnectionError("refused"))
    engine, _ = _engine(tmp_path, llm)

    assert engine.decide_snapshot(_snapshot()).action == "BUY"


def test_a_broken_policy_engine_does_not_matter_when_the_llm_works(tmp_path):
    from min_agent.models import TradeDecision

    class ExplodingPolicy:
        selector = None
        strategy_library = None
        reflection_memory = None

        def decide_snapshot(self, snapshot):
            raise AssertionError("policy engine must not be consulted when the LLM answered")

    llm = ScriptedLLM(TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait"))
    engine = HybridDecisionEngine(llm=llm, policy_engine=ExplodingPolicy())

    assert engine.decide_snapshot(_snapshot()).decision_source == "llm"


# --- decision_source is recorded ---------------------------------------------


def test_decision_source_defaults_and_validates():
    from min_agent.models import TradeDecision

    assert TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.1, rationale="r").decision_source == "baseline"
    with pytest.raises(Exception):
        TradeDecision(
            symbol="SPY", action="HOLD", quantity=0, confidence=0.1, rationale="r", decision_source="magic"
        )


# --- curriculum fallbacks declare themselves ---------------------------------


def test_fallback_tasks_declare_their_source(tmp_path):
    agent = StructuredCurriculumAgent(transport=None)
    from min_agent.models import ReflectionRecord

    reflection = ReflectionRecord(
        generated_at=NOW, window_cycles=10, submitted_orders=0, rejected_orders=0, error_count=0,
        guardian_rejections={}, strategy_scores={}, summary="s",
    )
    task = agent.propose(reflection=reflection, current_strategies=[])

    assert task.source == "fallback"
    assert task.task_id.startswith("fallback-")


def test_llm_tasks_are_labelled_llm(tmp_path):
    payload = (
        '{"task_id":"t1","task_type":"EVALUATE","summary":"s","strategy_spec":null,'
        '"parameters":{},"rationale":"r"}'
    )
    agent = StructuredCurriculumAgent(transport=lambda ctx: payload)
    from min_agent.models import ReflectionRecord

    reflection = ReflectionRecord(
        generated_at=NOW, window_cycles=10, submitted_orders=0, rejected_orders=0, error_count=0,
        guardian_rejections={}, strategy_scores={}, summary="s",
    )

    assert agent.propose(reflection=reflection, current_strategies=[_spec()]).source == "llm"


def test_a_garbage_llm_response_produces_a_declared_fallback():
    agent = StructuredCurriculumAgent(transport=lambda ctx: "no json here at all")
    from min_agent.models import ReflectionRecord

    reflection = ReflectionRecord(
        generated_at=NOW, window_cycles=10, submitted_orders=0, rejected_orders=0, error_count=0,
        guardian_rejections={}, strategy_scores={}, summary="s",
    )

    assert agent.propose(reflection=reflection, current_strategies=[_spec()]).source == "fallback"


# --- the curriculum counter survives restarts -------------------------------


def test_evaluate_counter_persists_across_agent_restarts(tmp_path):
    state = tmp_path / "curriculum_state.json"
    from min_agent.models import ReflectionRecord

    reflection = ReflectionRecord(
        generated_at=NOW, window_cycles=10, submitted_orders=0, rejected_orders=0, error_count=0,
        guardian_rejections={}, strategy_scores={}, summary="s",
    )
    strategies = [_spec("s1", kind="HOLD_BASELINE", lifecycle="BASELINE"), _spec("s2", lifecycle="ACTIVE")]

    # Four separate agent instances, i.e. four daemon restarts.
    for _ in range(4):
        StructuredCurriculumAgent(transport=None, state_path=state).propose(
            reflection=reflection, current_strategies=strategies
        )

    assert StructuredCurriculumAgent(state_path=state)._consecutive_evaluate_count == 4


def test_breakout_fires_once_the_persisted_counter_reaches_the_threshold(tmp_path):
    """Three consecutive EVALUATEs, then a strategy - across four separate agent
    instances, i.e. four daemon restarts. Before persistence the counter was
    instance state, so every restart reset it and the breakout never fired."""
    state = tmp_path / "curriculum_state.json"
    from min_agent.models import ReflectionRecord

    reflection = ReflectionRecord(
        generated_at=NOW, window_cycles=10, submitted_orders=0, rejected_orders=0, error_count=0,
        guardian_rejections={}, strategy_scores={}, summary="s",
    )
    strategies = [_spec("s1", kind="HOLD_BASELINE", lifecycle="BASELINE"), _spec("s2", lifecycle="ACTIVE")]
    summary = {"no_exploration": True, "window_cycles": 10}

    tasks = []
    for _ in range(4):
        tasks.append(
            StructuredCurriculumAgent(transport=None, state_path=state).propose(
                reflection=reflection, current_strategies=strategies, recent_exploration_summary=summary
            )
        )

    assert [t.task_type for t in tasks] == ["EVALUATE", "EVALUATE", "EVALUATE", "STRATEGY_SPEC"]
    task = tasks[-1]
    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec.strategy_id.startswith("fallback-breakout")


def test_counter_resets_on_a_strategy_spec(tmp_path):
    state = tmp_path / "curriculum_state.json"
    from min_agent.models import ReflectionRecord

    reflection = ReflectionRecord(
        generated_at=NOW, window_cycles=10, submitted_orders=0, rejected_orders=0, error_count=0,
        guardian_rejections={}, strategy_scores={}, summary="s",
    )
    agent = StructuredCurriculumAgent(transport=None, state_path=state)
    agent.propose(reflection=reflection, current_strategies=[])
    assert StructuredCurriculumAgent(state_path=state)._consecutive_evaluate_count == 0


# --- no fabricated numbers in the prompt -------------------------------------


def test_curriculum_generation_offers_no_copyable_price(monkeypatch):
    """No generated prompt may contain an example price the model can copy.

    The original prompt carried a worked example anchored to a 100-dollar
    reference price; the model copied it and nine strategies were admitted 4-7x
    below the real SPY. The guarantee is now enforced by schema plus
    StrategyAdmission rather than by prose, but the prompts must still be free of
    copyable examples.
    """
    import min_agent.cli as cli

    prompts = []

    class Capturing:
        def __init__(self, **kwargs):
            self.base_url = "http://ollama.invalid"
            self.timeout = 1

        def transport(self, url, payload, timeout):
            prompts.append(payload["prompt"])
            return {"response": '{"kind": "FIXED_SIZE", "why": "w"}'}

    monkeypatch.setattr(cli, "OllamaDecisionEngine", Capturing)
    cli._ollama_curriculum_transport(cli.AgentConfig.from_env())({"current_strategies": []})

    assert prompts, "the transport must have called the model"
    for prompt in prompts:
        assert '"reference_price":100.0' not in prompt
        assert "2026-06-10" not in prompt


def _admission(tmp_path, prices):
    from min_agent.guardian import Guardian
    from min_agent.strategy_admission import StrategyAdmission

    return StrategyAdmission(
        guardian=Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500),
        strategy_library=StrategyLibrary(tmp_path / "strategies"),
        market_prices=prices,
    )


def test_reference_price_from_the_prompt_example_is_rejected(tmp_path):
    admission = _admission(tmp_path, {"SPY": REAL_SPY_PRICE})
    bad = _spec("bad", kind="TREND_FOLLOW", reference_price=100.0, threshold_pct=0.02)

    result = admission.admit(bad)

    assert result.accepted is False
    assert "reference_price" in result.reason
    assert "744.27" in result.reason


def test_realistic_reference_price_is_accepted(tmp_path):
    admission = _admission(tmp_path, {"SPY": REAL_SPY_PRICE})
    # 720.00 against a 744.27 market: a real price, not the prompt's example, and
    # outside its own trigger band [705.60, 734.40] so the strategy can fire. It used
    # to be 740.00, which is inside the band the 2% threshold builds around it - the
    # market sits at 744.27, so that spec could only ever HOLD, which is the case
    # `test_reference_price_whose_band_contains_the_market_is_rejected` now covers.
    good = _spec("good", kind="TREND_FOLLOW", reference_price=720.0, threshold_pct=0.02)

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("base", kind="HOLD_BASELINE", lifecycle="BASELINE"))
    library.save(_spec("fixed", kind="FIXED_SIZE"))
    admission = StrategyAdmission(
        guardian=Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500),
        strategy_library=library,
        market_prices={"SPY": REAL_SPY_PRICE},
    )

    assert admission.admit(good).accepted is True


def test_reference_price_whose_band_contains_the_market_is_rejected(tmp_path):
    """A reference at the current price makes a TREND_FOLLOW permanently HOLD.

    It fires above `ref*(1+threshold)` and below `ref*(1-threshold)`, so anchoring
    the reference *at* the market puts the band around the present. 26 strategies on
    the live library were admitted that way, and replaying the recorded curriculum
    proposals found 27 accepted while their band already contained spot.

    This is the mirror image of `test_reference_price_from_the_prompt_example_is_rejected`
    and the pre-existing deviation rule cannot catch it: threshold_pct is capped at
    0.20, so a reference that swallows the price is never more than 20% away.
    """
    admission = _admission(tmp_path, {"SPY": REAL_SPY_PRICE})
    at_spot = _spec("at-spot", kind="TREND_FOLLOW", reference_price=REAL_SPY_PRICE, threshold_pct=0.02)

    result = admission.admit(at_spot)

    assert result.accepted is False
    assert "can only ever HOLD" in result.reason
    assert "744.27" in result.reason


def test_band_edge_is_inside_the_refusal(tmp_path):
    """Price exactly on the band edge still cannot trade, so the edge counts as inside."""
    # 744.27 / 1.02 == 729.68, so ref 729.68 puts 744.27 exactly on the upper edge.
    admission = _admission(tmp_path, {"SPY": REAL_SPY_PRICE})
    on_edge = _spec("on-edge", kind="TREND_FOLLOW", reference_price=729.68, threshold_pct=0.02)

    result = admission.admit(on_edge)

    assert result.accepted is False
    assert "can only ever HOLD" in result.reason


def test_a_resting_trigger_outside_the_band_is_still_admitted(tmp_path):
    """The rule must not refuse a strategy that is dormant, not degenerate.

    `trend-follow-buy-001` had band [727.66, 742.36] against spot 765.49 and produced
    6 informative decisions. A trigger the market has not reached yet is a resting
    order, which is exactly what these strategies are for.
    """
    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("base", kind="HOLD_BASELINE", lifecycle="BASELINE"))
    library.save(_spec("fixed", kind="FIXED_SIZE"))
    admission = StrategyAdmission(
        guardian=Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500),
        strategy_library=library,
        market_prices={"SPY": REAL_SPY_PRICE},
    )
    # ref 700.00 -> band [686.00, 714.00]; 744.27 is above it, so this is a resting
    # sell trigger at 714.00.
    resting = _spec("resting", kind="TREND_FOLLOW", reference_price=700.0, threshold_pct=0.02)

    result = admission.admit(resting)

    assert result.accepted is True, result.reason


def test_band_rule_follows_the_price_rather_than_purging_once(tmp_path):
    """The same spec is admissible once the market leaves its band, and not before.

    A frozen anchor is dormant, not wrong, so the rule has to be a function of the
    observed price. A one-time purge of the library would delete resting triggers
    along with the degenerate ones.
    """
    from min_agent.guardian import Guardian
    from min_agent.strategy_admission import StrategyAdmission

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("base", kind="HOLD_BASELINE", lifecycle="BASELINE"))
    library.save(_spec("fixed", kind="FIXED_SIZE"))
    admission = StrategyAdmission(
        guardian=Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500),
        strategy_library=library,
        market_prices={"SPY": REAL_SPY_PRICE},
    )

    # ref 720.00 -> band [705.60, 734.40], which excludes 744.27.
    clear = _spec("clear", kind="TREND_FOLLOW", reference_price=720.0, threshold_pct=0.02)
    assert admission.admit(clear).accepted is True

    # The market now sits inside that same band.
    admission.set_market_prices({"SPY": 720.0})
    same_shape = _spec("same-shape", kind="TREND_FOLLOW", reference_price=720.0, threshold_pct=0.02)
    result = admission.admit(same_shape)

    assert result.accepted is False
    assert "can only ever HOLD" in result.reason


def test_band_rule_is_skipped_when_no_price_is_known(tmp_path):
    """A quality gate, not a safety one: no price must not block admission."""
    admission = _admission(tmp_path, {})
    result = admission.admit(_spec("x", kind="TREND_FOLLOW", reference_price=100.0))

    assert "reference_price" not in result.reason


def test_fixed_size_strategies_are_unaffected(tmp_path):
    admission = _admission(tmp_path, {"SPY": REAL_SPY_PRICE})
    result = admission.admit(_spec("x", kind="FIXED_SIZE", action="BUY", quantity=1))

    assert "reference_price" not in result.reason


def test_daemon_supplies_real_prices_to_admission(tmp_path):
    import inspect

    import min_agent.daemon as daemon

    assert "set_market_prices" in inspect.getsource(daemon.AgentDaemon._curriculum_proposal)
    assert "_last_known_prices" in inspect.getsource(daemon.AgentDaemon._last_known_prices)


def test_both_llm_paths_use_schema_constrained_generation():
    """format="json" only asks for *some* JSON. Measured on deepseek-r1:8b for the
    curriculum task, constraining to a schema was 5x faster (11.7s vs 54.4s) and
    produced 4x less output (901 vs 6993 chars), and it removed every shape
    error."""
    import inspect

    import min_agent.cli as cli
    from min_agent.llm_decision import OllamaDecisionEngine

    transport_src = inspect.getsource(cli._ollama_curriculum_transport)
    assert "generate_curriculum_task_json" in transport_src
    assert '"format": "json"' not in transport_src, "plain 'json' mode is not a schema"

    decision_src = inspect.getsource(OllamaDecisionEngine.decide)
    assert "TradeDecision.model_json_schema()" in decision_src
    assert '"format": "json"' not in decision_src


def test_curriculum_uses_flat_per_kind_schemas_not_a_oneof(monkeypatch):
    """ollama's constrained decoder honours a flat `required` list and `enum`
    values but ignores `const` and `additionalProperties` inside a oneOf -
    measured: a oneOf conditional schema was accepted in 6.4s and then ignored,
    producing task_type 'strategy_generation' and arbitrary parameters. The
    kind-conditional requirement is therefore expressed by generating against the
    chosen kind's flat schema.
    """
    import min_agent.cli as cli
    from min_agent.curriculum import strategy_spec_schema

    seen = []

    class Capturing:
        def __init__(self, **kwargs):
            self.base_url = "http://ollama.invalid"
            self.timeout = 1

        def transport(self, url, payload, timeout):
            seen.append(payload["format"])
            return {"response": '{"kind": "FIXED_SIZE", "why": "w"}'}

    monkeypatch.setattr(cli, "OllamaDecisionEngine", Capturing)
    cli._ollama_curriculum_transport(cli.AgentConfig.from_env())({"current_strategies": []})

    assert seen, "the transport must have called the model"
    for schema in seen:
        assert isinstance(schema, dict)
        assert "oneOf" not in schema and "anyOf" not in schema, "the decoder ignores these"
        assert schema.get("required"), "a flat schema needs a required list to be enforced"

    required = strategy_spec_schema("FIXED_SIZE")["required"]
    for field in ("action", "quantity", "confidence", "kind", "strategy_id", "created_at"):
        assert field in required, f"{field} must be required for FIXED_SIZE"


def test_two_phase_generation_assembles_a_valid_task():
    """Phase A picks the kind, phase B fills that kind's required fields, and the
    result validates without the model ever having to get the conditional right
    in one shot."""
    from min_agent.curriculum import generate_curriculum_task_json
    from min_agent.models import CurriculumTask

    schemas = []

    def call(prompt, schema):
        schemas.append(schema)
        # The kind-selection schema requires [kind, why]; the per-kind spec schema
        # requires the kind's own parameter fields. Dispatch on that, not on the
        # presence of a `kind` property - both schemas have one.
        if "action" not in schema.get("required", []):
            return '{"kind": "FIXED_SIZE", "why": "no exploration"}'
        return json.dumps({
            "strategy_id": "fixed-probe-1", "name": "Probe", "symbols": ["SPY"],
            "max_position_value": 1000, "created_at": "2026-09-28T00:00:00Z",
            "rationale": "smallest admissible probe", "enabled": True,
            "kind": "FIXED_SIZE", "action": "SELL", "quantity": 1, "confidence": 0.6,
        })

    task = CurriculumTask.model_validate_json(
        generate_curriculum_task_json(call=call, prompt_context={"current_strategies": []})
    )

    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec.kind == "FIXED_SIZE"
    assert task.strategy_spec.parameters == {"action": "SELL", "quantity": 1, "confidence": 0.6}
    assert len(schemas) == 2, "kind selection, then spec generation"


def test_two_phase_generation_handles_an_unknown_kind():
    from min_agent.curriculum import generate_curriculum_task_json
    from min_agent.models import CurriculumTask

    def call(prompt, schema):
        if "action" not in schema.get("required", []):
            return '{"kind": "NONSENSE", "why": "w"}'
        return json.dumps({
            "strategy_id": "x", "name": "X", "symbols": ["SPY"], "max_position_value": 100,
            "created_at": "2026-09-28T00:00:00Z", "rationale": "r",
            "action": "BUY", "quantity": 1, "confidence": 0.6,
        })

    task = CurriculumTask.model_validate_json(
        generate_curriculum_task_json(call=call, prompt_context={})
    )
    assert task.strategy_spec.kind == "FIXED_SIZE"


def test_trade_decision_schema_is_passed_to_the_transport():
    from min_agent.models import TradeDecision

    captured = {}

    def transport(url, payload, timeout):
        captured["payload"] = payload
        return {"response": '{"symbol":"SPY","action":"HOLD","quantity":0,"confidence":0.1,"rationale":"r"}'}

    engine = OllamaDecisionEngine(
        base_url="http://ollama.invalid", model="m", gpu_devices="0", transport=transport
    )
    engine.decide({"symbol": "SPY", "last_price": 700.0})

    assert captured["payload"]["format"] == TradeDecision.model_json_schema()


def test_a_risk_driven_hold_is_distinguishable_from_an_ordinary_one():
    """Guardian logs "approved" for a HOLD, so when the model declines because of
    a risk scalar it has quietly become a second risk authority: it can halt
    trading with none of Guardian's auditability, and the journal shows an
    ordinary quiet cycle. That is the same class of invisible-halt defect as the
    heartbeat replay - a halt that does not read as a halt.
    """
    from min_agent.evaluator import DeterministicEvaluator
    from min_agent.models import (
        AccountSnapshot,
        CycleRecord,
        DataSnapshot,
        ExecutionResult,
        GuardianResult,
        TradeDecision,
    )

    def record(hold_reason):
        snapshot = DataSnapshot(
            symbol="SPY", timestamp=NOW, market_open=True, last_price=700.0, source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=300
            ),
        )
        decision = TradeDecision(
            symbol="SPY", action="HOLD", quantity=0, confidence=0.3, rationale="r",
            hold_reason=hold_reason, decision_source="llm",
        )
        return CycleRecord(
            cycle_id=f"c-{hold_reason}-{id(hold_reason)}", snapshot=snapshot, decision=decision,
            guardian=GuardianResult(approved=True, reason="approved"),
            execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold"),
            strategy_id="s1",
        )

    report = DeterministicEvaluator().evaluate(
        [record("risk_limit_near"), record("risk_limit_near"), record("no_signal")]
    )

    assert report.hold_reasons == {"risk_limit_near": 2, "no_signal": 1}
    assert report.action_counts["HOLD"] == 3
    # Guardian approved all of them; only the model knew two were risk halts.
    assert report.guardian_approved == 3


def test_hold_reason_is_validated():
    from min_agent.models import TradeDecision

    assert TradeDecision(
        symbol="SPY", action="HOLD", quantity=0, confidence=0.1, rationale="r", hold_reason="no_signal"
    ).hold_reason == "no_signal"
    with pytest.raises(Exception):
        TradeDecision(
            symbol="SPY", action="HOLD", quantity=0, confidence=0.1, rationale="r",
            hold_reason="because_i_said_so",
        )


def test_the_decision_context_states_the_hard_risk_limits():
    """The prompt tells the model to use hold_reason=risk_limit_near "only when a
    risk figure in the context is what stopped you", but no risk figure was ever in
    the context, so that reason could not be truthful. Stating the limits is not a
    hint to trade: Guardian enforces them downstream regardless."""
    from min_agent.llm_decision import HybridDecisionEngine

    engine = HybridDecisionEngine(
        llm=object(),
        policy_engine=_StubPolicy(),
        risk_limits={"max_position_value": 5000, "max_daily_loss": 500},
    )
    context = engine._context(_basis_snapshot())

    assert context["risk_limits"]["max_daily_loss"] == 500
    assert context["risk_limits"]["max_position_value"] == 5000


def test_the_decision_context_carries_cost_basis_so_profit_is_evaluable():
    """PositionSnapshot has only quantity and market_value, so nothing in the
    context could say whether the position was in profit. The model was asked to
    decide about a position it had no way to evaluate, and instructed to HOLD when
    the context did not support a trade - so it held, correctly, forever."""
    from min_agent.llm_decision import HybridDecisionEngine

    engine = HybridDecisionEngine(
        llm=object(),
        policy_engine=_StubPolicy(),
        cost_basis=lambda: {
            "SPY": {
                "quantity": 23.0,
                "average_price": 742.0313,
                "fill_count": 23,
                "source": "broker_confirmed_fills",
            }
        },
    )
    context = engine._context(_basis_snapshot(last_price=767.0, spy_quantity=23))

    assert context["average_cost"] == 742.0313
    assert context["unrealized_pnl_per_share"] == pytest.approx(24.9687, abs=1e-3)
    assert context["unrealized_pnl"] == pytest.approx(24.9687 * 23, abs=0.01)
    assert context["sellable_quantity"] == 23
    assert context["open_lots"]["SPY"]["source"] == "broker_confirmed_fills"


def test_no_cost_basis_means_no_invented_pnl():
    from min_agent.llm_decision import HybridDecisionEngine

    engine = HybridDecisionEngine(llm=object(), policy_engine=_StubPolicy())
    context = engine._context(_basis_snapshot(last_price=767.0, spy_quantity=23))

    assert "average_cost" not in context
    assert "unrealized_pnl" not in context
    assert context["sellable_quantity"] == 23


class _StubPolicy:
    reflection_memory = None

    def __init__(self):
        self.selector = self
        self.strategy_library = self

    def list(self):
        return []

    def select(self, *_args, **_kwargs):
        return None

    def relevant_lessons(self, _strategy_id):
        return []


def _basis_snapshot(*, last_price=700.0, spy_quantity=0, extra_positions=()):
    from min_agent.models import AccountSnapshot, DataSnapshot, PositionSnapshot

    positions = (
        (PositionSnapshot(symbol="SPY", quantity=float(spy_quantity),
                          market_value=float(spy_quantity) * last_price),)
        if spy_quantity
        else ()
    ) + tuple(extra_positions)
    return DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc),
        market_open=True,
        last_price=last_price,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=50_000, buying_power=50_000,
            portfolio_value=100_000, daily_loss=0,
        ),
        positions=positions,
    )


def test_the_selected_strategy_mandate_is_shown_to_the_model():
    """The model was given an opaque selected_strategy_id and nothing else, so it
    could not tell that the strategy it was asked to act for was a SELL."""
    from min_agent.llm_decision import HybridDecisionEngine

    class _Policy(_StubPolicy):
        # reflection_memory must exist: _context swallows any error from the
        # selector lookup and then decides with no strategy context at all.
        reflection_memory = None

        def __init__(self, spec):
            super().__init__()
            self._spec = spec

        def list(self):
            return [self._spec] if self._spec else []

        def select(self, *_args, **_kwargs):
            return self._spec

    spec = _fixed_size_spec(strategy_id="exit-1", action="SELL", quantity=1)
    engine = HybridDecisionEngine(llm=object(), policy_engine=_Policy(spec))
    context = engine._context(_basis_snapshot(last_price=767.0, spy_quantity=23))

    assert context["selected_strategy_id"] == "exit-1"
    assert context["selected_strategy"]["parameters"]["action"] == "SELL"
    assert context["selected_strategy"]["kind"] == "FIXED_SIZE"


def test_a_decision_is_attributed_to_the_selected_strategy():
    """All 908 journaled cycles carried strategy_id=None: the decision schema never
    asked the model for one, so per-strategy metrics could not accumulate for the
    LLM path and no lifecycle review could be about a decision the model made.
    Attribution is the system's to record."""
    from min_agent.llm_decision import HybridDecisionEngine

    class _Spec:
        strategy_id = "exit-1"
        kind = "FIXED_SIZE"
        parameters = {"action": "SELL", "quantity": 1, "confidence": 0.7}
        max_position_value = 1000.0
        lifecycle = "PROBATION"

    class _Policy(_StubPolicy):
        reflection_memory = None

        def list(self):
            return [_Spec()]

        def select(self, *_args, **_kwargs):
            return _Spec()

    class _LLM:
        def decide(self, _context):
            from min_agent.models import TradeDecision

            return TradeDecision(
                symbol="SPY", action="SELL", quantity=1, confidence=0.7, rationale="r"
            )

    engine = HybridDecisionEngine(llm=_LLM(), policy_engine=_Policy())
    decision = engine.decide_snapshot(_basis_snapshot(last_price=767.0, spy_quantity=23))

    assert decision.strategy_id == "exit-1"
    assert decision.decision_source == "llm"


def _fixed_size_spec(*, strategy_id, action, quantity=1):
    from datetime import datetime, timezone

    from min_agent.models import StrategySpec

    return StrategySpec(
        strategy_id=strategy_id, name=strategy_id, kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": action, "quantity": quantity, "confidence": 0.7},
        max_position_value=1000, enabled=True, lifecycle="PROBATION",
        created_at=datetime.now(tz=timezone.utc), rationale="t",
    )


def test_the_context_states_that_the_exposure_limit_already_blocks_buying():
    """The model was told the cap but never the total it is measured against, so it
    could not know that this account holds ~$76k against a $20k cap and that every
    BUY would be rejected by Guardian. It kept proposing BUY and Guardian kept
    answering "total exposure exceeds hard limit" - 49 BUY decisions, 0 fills.

    This is arithmetic on the broker positions already in the context, not a hint to
    trade, and it changes nothing about enforcement: Guardian still decides.
    """
    from min_agent.llm_decision import HybridDecisionEngine

    engine = HybridDecisionEngine(
        llm=object(),
        policy_engine=_StubPolicy(),
        risk_limits={"max_total_exposure": 20000.0},
    )
    from min_agent.models import PositionSnapshot

    # The live account: 23 SPY plus ~$59k of positions outside the allowlist, so
    # total exposure is far past the $20k cap and every BUY is rejected.
    snapshot = _basis_snapshot(
        last_price=767.0,
        spy_quantity=23,
        extra_positions=(
            PositionSnapshot(symbol="TLT", quantity=226.0, market_value=17745.52),
        ),
    )
    context = engine._context(snapshot)

    exposure = context["exposure"]
    assert exposure["max_total_exposure"] == 20000.0
    assert exposure["account_total_value"] > 20000.0
    assert exposure["buy_blocked_by_exposure_limit"] is True
    assert exposure["position_in_this_symbol"] == 23
    assert exposure["position_count"] == 2


def test_a_flat_account_is_not_reported_as_exposure_blocked():
    from min_agent.llm_decision import HybridDecisionEngine

    engine = HybridDecisionEngine(
        llm=object(),
        policy_engine=_StubPolicy(),
        risk_limits={"max_total_exposure": 20000.0, "allowlist": ["SPY"]},
    )
    context = engine._context(_basis_snapshot(last_price=700.0, spy_quantity=0))

    exposure = context["exposure"]
    assert exposure["account_total_value"] == 0.0
    assert exposure["agent_book_value"] == 0.0
    assert exposure["buy_blocked_by_exposure_limit"] is False
    assert "position_in_this_symbol" not in exposure


def _fill_journal(tmp_path, fills):
    """A journal whose ORDER_FILL_CONFIRMED events are enough for the real
    `confirmed_fill_activities` to rebuild activities, so `_agent_open_lots`
    executes rather than being handed a pre-made cost basis."""
    from min_agent.journal import JsonlJournal
    from min_agent.models import JournalEvent

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for i, (coid, side, quantity, price) in enumerate(fills):
        journal.append_event(JournalEvent(
            event_id=f"f{i}", event_type="ORDER_FILL_CONFIRMED",
            timestamp=TS + timedelta(seconds=i * 60), status="SUCCESS",
            message="filled", payload={
                "client_order_id": coid, "order_id": f"o{i}", "symbol": "SPY",
                "side": side, "filled_quantity": float(quantity),
                "filled_avg_price": float(price), "strategy_id": "s",
            },
        ))
    return journal


def test_selling_consumes_the_lots_it_sold(tmp_path):
    """`open_lots` counted every fill, so a sell *increased* the reported holding.

    `_agent_open_lots` appended each confirmed fill to a list and then summed the
    list, with no consumption - so `quantity` was buys + sells. On this project's
    own journal that reported 116 SPY shares against a broker holding of 17, in
    the same context that separately said `positions` 17 and `sellable_quantity` 17.
    The model could not trust the one number that would have told it whether the
    position was up or down.

    Buys and sells are now netted FIFO: 10 bought then 4 sold leaves 6, and the
    average is over those 6 rather than over all 14 fills.
    """
    from min_agent.cli import _agent_open_lots

    journal = _fill_journal(tmp_path, [
        ("c1", "BUY", 10, 100.0),
        ("c2", "SELL", 4, 120.0),
    ])
    lots = _agent_open_lots(journal)

    assert lots["SPY"]["quantity"] == 6.0
    assert lots["SPY"]["average_price"] == 100.0, (
        "the average must be over the shares still held, not over the sells too"
    )


def test_a_sell_beyond_the_visible_buys_is_not_reported_as_a_whole_average(tmp_path):
    """When sells outrun the buys this journal can see, there is no average to give.

    29 of the shares sold on this account belonged to the owner, so no cost in the
    journal prices them. Averaging buys together with those sells produced a number
    that meant nothing. So the unpriced part is named and the average is omitted -
    the consumer already skips a lot without one, and a missing average is honest
    where a fabricated one is not.

    20 bought at 100, then 25 sold: 20 are consumed FIFO, 5 liquidated shares this
    journal never saw bought, and nothing attributable is left, so the symbol is
    dropped entirely rather than reported at a made-up price.
    """
    from min_agent.cli import _agent_open_lots

    journal = _fill_journal(tmp_path, [
        ("c1", "BUY", 20, 100.0),
        ("c2", "SELL", 25, 120.0),
    ])
    assert "SPY" not in _agent_open_lots(journal), (
        "nothing attributable remains, so no lot may be reported at all"
    )

    # 20 bought, 22 sold: 2 survive FIFO and 2 were unpriced. The surviving 2 have
    # a real cost, but the position as a whole is not fully priced, so the flag is
    # what tells a reader the average covers only part of it.
    # The shape this account actually has: the agent liquidated shares it never
    # bought here, then kept buying. The 5 bought after the oversized sell are the
    # only ones with an attributable cost, and the 15 unpriced shares are named
    # rather than folded into an average.
    partial = _fill_journal(tmp_path / "partial", [
        ("c1", "BUY", 10, 100.0),
        ("c2", "SELL", 25, 120.0),
        ("c3", "BUY", 5, 140.0),
    ])
    lot = _agent_open_lots(partial)["SPY"]
    assert lot["quantity"] == 5.0
    assert lot["cost_basis_incomplete"] is True
    assert lot["unpriced_sold_quantity"] == 15.0
    assert "average_price" not in lot, (
        "5 shares do have a cost, but reporting their average as the position's "
        "would be the same defect in miniature - a partial figure presented as the "
        "whole. `_context` already skips a lot without an average, so the model "
        "gets no `average_cost` rather than one it cannot trust."
    )


def test_open_lots_can_never_report_more_than_a_sell_left_behind(tmp_path):
    """The regression in one assertion: the old sum grew with every sell."""
    from min_agent.cli import _agent_open_lots

    held = 10
    for extra_sell in (0, 1, 3):
        journal = _fill_journal(tmp_path / f"s{extra_sell}", [
            ("c1", "BUY", 10, 100.0),
            ("c2", "SELL", 1 + extra_sell, 110.0),
        ])
        reported = float(_agent_open_lots(journal)["SPY"]["quantity"])
        assert reported == held - (1 + extra_sell)
        assert reported <= held


def _limits_engine(cap, total_cap=None):
    from min_agent.llm_decision import HybridDecisionEngine

    return HybridDecisionEngine(
        llm=object(),
        policy_engine=_StubPolicy(),
        risk_limits={
            "max_position_value": cap,
            "max_total_exposure": total_cap,
            "max_daily_loss": 500.0,
            "max_trades_per_day": 10,
            "min_confidence": 0.5,
            "allowlist": ["SPY"],
        },
        cost_basis=dict,
    )


def test_the_context_states_the_position_limit_rather_than_making_the_model_divide():
    """`max_position_value` and the position's value were both in the context, and
    the model was expected to divide one by the other.

    One recorded rationale did exactly that and correctly concluded it could not
    add; every other one reasoned about the strategy's price signal and never
    mentioned the limit. Ninety percent HOLDs, most with `no_signal` as the stated
    reason, while the single fact that would have justified holding - already 2.6x
    over the cap - was left as arithmetic.

    So it is stated. The numbers here are the ones this project's own state produces:
    17 shares at 769.72 against a $5,000 cap.
    """
    engine = _limits_engine(5_000)
    snapshot = _basis_snapshot(last_price=769.72, spy_quantity=17)
    state = engine._position_limit_state(snapshot, {"SPY": {"quantity": 17.0}})

    assert state["max_position_value"] == 5_000
    assert state["your_position_value"] == pytest.approx(17 * 769.72, abs=0.01)
    assert state["position_headroom"] < 0
    assert state["over_position_limit"] is True
    assert state["buy_blocked_by_position_limit"] is True
    assert state["shares_you_may_buy"] == 0


def test_shares_you_may_buy_is_exactly_what_guardian_allows():
    """The one property worth pinning, because two implementations of one rule is
    what this project keeps having to undo.

    `max_position_value` is enforced twice - once in Guardian, once in the context
    - so the risk is not that one blocks and the other allows; it is that the two
    answer slightly different questions and the model is guided by the wrong one.

    Which is what a boolean does. `buy_blocked_by_position_limit` tests a *one-share*
    buy, because the model has not chosen a size yet; Guardian tests whatever size
    was actually ordered. At 6 shares held against a $5,000 cap at $700, a 1-share buy
    fits (4900) and a 3-share buy does not (6300), so the boolean says "fine" for an
    order Guardian refuses. Asserting agreement on the boolean therefore proves
    almost nothing.

    `shares_you_may_buy` is the quantity-aware fact, and it must equal the largest
    order Guardian approves - across holdings from empty to six times the cap.
    """
    from min_agent.guardian import Guardian
    from min_agent.models import TradeDecision

    cap = 5_000
    price = 700.0
    guardian = Guardian(
        allowlist={"SPY"}, max_position_value=cap, max_daily_loss=500,
        max_total_exposure=None, min_confidence=0.5,
    )
    engine = _limits_engine(cap)

    def guardian_allows(snapshot, held, quantity):
        if quantity == 0:
            return True
        return guardian.review(
            TradeDecision(symbol="SPY", action="BUY", quantity=quantity, confidence=0.9,
                          rationale="probe", strategy_id="probe"),
            snapshot, mode="paper", trades_today=0, now=snapshot.timestamp,
            agent_position_quantity=float(held),
        ).approved

    for held in (0, 1, 5, 6, 7, 8, 17, 30):
        snapshot = _basis_snapshot(last_price=price, spy_quantity=held)
        state = engine._position_limit_state(snapshot, {"SPY": {"quantity": float(held)}})

        largest = 0
        for quantity in range(1, 30):
            if not guardian_allows(snapshot, held, quantity):
                break
            largest = quantity

        assert state["shares_you_may_buy"] == largest, (
            f"held={held}: context says it may buy {state['shares_you_may_buy']}, "
            f"Guardian allows at most {largest}"
        )
        # And the boolean means "no buy of any size fits", which is the only claim
        # that is true before a size is chosen.
        assert state["buy_blocked_by_position_limit"] == (largest == 0)


def test_an_unknown_agent_holding_falls_back_to_the_account_the_same_way_guardian_does():
    """Both sides fall back the same way, or they disagree at exactly the moment the
    journal is unreadable - which is when a wrong answer costs the most.

    Guardian uses the account's figure for the symbol so the cap binds harder. With
    no `open_lots` entry the context must reach the same number, not the smaller
    agent-only one.
    """
    from min_agent.guardian import Guardian
    from min_agent.models import TradeDecision

    cap = 5_000
    snapshot = _basis_snapshot(last_price=700.0, spy_quantity=17)
    state = _limits_engine(cap)._position_limit_state(snapshot, {})

    assert state["your_position_value"] == pytest.approx(17 * 700, abs=0.01)
    assert state["buy_blocked_by_position_limit"] is True

    guardian = Guardian(
        allowlist={"SPY"}, max_position_value=cap, max_daily_loss=500,
        max_total_exposure=None, min_confidence=0.5,
    )
    refused = not guardian.review(
        TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.9,
                      rationale="p", strategy_id="p"),
        snapshot, mode="paper", trades_today=0, now=snapshot.timestamp,
        agent_position_quantity=None,
    ).approved
    assert refused == state["buy_blocked_by_position_limit"]


# --- the rule is the default; the model overrides it only with a reason --------


def _llm_says(action, reason=None):
    from min_agent.models import TradeDecision

    return ScriptedLLM(TradeDecision(
        symbol="SPY", action=action, quantity=0 if action == "HOLD" else 1, confidence=0.6,
        rationale="r", override_reason=reason,
    ))


def test_the_model_is_shown_the_rule_s_decision(tmp_path):
    llm = _llm_says("BUY")
    engine, _ = _engine(tmp_path, llm)  # s1 is a FIXED_SIZE BUY of 1
    engine.decide_snapshot(_snapshot())
    assert llm.calls[0]["rule_decision"] == {"action": "BUY", "quantity": 1}


def test_agreeing_with_the_rule_is_recorded_as_a_pair(tmp_path):
    engine, _ = _engine(tmp_path, _llm_says("BUY"))
    decision = engine.decide_snapshot(_snapshot())
    assert (decision.action, decision.rule_action, decision.decision_source) == ("BUY", "BUY", "llm")
    assert decision.override_reason is None


def test_an_override_with_a_reason_is_taken_and_both_actions_are_recorded(tmp_path):
    engine, _ = _engine(tmp_path, _llm_says("HOLD", "the position cap leaves no room to buy"))
    decision = engine.decide_snapshot(_snapshot())
    assert (decision.action, decision.rule_action, decision.decision_source) == ("HOLD", "BUY", "llm")
    assert decision.override_reason == "the position cap leaves no room to buy"


def test_an_override_without_a_reason_is_discarded_for_the_rule(tmp_path):
    """112 of 113 recorded disagreements were the model declining the rule's trade, unexplained."""
    engine, _ = _engine(tmp_path, _llm_says("HOLD", "  "))
    decision = engine.decide_snapshot(_snapshot())
    assert (decision.action, decision.quantity, decision.rule_action) == ("BUY", 1, "BUY")
    assert decision.decision_source == "policy_engine"
    assert "without an override_reason" in decision.rationale


def test_the_fallback_records_its_own_rule_as_the_rule_action(tmp_path):
    engine, _ = _engine(tmp_path, ScriptedLLM(error=TimeoutError("slow")))
    decision = engine.decide_snapshot(_snapshot())
    assert decision.decision_source == "fallback_policy_engine"
    assert decision.rule_action == decision.action == "BUY"
