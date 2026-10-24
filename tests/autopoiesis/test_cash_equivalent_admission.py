"""A strategy on a cash-equivalent ETF must not reach the library.

Measured 2026-10-24: 7 of the 14 live TREND_FOLLOW strategies sat on BIL, anchored at 87-96
with a 1.5-3% band. BIL moves a few basis points a month, so the hypothesis cannot pay and the
probes only consumed probation cycles. The model may still manage a BIL position; what is
refused is a *strategy* on it.

All fixtures are synthetic. Usage: pytest tests/autopoiesis/test_cash_equivalent_admission.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.guardian import Guardian
from autopoiesis.models import StrategySpec
from autopoiesis.strategy_admission import (
    CASH_EQUIVALENT_SYMBOLS,
    StrategyAdmission,
    _cash_equivalent_reason,
)
from autopoiesis.strategy_engine import StrategyLibrary


def _spec(symbol: str, kind: str = "TREND_FOLLOW", strategy_id: str = "trend-follow-x-001") -> StrategySpec:
    parameters = (
        {"reference_price": 95.0, "threshold_pct": 0.02, "quantity": 1, "confidence": 0.7}
        if kind == "TREND_FOLLOW"
        else {}
    )
    return StrategySpec(
        strategy_id=strategy_id, name="x", kind=kind, symbols=(symbol,), parameters=parameters,
        max_position_value=5_000.0, created_at=datetime(2026, 10, 24, tzinfo=timezone.utc), rationale="x",
    )


@pytest.mark.parametrize("symbol", sorted(CASH_EQUIVALENT_SYMBOLS))
def test_every_cash_equivalent_is_refused(symbol):
    assert symbol in (_cash_equivalent_reason(_spec(symbol)) or "")


def test_lowercase_symbols_are_still_recognised():
    assert _cash_equivalent_reason(_spec("bil")) is not None


@pytest.mark.parametrize("symbol", ["SPY", "TLT", "IEF", "AAPL"])
def test_instruments_that_carry_risk_are_untouched(symbol):
    assert _cash_equivalent_reason(_spec(symbol)) is None


def test_the_hold_baseline_is_exempt():
    assert _cash_equivalent_reason(_spec("BIL", kind="HOLD_BASELINE")) is None


def test_admission_refuses_a_cash_equivalent_strategy(tmp_path):
    guardian = Guardian(
        max_position_value=25_000.0, max_daily_loss=500.0, max_trades_per_day=100,
        allowlist=frozenset({"SPY", "BIL"}),
    )
    admission = StrategyAdmission(guardian=guardian, strategy_library=StrategyLibrary(tmp_path))
    baseline = admission.admit(_spec("SPY", kind="HOLD_BASELINE", strategy_id="hold-baseline-001"))
    assert baseline.accepted is True, baseline.reason

    result = admission.admit(_spec("BIL"))

    assert result.accepted is False
    assert "cash-equivalent" in result.reason


def test_a_refused_kind_is_not_admitted_and_the_default_gate_admits_it(tmp_path):
    from autopoiesis.config import AgentConfig

    guardian = Guardian(
        max_position_value=25_000.0, max_daily_loss=500.0, max_trades_per_day=100,
        allowlist=frozenset({"SPY"}),
    )
    refusing = StrategyAdmission(
        guardian=guardian, strategy_library=StrategyLibrary(tmp_path / "a"), refused_kinds=("TREND_FOLLOW",)
    )
    assert refusing.admit(_spec("SPY", kind="HOLD_BASELINE", strategy_id="hold-baseline-001")).accepted
    result = refusing.admit(_spec("SPY"))
    assert result.accepted is False and "not admitted on this deployment" in result.reason

    assert AgentConfig.from_env({}).refused_kinds == ("TREND_FOLLOW",)
    assert AgentConfig.from_env({"AUTOPOIESIS_REFUSED_KINDS": "none"}).refused_kinds == ()
