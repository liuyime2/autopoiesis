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
