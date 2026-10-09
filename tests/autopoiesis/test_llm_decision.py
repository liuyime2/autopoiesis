import inspect
from datetime import datetime, timezone

import pytest

from autopoiesis import llm_decision
from autopoiesis.llm_decision import OllamaDecisionEngine, parse_decision_json
from autopoiesis.models import TradeDecision


def test_parse_decision_json_accepts_json_inside_reasoning_text():
    text = """think first
    {"symbol":"spy","action":"buy","quantity":2,"confidence":0.61,"rationale":"trend continuation"}
    """

    decision = parse_decision_json(text)

    assert decision == TradeDecision(
        symbol="SPY",
        action="BUY",
        quantity=2,
        confidence=0.61,
        rationale="trend continuation",
    )


def test_parse_decision_json_rejects_non_json_response():
    with pytest.raises(ValueError):
        parse_decision_json("buy spy because it looks good")


def test_ollama_engine_uses_configured_model_and_gpu_devices():
    calls = []

    def transport(url, payload, timeout):
        calls.append((url, payload, timeout))
        return {"response": '{"symbol":"SPY","action":"HOLD","quantity":0,"confidence":0.2,"rationale":"wait"}'}

    engine = OllamaDecisionEngine(
        base_url="http://localhost:11434",
        model="deepseek-r1:8b",
        gpu_devices="2,3",
        transport=transport,
    )

    decision = engine.decide({"symbol": "SPY", "last_price": 100})

    assert decision.action == "HOLD"
    assert calls[0][0] == "http://localhost:11434/api/generate"
    assert calls[0][1]["model"] == "deepseek-r1:8b"
    assert engine.launch_environment()["CUDA_VISIBLE_DEVICES"] == "2,3"


# --------------------------------------------------------------------------
# context window and reasoning: measured, not assumed
# --------------------------------------------------------------------------

def test_num_ctx_leaves_headroom_for_the_largest_real_context():
    """num_ctx=4096 must fit the largest context the engine can actually build.

    A window cut for speed that truncates a real decision mid-JSON is a
    correctness bug wearing a speedup's clothes. The engine's context carries the
    account, positions, open orders and the *full* selected strategy spec; the
    largest measured prompt is ~253 tokens in and the longest measured answer is
    814 tokens out, so 4096 leaves 3.5x headroom.
    """
    from autopoiesis.llm_decision import OllamaDecisionEngine

    engine = OllamaDecisionEngine(
        base_url="http://x", model="qwen3.8:27b", gpu_devices="0",
    )
    captured: dict = {}

    def transport(url, payload, timeout):
        captured.update(payload)
        return {"response": "{}"}

    engine.transport = transport
    try:
        engine.decide({
            "symbol": "SPY", "positions": [{"symbol": "SPY", "qty": 10}],
            "open_orders": [{"symbol": "SPY", "side": "buy", "qty": 1}],
            "selected_strategy": {
                "strategy_id": "fixed-size-buy-001", "kind": "FIXED_SIZE",
                "parameters": {"a": 1}, "max_position_value": 5000.0,
                "lifecycle": "PROBATION",
            },
            "paper_only": True,
        })
    except Exception:
        pass  # parsing an empty stub is not what this test is about

    window = captured["options"]["num_ctx"]
    prompt_tokens = len(captured["prompt"]) // 4
    longest_measured_answer = 814
    assert prompt_tokens + longest_measured_answer < window, (
        f"window {window} cannot hold a {prompt_tokens}-token prompt plus a "
        f"{longest_measured_answer}-token answer"
    )


def test_num_ctx_is_not_left_at_the_server_default():
    """The server default is 32768. KV cache is allocated for the whole window
    even when unused, which measured 17.8s at 32768 against 4.6s at 4096 for the
    identical call. This is the largest single latency lever in the request and it
    is invisible unless it is asserted."""
    from autopoiesis.llm_decision import OllamaDecisionEngine

    engine = OllamaDecisionEngine(
        base_url="http://x", model="qwen3.8:27b", gpu_devices="0",
    )
    captured: dict = {}
    engine.transport = lambda url, payload, timeout: (
        captured.update(payload) or {"response": "{}"}
    )
    try:
        engine.decide({"symbol": "SPY"})
    except Exception:
        pass

    assert captured["options"]["num_ctx"] == 4096


