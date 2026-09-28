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

from datetime import datetime, timezone

import pytest

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


def test_curriculum_prompt_offers_no_copyable_price_example(monkeypatch):
    """A literal 100.0 reference price in the prompt was copied verbatim into
    nine admitted strategies, all anchored ~6x below the real SPY.

    Asserted against the prompt that is actually sent, not against the source,
    so a comment cannot mask the problem and a real example cannot hide in one.
    """
    import min_agent.cli as cli

    captured = {}

    class CapturingEngine:
        def __init__(self, **kwargs):
            self.base_url = "http://ollama.invalid"
            self.timeout = 1
            self.model = "m"

        def transport(self, url, payload, timeout):
            captured["prompt"] = payload["prompt"]
            return {"response": "{}"}

    monkeypatch.setattr(cli, "OllamaDecisionEngine", CapturingEngine)
    config = cli.AgentConfig.from_env()
    cli._ollama_curriculum_transport(config)({"risk_limits": {}, "current_strategies": []})

    prompt = captured["prompt"]
    assert '"reference_price":100.0' not in prompt
    assert "2026-06-10T00:00:00Z" not in prompt
    assert "must be taken from the current market price" in prompt.lower()
    # The schema description may still name the key; that is not a value.
    assert "reference_price" in prompt


# --- admission rejects a fabricated reference price --------------------------


def _admission(tmp_path, prices):
    guardian = Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500)
    library = StrategyLibrary(tmp_path / "strategies")
    return StrategyAdmission(guardian=guardian, strategy_library=library, market_prices=prices)


def test_reference_price_from_the_prompt_example_is_rejected(tmp_path):
    admission = _admission(tmp_path, {"SPY": REAL_SPY_PRICE})
    bad = _spec("bad", kind="TREND_FOLLOW", reference_price=100.0, threshold_pct=0.02)

    result = admission.admit(bad)

    assert result.accepted is False
    assert "reference_price" in result.reason
    assert "744.27" in result.reason


def test_realistic_reference_price_is_accepted(tmp_path):
    admission = _admission(tmp_path, {"SPY": REAL_SPY_PRICE})
    good = _spec("good", kind="TREND_FOLLOW", reference_price=740.0, threshold_pct=0.02)

    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("base", kind="HOLD_BASELINE", lifecycle="BASELINE"))
    library.save(_spec("fixed", kind="FIXED_SIZE"))
    admission = StrategyAdmission(
        guardian=Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500),
        strategy_library=library,
        market_prices={"SPY": REAL_SPY_PRICE},
    )

    assert admission.admit(good).accepted is True


def test_reference_check_is_skipped_when_no_price_is_known(tmp_path):
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
    """format="json" only asks for *some* JSON. The model repeatedly omitted
    required fields, so every curriculum call failed validation and fell back.

    Measured against deepseek-r1:8b on the curriculum task, constraining to the
    pydantic schema was not only correct but 5x faster (11.7s vs 54.4s) and
    produced 4x less output (901 vs 6993 chars) - the model no longer has to
    search for a valid shape.
    """
    import inspect

    import min_agent.cli as cli
    from min_agent.llm_decision import OllamaDecisionEngine

    curriculum = inspect.getsource(cli._ollama_curriculum_transport)
    assert 'CurriculumTask.model_json_schema()' in curriculum
    assert '"format": "json"' not in curriculum, "plain 'json' mode is not a schema"

    decision = inspect.getsource(OllamaDecisionEngine.decide)
    assert "TradeDecision.model_json_schema()" in decision
    assert '"format": "json"' not in decision


def test_curriculum_schema_is_passed_to_the_transport():
    """A live-shape test of the payload, with a fake engine."""
    import json

    import min_agent.cli as cli
    from min_agent.config import AgentConfig
    from min_agent.models import CurriculumTask

    captured = {}

    class Capturing:
        def __init__(self, **kwargs):
            self.base_url = "http://ollama.invalid"
            self.timeout = 1

        def transport(self, url, payload, timeout):
            captured["payload"] = payload
            return {"response": "{}"}

    original = cli.OllamaDecisionEngine
    cli.OllamaDecisionEngine = Capturing
    try:
        config = AgentConfig.from_env()
        cli._ollama_curriculum_transport(config)({})
    finally:
        cli.OllamaDecisionEngine = original

    fmt = captured["payload"]["format"]
    assert isinstance(fmt, dict), "format must be a JSON schema object"
    assert fmt == CurriculumTask.model_json_schema()
    assert "properties" in fmt and "required" in fmt
    json.dumps(fmt)  # must be serialisable for the wire


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
