"""Which model produced which decision.

Phase 3 asks for a strategy **and model** registry. Every decision recorded
`decision_source` - "llm", "fallback_policy_engine", "baseline" - but not *which*
model, so outcomes could never be attributed to a model change, and the model was
upgraded during this project with nothing in the record to show it.

These tests pin the provenance rule and the refusal to back-fill history. The 920
cycles already on the journal carry no model, and inventing one for them would assert
a fact about the past that is not known.
"""

from datetime import datetime, timezone

import pytest

from min_agent import model_registry
from min_agent.llm_decision import parse_decision_json
from min_agent.models import (
    AccountSnapshot, CycleRecord, DataSnapshot, ExecutionResult, GuardianResult,
    TradeDecision,
)

TS = datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc)
UNATTRIBUTED = model_registry.UNATTRIBUTED


def _cycle(model=None, action="HOLD", confidence=0.6, status="SKIPPED"):
    return CycleRecord(
        cycle_id=f"c-{model}-{action}-{status}",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=TS, market_open=True, last_price=100.0,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action=action, quantity=0 if action == "HOLD" else 1,
            confidence=confidence, rationale="r", model=model,
            decision_source="llm" if model else "baseline",
        ),
        guardian=GuardianResult(approved=True, reason="ok"),
        execution=ExecutionResult(
            status=status, order_id=None, filled_quantity=0, message="m",
        ),
    )


# --------------------------------------------------------------------------
# provenance is captured at the source
# --------------------------------------------------------------------------

def test_a_parsed_llm_decision_carries_the_model_that_produced_it():
    text = '{"symbol":"SPY","action":"HOLD","quantity":0,"confidence":0.7,"rationale":"r"}'
    decision = parse_decision_json(text, model_name="qwen3.8:27b")

    assert decision.model == "qwen3.8:27b"


def test_no_model_name_leaves_the_decision_unattributed_rather_than_guessing():
    text = '{"symbol":"SPY","action":"HOLD","quantity":0,"confidence":0.7,"rationale":"r"}'
    assert parse_decision_json(text).model is None


def test_a_model_named_inside_the_response_is_not_overwritten():
    """The transport's own view of which model answered wins over the configured
    name, because that is the one that actually served the request."""
    text = (
        '{"symbol":"SPY","action":"HOLD","quantity":0,"confidence":0.7,'
        '"rationale":"r","model":"qwen35:latest"}'
    )
    assert parse_decision_json(text, model_name="qwen3.8:27b").model == "qwen35:latest"


def test_the_fallback_path_names_itself():
    """A fallback decision must never be counted as a model's work."""
    from min_agent.policy_engine import PolicyEngine
    import inspect
    source = inspect.getsource(PolicyEngine)
    assert 'model="fallback_policy_engine"' in source


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

def test_decisions_are_grouped_by_model():
    records = [
        _cycle(model="qwen3.8:27b"),
        _cycle(model="qwen3.8:27b", action="BUY", status="SUBMITTED"),
        _cycle(model="qwen35:latest"),
    ]
    registry = model_registry.build(records)

    assert registry.total_decisions == 3
    assert registry.models["qwen3.8:27b"].decisions == 2
    assert registry.models["qwen3.8:27b"].submitted == 1
    assert registry.models["qwen3.8:27b"].actions == {"HOLD": 1, "BUY": 1}
    assert registry.models["qwen35:latest"].decisions == 1


def test_history_is_never_back_filled():
    """All 920 journaled cycles predate the field. Assigning them the currently
    configured model would assert a fact about the past that is not known, and would
    make every future comparison look like evidence for a model that never ran."""
    registry = model_registry.build([_cycle(model=None) for _ in range(5)])

    assert registry.models[UNATTRIBUTED].decisions == 5
    assert "provenance was not captured" in registry.summary()


def test_a_registry_with_no_named_model_says_so_rather_than_showing_an_empty_table():
    registry = model_registry.build([_cycle(model=None)])
    summary = registry.summary()

    assert "none of which records a model" in summary
    assert "qwen" not in summary


def test_rejected_orders_are_counted_separately_from_submitted():
    registry = model_registry.build([
        _cycle(model="m", status="SUBMITTED"),
        _cycle(model="m", status="REJECTED"),
        _cycle(model="m", status="SHADOWED"),
    ])
    entry = registry.models["m"]

    assert entry.submitted == 1
    assert entry.rejected == 1
    assert entry.decisions == 3, "a shadow decision is still a decision"


def test_mean_confidence_is_reported_per_model():
    registry = model_registry.build([
        _cycle(model="m", confidence=0.2), _cycle(model="m", confidence=0.8),
    ])
    assert registry.models["m"].mean_confidence == pytest.approx(0.5)


def test_first_and_last_seen_are_recorded():
    registry = model_registry.build([_cycle(model="m")])
    entry = registry.models["m"]
    assert entry.first_seen is not None
    assert entry.last_seen is not None


def test_the_module_writes_nothing():
    for name in ("save", "write", "append", "persist"):
        assert not hasattr(model_registry, name), (
            "a second store of model performance would drift from the journal"
        )


def test_an_empty_registry_is_honest_rather_than_crashy():
    registry = model_registry.build([])
    assert registry.total_decisions == 0
    assert registry.payloads() == []
    assert "none of which records a model" in registry.summary()


def test_decisions_from_different_sources_are_not_merged_under_one_model():
    """A fallback decision naming itself must be separable from a model decision."""
    from min_agent.policy_engine import PolicyEngine
    import inspect
    registry = model_registry.build([
        _cycle(model="qwen3.8:27b"),
        _cycle(model="fallback_policy_engine"),
    ])

    assert set(registry.models) == {"qwen3.8:27b", "fallback_policy_engine"}
    assert "fallback_policy_engine" in inspect.getsource(PolicyEngine)
