"""Statistical helpers must preserve the properties the verdict depends on."""

from __future__ import annotations

import random

import pytest

from autopoiesis.research.statistics import (
    daily_returns,
    deflated_sharpe_ratio,
    holm_adjust,
    paired_block_bootstrap,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)


def test_daily_returns_uses_previous_equity() -> None:
    assert daily_returns([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])


def test_bootstrap_detects_a_constant_positive_shift() -> None:
    result = paired_block_bootstrap(
        [0.0] * 200, [0.001] * 200, block_size=5, n_boot=200, seed=0
    )
    assert result["mean_diff"] == pytest.approx(0.001)
    assert result["ci_low"] > 0
    assert result["p_value"] == 0.0


def test_bootstrap_does_not_invent_a_shift() -> None:
    result = paired_block_bootstrap(
        [0.0] * 200, [0.0] * 200, block_size=5, n_boot=200, seed=0
    )
    assert result["mean_diff"] == 0.0
    assert result["ci_low"] <= 0 <= result["ci_high"]
    assert result["p_value"] == 1.0


def test_holm_is_monotone_and_preserves_order() -> None:
    adjusted = holm_adjust([0.01, 0.04, 0.03])
    assert adjusted == pytest.approx([0.03, 0.06, 0.06])


def test_deflated_sharpe_needs_variation_and_trials() -> None:
    assert deflated_sharpe_ratio([0.001] * 100, n_trials=4)["dsr"] == 0.0
    rng = random.Random(0)
    returns = [0.001 + rng.gauss(0, 0.0005) for _ in range(500)]
    result = deflated_sharpe_ratio(returns, n_trials=4)
    assert 0.0 <= result["dsr"] <= 1.0
    assert result["sharpe"] > 1.0
    assert result["expected_max_sharpe"] > 0


def test_deflated_sharpe_scales_the_expected_maximum_by_sqrt_variance() -> None:
    # Two-point returns: mean 0.001, sample stdev 0.0005005, so SR ~= 2.0.
    # With N=4 the expected maximum is ~1.05 * sqrt(1/499) ~= 0.047, not ~1.05.
    # The first implementation omitted that scaling and returned 0 for every arm.
    returns = [0.0015, 0.0005] * 250
    result = deflated_sharpe_ratio(returns, n_trials=4)
    assert result["sharpe"] == pytest.approx(2.0, rel=0.01)
    assert 0.04 < result["expected_max_sharpe"] < 0.06
    assert result["dsr"] > 0.99
    single = deflated_sharpe_ratio(returns, n_trials=1)
    assert single["expected_max_sharpe"] == 0.0
    assert single["dsr"] > 0.99


def test_pbo_is_bounded_and_deterministic() -> None:
    rng = random.Random(0)
    matrix = [[rng.gauss(0, 0.01) for _ in range(120)] for _ in range(3)]
    first = probability_of_backtest_overfitting(matrix, n_splits=6)
    second = probability_of_backtest_overfitting(matrix, n_splits=6)
    assert first == second
    assert first["pbo"] is not None
    assert 0.0 <= first["pbo"] <= 1.0


def test_sharpe_is_zero_on_a_flat_series() -> None:
    assert sharpe_ratio([0.0, 0.0, 0.0]) == 0.0
