"""An empty decision body must name its cause, not only report that it had none.

Measured 2026-10-16: 43 fallbacks on 10-15 and 10-16 carried exactly one cause -
"ValueError: LLM response did not contain a JSON object" - and nothing on record said why
the model returned no object. `done_reason` and `error` answer it, and both were discarded
by `str(response.get("response", ""))` before anything could read them.

This is the same defect the curriculum had one level up, and it is why the plan's P2 step
one - "measure the real failing prompt and thinking tokens" - could not be done: nothing
recorded them.

Usage: pytest tests/autopoiesis/test_decision_empty_body_cause.py -q
"""

from __future__ import annotations

import pytest

from autopoiesis.llm_decision import DECISION_NUM_CTX, OllamaDecisionEngine


def _engine(body, captured=None):
    engine = OllamaDecisionEngine.__new__(OllamaDecisionEngine)
    engine.base_url = "http://127.0.0.1:11434"
    engine.model = "synthetic"
    engine.timeout = 30
    engine.gpu_devices = "0"

    def transport(url, payload, timeout):
        if captured is not None:
            captured.append(payload)
        return body

    engine.transport = transport
    return engine


def _run(body):
    """Drive the production `decide`, not a copy of it.

    An earlier version of this file reimplemented the response handling, which would have
    passed whatever it was written to do. `decide` takes the context dict, so a synthetic
    one is enough to reach the transport and nothing here duplicates the code under test.
    """
    engine = _engine(body)
    return engine.decide({"symbol": "SPY", "last_price": 770.0})


def test_a_length_finished_empty_body_names_context_exhaustion():
    """The case DECISION_NUM_CTX is sized against, and the one that was invisible."""
    with pytest.raises(ValueError) as raised:
        _run({"done_reason": "length", "response": "", "prompt_eval_count": 3900, "eval_count": 0})

    message = str(raised.value)
    assert "length" in message
    assert "context" in message.lower()
    assert "3900" in message, "the token counts are what a window is re-derived from"


def test_an_error_body_names_the_transport_error():
    with pytest.raises(ValueError) as raised:
        _run({"error": "model requires more system memory"})
    assert "model requires more system memory" in str(raised.value)


def test_a_body_with_no_cause_says_what_it_had():
    with pytest.raises(ValueError) as raised:
        _run({"done_reason": "stop", "response": "", "eval_count": 0})
    message = str(raised.value)
    assert "no body and no cause" in message
    assert "stop" in message


def test_a_non_object_reply_is_named():
    with pytest.raises(ValueError) as raised:
        _run("not a dict at all")
    assert "was not an object" in str(raised.value)


def test_a_healthy_body_still_parses():
    good = (
        '{"symbol":"SPY","action":"HOLD","quantity":0,"confidence":0.5,'
        '"rationale":"no signal"}'
    )
    decision = _run({"done_reason": "stop", "response": good})
    assert decision.action == "HOLD"


def test_the_real_parse_error_message_is_unchanged_for_prose():
    """Adding the transport's diagnosis must not lose the JSON distinction."""
    with pytest.raises(ValueError) as raised:
        _run({"done_reason": "stop", "response": "I think you should buy."})
    assert "JSON" in str(raised.value)
