"""An empty curriculum body must name its cause, not just report that it had none.

Measured 2026-10-14. `CURRICULUM_FAILED` with `constrained response contained no JSON object`
occurred six times out of six attempts between 2026-10-13 10:10 and 2026-10-14 05:36, across
three source fingerprints. The message is the whole of what was recorded and it says nothing
useful: every one of those six could be a context exhaustion, an Ollaca-style error body, or a
model that emitted prose, and the journal cannot tell them apart.

The cause was already written down in `llm_decision.py`, far from where the information is
destroyed:

    At 4096 the curriculum had 655 tokens left to answer, needed ~1202, and returned
    done_reason=length with an empty body - the exact "constrained response contained no JSON
    object" failure.

`done_reason=length` with an empty `response` is what a reply looks like when the prompt and
the answer schema have eaten the context window. `_ollama_curriculum_transport` returns
`str(response.get("response", ""))` and discards everything else, so the one field that
identifies the cause is gone before anything can read it.

Probed against a live model on 2026-10-14 to establish what is *not* the cause, so this file
does not encode a guess:

  - every schema returns well-formed JSON;
  - the real entry point completes both calls with a live-shaped context;
  - a 54,960-character prompt - about thirteen times the real one - still returns valid JSON at
    `num_ctx` 8192 and 32768, so a merely large prompt is not it.

What remains is the budget: prompt plus schema leaving too few tokens to answer. Reproduced
here with a synthetic transport and no model, network or clock, which is the only way to
assert the failure mode deterministically.

Usage: pytest tests/autopoiesis/test_curriculum_empty_body_cause.py -q
"""

from __future__ import annotations

import json

import pytest
from test_daemon import config as _config

from autopoiesis.cli import _ollama_curriculum_transport
from autopoiesis.curriculum import CurriculumTask, parse_curriculum_task_json

CONTEXT = {"symbols": ["SPY"]}


class _Engine:
    """Just enough of OllamaDecisionEngine for the transport under test."""

    def __init__(self, bodies, captured):
        self.bodies = list(bodies)
        self.captured = captured
        self.base_url = "http://127.0.0.1:11434"
        self.timeout = 30

    def transport(self, url, payload, timeout):
        self.captured.append({"url": url, "payload": payload})
        return self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]


def _transport(tmp_path, bodies, captured=None):
    import autopoiesis.cli as cli

    engine = _Engine(bodies, captured if captured is not None else [])
    original = cli.OllamaDecisionEngine
    cli.OllamaDecisionEngine = lambda **kwargs: engine  # type: ignore[misc]
    try:
        # `_ollama_curriculum_transport` builds its engine at call time, so the patch has to
        # stay in place while the inner `call` runs - which it does not, because it returns a
        # closure over an already-constructed engine. Captured here instead.
        factory = _ollama_curriculum_transport(_config(tmp_path))
    finally:
        cli.OllamaDecisionEngine = original  # type: ignore[misc]
    return factory


def _kind():
    return {"done_reason": "stop", "response": json.dumps({"kind": "FIXED_SIZE", "why": "x"})}


def _spec(strategy_id="synthetic-001"):
    return {
        "done_reason": "stop", "response": json.dumps({
            "strategy_id": strategy_id, "name": "Synthetic", "symbols": ["SPY"],
            "max_position_value": 1000.0, "created_at": "2026-10-14T12:00:00Z",
            "rationale": "synthetic fixture", "enabled": True, "kind": "FIXED_SIZE",
            "action": "BUY", "quantity": 1, "confidence": 0.6,
        }),
    }


# --- the cause has to survive ------------------------------------------------


def test_a_length_finished_empty_body_is_reported_as_context_exhaustion(tmp_path):
    """The recorded cause, reproduced with no model in the loop.

    `done_reason=length` with an empty `response` is the reply shape that says the context
    window is spent. An empty string here loses exactly the field that says so.
    """
    transport = _transport(tmp_path, [{"done_reason": "length", "response": "", "eval_count": 0}])

    with pytest.raises(ValueError) as raised:
        transport(CONTEXT)

    message = str(raised.value)
    assert "length" in message, f"the cause is in the reply and must reach the caller: {message!r}"
    assert "context" in message.lower(), message


def test_a_transport_error_body_is_reported_as_a_transport_failure(tmp_path):
    """An error body has no `response` key, and that is a different failure entirely.

    Both arrive as `""` at the caller today, so a model that is down and a model that ran out
    of room produce byte-identical journal entries.
    """
    transport = _transport(tmp_path, [{"error": "model requires more system memory"}])

    with pytest.raises(ValueError) as raised:
        transport(CONTEXT)

    assert "model requires more system memory" in str(raised.value), (
        "the transport's own text is the only diagnosis available"
    )


def test_the_cause_names_the_stage_it_happened_at(tmp_path):
    """Which of the two calls ran out matters: the kind pick and the spec differ in cost."""
    transport = _transport(tmp_path, [_kind(), {"done_reason": "length", "response": ""}])

    with pytest.raises(ValueError) as raised:
        transport(CONTEXT)

    assert "length" in str(raised.value)


# --- and nothing that works may change --------------------------------------


def test_a_healthy_chain_still_produces_a_task(tmp_path):
    transport = _transport(tmp_path, [_kind(), _spec()])

    task = parse_curriculum_task_json(transport(CONTEXT))

    assert isinstance(task, CurriculumTask)
    assert task.task_type == "STRATEGY_SPEC"
    assert task.strategy_spec is not None
    assert task.strategy_spec.strategy_id == "synthetic-001"


def test_a_body_that_is_prose_is_still_reported_as_a_json_problem(tmp_path):
    """The original message must survive for the case it was actually written for.

    Adding the transport's own diagnosis must not lose the distinction between "the transport
    gave me nothing" and "the transport gave me something that was not JSON".
    """
    transport = _transport(tmp_path, [{"done_reason": "stop", "response": "I think you should buy."}])

    with pytest.raises(ValueError) as raised:
        transport(CONTEXT)

    assert "JSON" in str(raised.value)
