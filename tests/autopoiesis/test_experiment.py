"""The four-arm experiment must use the real Guardian and next-open fills.

These tests exist because the first version of this runner did neither: it filled
at the decision bar's close and let cash go negative. A numeric result from such
a runner is not a strategy result, so the properties are asserted structurally.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis.models import TradeDecision
from autopoiesis.replay import ReplayBar
from autopoiesis.research.experiment import (
    BuyAndHoldEngine,
    CostModel,
    HoldEngine,
    RiskLimits,
    SleeveEngine,
    run_arm,
    run_experiment,
)

BASE = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)


def bars(count: int = 4, start: float = 100.0, step: float = 1.0) -> list[ReplayBar]:
    return [
        ReplayBar(
            timestamp=BASE + timedelta(days=index),
            open=start + step * index,
            high=start + step * index + 1,
            low=start + step * index - 1,
            close=start + step * index + 0.5,
            volume=1_000,
        )
        for index in range(count)
    ]


class AlwaysBuy:
    def __init__(self, quantity: int = 10):
        self.quantity = quantity

    def __call__(self, snapshot) -> TradeDecision:
        return TradeDecision(
            symbol=snapshot.symbol,
            action="BUY",
            quantity=self.quantity,
            confidence=0.6,
            rationale="test buy",
        )


def permissive() -> RiskLimits:
    return RiskLimits(
        allowlist=frozenset({"SPY"}),
        max_position_value=1_000_000,
        max_daily_loss=1_000_000,
        max_total_exposure=1_000_000,
        max_trades_per_day=100,
    )


def test_fill_uses_next_bar_open() -> None:
    series = bars(3)
    outcome = run_arm(
        arm="T",
        bars=series,
        engine=AlwaysBuy(),
        starting_equity=100_000,
        risk=permissive(),
    )
    assert outcome.fills, "the first decision should fill"
    assert outcome.fills[0]["price"] == pytest.approx(series[1].open)
    assert outcome.fills[0]["decision_timestamp"] == series[0].timestamp.isoformat()
    assert outcome.fills[0]["fill_timestamp"] == series[1].timestamp.isoformat()


def test_last_bar_cannot_fill() -> None:
    series = bars(2)
    outcome = run_arm(
        arm="T",
        bars=series,
        engine=AlwaysBuy(),
        starting_equity=100_000,
        risk=permissive(),
    )
    assert len(outcome.fills) == 1
    assert any("last bar cannot fill" in row["reason"] for row in outcome.refusals)


def test_position_cap_refuses_excess() -> None:
    series = bars(3)
    risk = RiskLimits(
        allowlist=frozenset({"SPY"}),
        max_position_value=500,
        max_daily_loss=1_000_000,
        max_total_exposure=1_000_000,
        max_trades_per_day=100,
    )
    outcome = run_arm(
        arm="T",
        bars=series,
        engine=AlwaysBuy(),
        starting_equity=100_000,
        risk=risk,
    )
    assert outcome.fills == []
    assert any("max_position_value" in row["reason"] for row in outcome.refusals)


def test_buying_power_prevents_leverage() -> None:
    series = bars(3)
    risk = RiskLimits(
        allowlist=frozenset({"SPY"}),
        max_position_value=1_000_000,
        max_daily_loss=1_000_000,
        max_total_exposure=1_000_000,
        max_trades_per_day=100,
    )
    outcome = run_arm(
        arm="T",
        bars=series,
        engine=AlwaysBuy(),
        starting_equity=500,
        risk=risk,
    )
    assert outcome.fills == []
    assert any("buying power" in row["reason"] for row in outcome.refusals)
    assert outcome.min_cash >= 0


def test_costs_are_folded_into_the_fill_price() -> None:
    series = bars(3)
    outcome = run_arm(
        arm="T",
        bars=series,
        engine=AlwaysBuy(),
        starting_equity=100_000,
        risk=permissive(),
        costs=CostModel(slippage_pct=0.001, commission_pct=0.002),
    )
    assert outcome.fills[0]["price"] == pytest.approx(series[1].open * 1.003)


def test_experiment_marks_d_not_run_and_keeps_every_seed() -> None:
    report = run_experiment(
        bars=bars(60),
        engines={"A": HoldEngine, "B": lambda: HoldEngine(), "C": lambda: HoldEngine()},
        seeds=(0, 1),
        risk=permissive(),
    )
    assert len(report.arms["A"]) == 2
    assert report.conclusion == "INSUFFICIENT"
    assert any("D NOT_RUN" in note for note in report.notes)


def test_sleeve_arm_buys_after_its_forecast_is_ready() -> None:
    series = bars(80, start=100.0, step=1.0)
    outcome = run_arm(
        arm="A",
        bars=series,
        engine=SleeveEngine(pool=20_000, target_vol_pct=10.0),
        starting_equity=100_000,
        risk=permissive(),
    )
    assert outcome.fills, "the core sleeve should rebalance once it has a forecast"
    assert all(fill["action"] in {"BUY", "SELL"} for fill in outcome.fills)


def test_buy_and_hold_benchmark_buys_once() -> None:
    outcome = run_arm(
        arm="BENCHMARK",
        bars=bars(10),
        engine=BuyAndHoldEngine(max_position_value=25_000),
        starting_equity=100_000,
        risk=permissive(),
    )
    assert len(outcome.fills) == 1
    assert outcome.fills[0]["action"] == "BUY"


def test_experiment_keeps_benchmark_and_controls_out_of_the_family() -> None:
    report = run_experiment(
        bars=bars(30),
        engines={"A": HoldEngine},
        seeds=(0,),
        risk=permissive(),
        benchmarks={"BENCH": HoldEngine},
        controls={"N1": HoldEngine},
    )
    assert set(report.benchmarks) == {"BENCH"}
    assert set(report.controls) == {"N1"}
    assert len(report.benchmark_comparisons) == 1
    assert report.benchmark_comparisons[0]["arm"] == "A"
    assert report.benchmark_comparisons[0]["benchmark"] == "BENCH"
    assert report.conclusion == "INSUFFICIENT"
