import pytest

from min_agent.llm_decision import OllamaDecisionEngine, parse_decision_json
from min_agent.models import TradeDecision


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
    from min_agent.llm_decision import OllamaDecisionEngine

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
    from min_agent.llm_decision import OllamaDecisionEngine

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
    from min_agent.llm_decision import OllamaDecisionEngine

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


def test_every_generation_call_site_uses_the_same_window():
    """Both ollama call sites must pin num_ctx, not just the decision engine.

    The curriculum transport in cli.py builds its own options dict. Leaving it at
    the server default meant the curriculum paid the 32k KV-cache allocation on
    every call, and it is the path that recorded the 120s read timeouts - so
    fixing only the decision engine would have left the actual timeout site
    untouched while the tests all passed.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in sorted((root / "src" / "min_agent").glob("*.py")):
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
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
            lines = path.read_text().splitlines()
            start = 0
            for back in range(line_no - 1, -1, -1):
                stripped = lines[back]
                if stripped.startswith("    def ") or stripped.startswith("def "):
                    start = back
                    break
            if "num_ctx" not in "\n".join(lines[start:line_no]):
                offenders.append(f"{path.name}:{line_no}")
    assert not offenders, (
        "these /api/generate call sites do not pin num_ctx: " + ", ".join(offenders)
    )