def test_temperature_stays_low():
    """A decision engine that samples at temperature 1 produces a different trade
    on the same context, so replay stops being deterministic. 0.1 is the value the
    determinism guarantee was written against."""
    from autopoiesis.llm_decision import OllamaDecisionEngine

    engine = OllamaDecisionEngine(
        base_url="http://x", model="qwen3.8:27b", gpu_devices="0",
    )
    captured: dict = {}
    engine.transport = lambda url, payload, timeout: (
        captured.update(payload) or {"response": "{}"}
    )
    try:
        engine.decide({"symbol": "SPY"})
    except Exception:
        pass

    assert captured["options"]["temperature"] == 0.1


def test_every_generation_call_site_pins_a_named_window():
    """Every ollama call site must pin num_ctx, and must not hard-code the number.

    This check used to assert that all call sites used the *same* window, and it
    passed while the curriculum was returning empty bodies. "Both call sites pin
    a window" was never the property that mattered; "both call sites can finish
    their own prompt and answer" was. The two workloads differ by 13x in prompt
    size (253 vs 3441 measured tokens), so requiring them to agree guaranteed
    that one of them was wrong, and the tests certified the mistake.

    Now each site must reference a named constant, so a value lives in one place
    with its measurements attached and a call site cannot quietly drift from it.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    literals: list[str] = []
    for path in sorted((root / "src" / "autopoiesis").glob("*.py")):
        lines = path.read_text().splitlines()
        for line_no, line in enumerate(lines, 1):
            # A comment naming the endpoint is documentation, not a call site. The
            # first version of this check flagged its own explanatory comment,
            # which is how a check earns a reputation for being wrong.
            if line.lstrip().startswith("#"):
                continue
            if "api/generate" not in line:
                continue
            # Walk back to the start of the enclosing function, not a fixed number
            # of lines: a comment explaining the value can be longer than any fixed
            # window, and a check that silently stops covering a call site when a
            # comment grows is worse than no check.
            start = 0
            for back in range(line_no - 1, -1, -1):
                stripped = lines[back]
                if stripped.startswith("    def ") or stripped.startswith("def "):
                    start = back
                    break
            body = "\n".join(lines[start:line_no])
            if "num_ctx" not in body:
                offenders.append(f"{path.name}:{line_no} pins no num_ctx")
            elif not re.search(r"num_ctx\"?\s*:\s*[A-Z_]+NUM_CTX", body):
                literals.append(f"{path.name}:{line_no}")
    assert not offenders, "call sites that do not pin num_ctx: " + ", ".join(offenders)
    assert not literals, (
        "call sites that hard-code the window instead of naming a constant: "
        + ", ".join(literals)
    )


def test_the_two_windows_are_not_assumed_equal():
    """A shared constant is a coincidence waiting to be relied on.

    The decision prompt measures ~253 tokens and the curriculum prompt 3441. If
    these ever converged, the honest move would be to notice and re-measure, not
    to have the equality silently hold because one number was typed twice.
    """
    from autopoiesis.llm_decision import CURRICULUM_NUM_CTX, DECISION_NUM_CTX

    assert DECISION_NUM_CTX == 4096
    assert CURRICULUM_NUM_CTX == 8192
    assert CURRICULUM_NUM_CTX > DECISION_NUM_CTX, (
        "the curriculum prompt is far larger than the decision prompt; if this "
        "ever fails, the measurements changed and the constants need re-deriving"
    )


def test_curriculum_window_fits_its_own_prompt_and_answer():
    """The curriculum window must cover the real prompt, measured not guessed.

    The decision engine's 4096 was chosen from a ~253-token prompt. Reusing it
    for the curriculum left 655 tokens of room for a reply that needs ~1200, and
    the model returned done_reason=length with an empty body - logged as
    "constrained response contained no JSON object". This rebuilds the actual
    worst-case context from the real strategy library and requires the window to
    cover prompt + measured answer with room left over.

    Token counts come from ollama's own prompt_eval_count when the model is
    reachable. The offline fallback deliberately over-estimates tokens per
    character, because the estimate that caused this bug (chars/3.6) was 19% low.
    """
    import glob
    import json
    import pathlib

    import requests

    from autopoiesis.curriculum import (
        CurriculumTask,
        StructuredCurriculumAgent,
        prompt_context_text,
    )
    from autopoiesis.llm_decision import CURRICULUM_NUM_CTX
    from autopoiesis.models import ReflectionRecord, StrategySpec

    # The worst case is constructed rather than read from the live strategy library.
    # The library lives under runtime/, which is gitignored, so on a fresh clone it does
    # not exist and this test - the one that pins the curriculum's context window - failed
    # with "no strategy library to measure against; the test would be vacuous". A test
    # that protects a documented regression cannot depend on state that a fresh clone
    # does not have. The synthetic spec below carries the widest parameter set the
    # curriculum can emit, which is what the window has to cover.
    specs: list[StrategySpec] = [
        # One per kind, each with the full permitted parameter set and the widest
        # symbol list the curriculum emits. Parameter names are validated per kind by
        # StrategySpec, so these match the three shapes the library actually contains.
        StrategySpec(
            strategy_id="worst-case-trend-follow",
            name="worst case trend follow",
            kind="TREND_FOLLOW",
            symbols=("SPY", "QQQ", "IWM", "TLT", "GLD", "XLE", "XLB", "XLF"),
            parameters={
                "quantity": 100, "confidence": 0.55,
                "reference_price": 700.0, "threshold_pct": 0.20,
            },
            max_position_value=5000.0,
            rationale="synthetic widest-case spec so this test does not read live state",
            lifecycle="PROBATION",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        StrategySpec(
            strategy_id="worst-case-fixed-size",
            name="worst case fixed size",
            kind="FIXED_SIZE",
            symbols=("SPY", "QQQ", "IWM", "TLT", "GLD", "XLE", "XLB", "XLF"),
            parameters={"action": "BUY", "quantity": 100, "confidence": 0.55},
            max_position_value=5000.0,
            rationale="synthetic widest-case spec so this test does not read live state",
            lifecycle="PROBATION",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]

    reflection = ReflectionRecord.model_validate({
        "summary": "50 cycles", "window_cycles": 50, "submitted_orders": 10,
        "rejected_orders": 9, "skipped_orders": 31, "error_count": 0,
        "evaluation": {"pnl_evidence": "broker_strategy_closed_lot_pnl_verified"},
        "generated_at": "2026-09-29T23:09:00Z", "cycle_ids": [],
    })
    # A 50-cycle window with the fields the daemon really sends, including the
    # cycle_ids the daemon keeps for provenance and the model must not pay for.
    summary = {
        "window_cycles": 50, "market_open_cycles": 50,
        "strategy_ids": ["fixed-size-buy-001", "trend-follow-sell-002"],
        "action_counts": {"HOLD": 31, "BUY": 19}, "submitted_orders": 10,
        "rejected_orders": 9, "skipped_orders": 31, "buy_sell_intent_count": 19,
        "submitted_or_rejected_order_count": 19, "intended_notional": 14520.0,
        "filled_quantity": 0.0, "data_sources": {"alpaca": 50},
        "latest_prices_by_symbol": {"SPY": 764.18},
        "cycle_ids": [f"e475e3c1-a0b8-4376-aa41-2925976adac{i}" for i in range(50)],
        "no_exploration": False, "paper_only": True,
    }
    captured: dict[str, object] = {}
    agent = StructuredCurriculumAgent(
        transport=lambda ctx: (
            captured.__setitem__("ctx", ctx),
            json.dumps({"task_id": "t", "task_type": "EVALUATE", "summary": "s",
                        "strategy_spec": None, "parameters": {}, "rationale": "r"}),
        )[1]
    )
    agent.propose(
        reflection=reflection, current_strategies=specs,
        recent_exploration_summary=summary, guardian_max_position_value=5000.0,
        open_positions=[{"symbol": "SPY", "qty": 10, "avg_entry_price": 762.0}],
        last_price=764.18, symbol="SPY",
    )
    context = captured["ctx"]
    text = prompt_context_text(context)

    # The provenance the model must not receive.
    assert "cycle_ids" not in context["recent_exploration_summary"], (
        "50 provenance UUIDs are daemon-side lineage, not model input"
    )
    assert summary["cycle_ids"], "the daemon must still hold what the model is spared"

    measured_answer = 1493  # longest measured curriculum answer, at 12288
    tokens: int
    try:
        probe = requests.post(
            "http://127.0.0.1:11434/api/generate",
            json={"model": "qwen3.8:27b", "prompt": text, "stream": False,
                  "options": {"num_predict": 1}},
            timeout=300,
        ).json()
        tokens = int(probe.get("prompt_eval_count") or 0)
    except Exception:
        tokens = 0
    if tokens <= 0:
        # chars/3.0, not chars/3.6: JSON punctuation and short keys are cheap
        # per character, and the 3.6 estimate was 19% low in exactly the way that
        # hid this bug.
        tokens = int(len(text) / 3.0)
    assert tokens + measured_answer < CURRICULUM_NUM_CTX, (
        f"curriculum prompt measures ~{tokens} tokens and the answer needs "
        f"{measured_answer}; window {CURRICULUM_NUM_CTX} would truncate"
    )


# --------------------------------------------------------------------------
# the instruction is data, and it must cover what the validator enforces
# --------------------------------------------------------------------------

def test_the_decision_instruction_lives_outside_the_method_body():
    """There has to be a surface representing what the system tells the model.

    It was a string literal inside `decide()`, which meant the whole of the system's
    self-representation could only be audited by reading a method body, and could only
    be corrected by a code change and a daemon restart.
    """
    from autopoiesis.llm_decision import DECISION_INSTRUCTION

    assert isinstance(DECISION_INSTRUCTION, str) and DECISION_INSTRUCTION.strip()
    source = inspect.getsource(llm_decision.OllamaDecisionEngine.decide)
    assert "DECISION_INSTRUCTION" in source
    # The literal must not be duplicated back inside the method.
    assert "You are a paper-trading decision engine" not in source


def test_the_instruction_states_every_constraint_the_validator_enforces():
    """The measured cost of a missing line here was ~20% of the decision history.

    92 cycles carried an out-of-range `confidence` and 28 a HOLD with non-zero quantity;
    the JSON schema carries `minimum: 0, maximum: 1`, so the bound was available to
    constrained decoding and still did not hold - a numeric range does not survive into
    the decoding grammar, which leaves these words as the only enforcement point.

    So this is a correspondence test, not a string test: every enum the schema declares
    must appear in the instruction, and the confidence bound must be stated in words.
    A field added to the schema without a line here now fails the build.
    """
    from autopoiesis.llm_decision import DECISION_INSTRUCTION

    instruction = DECISION_INSTRUCTION
    schema = TradeDecision.model_json_schema()

    for value in schema["properties"]["action"]["enum"]:
        assert value in instruction, f"action {value} is not in the instruction"

    for value in schema["properties"]["hold_reason"]["anyOf"][0]["enum"]:
        assert value in instruction, f"hold_reason {value} is not in the instruction"

    for field in schema["required"]:
        assert field in instruction, f"required field {field} is not in the instruction"

    # The range the schema declares numerically, and the grammar silently drops.
    bounds = schema["properties"]["confidence"]
    assert bounds["minimum"] == 0 and bounds["maximum"] == 1
    assert "confidence" in instruction
    lowered = instruction.lower()
    assert "0" in lowered and "1" in lowered, "the confidence range is never stated"

    # The quantity rule, which the model broke 28 times despite it being stated.
    assert "quantity must be" in lowered
    assert "hold" in lowered
