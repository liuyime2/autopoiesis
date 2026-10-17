"""A strategy admission would refuse today must not keep serving.

The plan's P1 item, second half: "make the lifecycle able to retire a strategy whose
reference has drifted from the market rather than waiting for a human". Measured three
times - TLT, XLF and SPY were each drained one share per cycle by a strategy whose trigger
band emitted a single action forever, and each time a human paused it. A pause stops that
instance and not the next one.

The predicate is admission's own, not a second number.

Usage: pytest tests/autopoiesis/test_drifted_reference_retirement.py -q
"""

from __future__ import annotations

from autopoiesis.daemon import AgentDaemon
from autopoiesis.models import StrategySpec
from autopoiesis.strategy_engine import StrategyLibrary


class _Admission:
    """Stands in for the gate: refuses exactly one id, for a reason."""

    def __init__(self, refused: dict):
        self._refused = refused

    def _reference_price_rejection_reason(self, strategy):
        return self._refused.get(strategy.strategy_id)


def _spec(strategy_id, lifecycle="PROBATION", enabled=True):
    return StrategySpec.model_validate({
        "strategy_id": strategy_id, "name": strategy_id, "symbols": ["SPY"],
        "created_at": "2026-10-06T00:00:00Z", "rationale": "fixture", "enabled": enabled,
        "kind": "TREND_FOLLOW", "max_position_value": 1000.0,
        "parameters": {"reference_price": 750.0, "threshold_pct": 0.01,
                       "quantity": 1, "confidence": 0.6},
        "lifecycle": lifecycle,
    })


def _daemon(tmp_path, *specs, refused=None):
    library = StrategyLibrary(tmp_path / "strategies")
    for spec in specs:
        library.save(spec)
    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.strategy_library = library
    daemon.strategy_admission = _Admission(refused or {})
    return daemon


def test_a_drifted_reference_is_retired(tmp_path):
    daemon = _daemon(
        tmp_path, _spec("drifted-001"),
        refused={"drifted-001": "reference_price 750.0 puts the trigger band around the real SPY price"},
    )
    decisions = daemon._drifted_reference_retirements()

    assert len(decisions) == 1
    assert decisions[0].new_lifecycle == "RETIRED"
    assert "reference drifted from the market" in decisions[0].reason
    assert "trigger band" in decisions[0].reason, "the gate's own reason must survive"


def test_a_healthy_reference_is_left_alone(tmp_path):
    """The check must not retire the library."""
    daemon = _daemon(tmp_path, _spec("healthy-001"))
    assert daemon._drifted_reference_retirements() == []


def test_only_probation_and_active_are_considered(tmp_path):
    """PAUSED and RETIRED are already out of service; retiring them again says nothing."""
    daemon = _daemon(
        tmp_path,
        _spec("paused-001", lifecycle="PAUSED"),
        _spec("retired-001", lifecycle="RETIRED"),
        _spec("baseline-001", lifecycle="BASELINE"),
        refused={"paused-001": "drifted", "retired-001": "drifted", "baseline-001": "drifted"},
    )
    assert daemon._drifted_reference_retirements() == []


def test_a_disabled_strategy_is_left_alone(tmp_path):
    """Not enabled is not serving, which is the only thing this rule is about."""
    daemon = _daemon(tmp_path, _spec("off-001", enabled=False),
                     refused={"off-001": "drifted"})
    assert daemon._drifted_reference_retirements() == []


def test_no_admission_gate_is_not_an_error(tmp_path):
    """A daemon built without one must not raise."""
    library = StrategyLibrary(tmp_path / "strategies")
    library.save(_spec("x-001"))
    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.strategy_library = library
    daemon.strategy_admission = None
    assert daemon._drifted_reference_retirements() == []


def test_every_drifted_strategy_is_reported_not_just_the_first(tmp_path):
    daemon = _daemon(
        tmp_path, _spec("a-001"), _spec("b-001"), _spec("c-001"),
        refused={"a-001": "drifted", "b-001": "drifted", "c-001": "drifted"},
    )
    assert len(daemon._drifted_reference_retirements()) == 3
