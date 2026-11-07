"""Paired block bootstrap, multiple-testing and overfitting diagnostics (research only)."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from itertools import combinations
from statistics import NormalDist


def daily_returns(equity_curve: Sequence[float]) -> list[float]:
    out = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev:
            out.append((equity_curve[i] - prev) / prev)
    return out


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def sharpe_ratio(returns: Sequence[float]) -> float:
    """Per-period Sharpe with a zero risk-free rate.

    Not annualised: the bootstrap and the DSR work in the same per-period units,
    and annualising in one place but not the other is how a factor of sqrt(252)
    silently enters a verdict.
    """
    stdev = _stdev(returns)
    if stdev == 0:
        return 0.0
    return _mean(returns) / stdev


def paired_block_bootstrap(
    a: Sequence[float],
    b: Sequence[float],
    *,
    block_size: int = 5,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict:
    """Paired block bootstrap for mean(b - a), with one- and two-sided p-values.

    Blocks are drawn with replacement from the paired difference series, preserving
    the local dependence that an i.i.d. bootstrap would destroy. The p-value is the
    share of resampled means on the other side of zero; it is a bootstrap p-value,
    not a t-test p-value, and is reported as such.
    """
    rng = random.Random(seed)
    n = min(len(a), len(b))
    if n < block_size:
        return {
            "mean_diff": 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "p_value": 1.0,
            "p_two_sided": 1.0,
            "n": n,
            "n_boot": 0,
        }
    diffs = [b[i] - a[i] for i in range(n)]
    means = []
    for _ in range(n_boot):
        idx = 0
        sample = []
        while idx < n:
            start = rng.randrange(0, n - block_size + 1)
            for j in range(block_size):
                if idx + j < n and start + j < n:
                    sample.append(diffs[start + j])
            idx += block_size
        if sample:
            means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    at_or_below_zero = sum(1 for value in means if value <= 0) / len(means)
    at_or_above_zero = sum(1 for value in means if value >= 0) / len(means)
    return {
        "mean_diff": sum(diffs) / len(diffs),
        "ci_low": lo,
        "ci_high": hi,
        "p_value": at_or_below_zero,
        "p_two_sided": min(1.0, 2.0 * min(at_or_below_zero, at_or_above_zero)),
        "n": n,
        "n_boot": len(means),
    }


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjustment, preserving the input order.

    The running maximum is what makes the adjusted values monotone: without it a
    later, smaller raw p-value can receive a smaller adjusted value than an earlier
    one, which is not a valid step-down procedure.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        val = p_values[i] * (m - rank)
        running = max(running, val)
        adjusted[i] = min(1.0, running)
    return adjusted


def deflated_sharpe_ratio(returns: Sequence[float], *, n_trials: int) -> dict:
    """Bailey/López de Prado deflated Sharpe probability.

    ``n_trials`` is the number of configurations the researcher could have chosen
    from, and is the number that makes the result honest: a search that tried forty
    variants and kept the best one has a higher expected maximum Sharpe under the
    null than a search that tried one. The result is a probability, not a verdict.
    """
    if len(returns) < 3 or n_trials < 1:
        return {"sharpe": 0.0, "expected_max_sharpe": 0.0, "dsr": 0.0, "n": len(returns)}
    stdev = _stdev(returns)
    if stdev == 0:
        return {"sharpe": 0.0, "expected_max_sharpe": 0.0, "dsr": 0.0, "n": len(returns)}
    mean = _mean(returns)
    sr = mean / stdev
    centered = [value - mean for value in returns]
    n = len(returns)
    skew = sum(value**3 for value in centered) / n / stdev**3
    kurt = sum(value**4 for value in centered) / n / stdev**4
    denominator_sq = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denominator_sq <= 0:
        return {"sharpe": sr, "expected_max_sharpe": 0.0, "dsr": 0.0, "n": n}
    normal = NormalDist()
    gamma = 0.5772156649015329
    # The expected maximum is on the same per-period scale as `sr`. Omitting
    # sqrt(V[SR]) here compares a per-period Sharpe to a ~1.3 standard-normal
    # quantile and drives every DSR to zero, which is what the first v2 report did.
    variance_sr = denominator_sq / (n - 1)
    if n_trials <= 1:
        expected_max = 0.0
    else:
        expected_max = math.sqrt(variance_sr) * (
            (1 - gamma) * normal.inv_cdf(1 - 1 / n_trials)
            + gamma * normal.inv_cdf(1 - 1 / (n_trials * math.e))
        )
    statistic = (sr - expected_max) * math.sqrt(n - 1) / math.sqrt(denominator_sq)
    return {
        "sharpe": sr,
        "expected_max_sharpe": expected_max,
        "dsr": normal.cdf(statistic),
        "n": n,
        "n_trials": n_trials,
    }


def probability_of_backtest_overfitting(
    return_matrix: Sequence[Sequence[float]], *, n_splits: int = 10
) -> dict:
    """PBO via combinatorially symmetric cross-validation (CSCV).

    Each split ranks every candidate on the in-sample half, picks the in-sample
    winner, and records where that winner ranks out-of-sample. PBO is the share of
    splits in which the winner lands below the out-of-sample median. A high value
    means the selection procedure is choosing noise.
    """
    if not return_matrix or len(return_matrix) < 2:
        return {"pbo": None, "splits": 0, "n_strategies": len(return_matrix)}
    n_strategies = len(return_matrix)
    length = min(len(series) for series in return_matrix)
    if length < n_splits * 2 or n_splits % 2:
        return {"pbo": None, "splits": 0, "n_strategies": n_strategies}
    aligned = [list(series[:length]) for series in return_matrix]
    boundaries = [
        (length * index // n_splits, length * (index + 1) // n_splits)
        for index in range(n_splits)
    ]
    overfit = 0
    splits = 0
    for chosen in combinations(range(n_splits), n_splits // 2):
        is_indices = [i for block in chosen for i in range(*boundaries[block])]
        oos_indices = [
            i
            for block in range(n_splits)
            if block not in chosen
            for i in range(*boundaries[block])
        ]
        if not is_indices or not oos_indices:
            continue
        is_scores = [sharpe_ratio([series[i] for i in is_indices]) for series in aligned]
        oos_scores = [sharpe_ratio([series[i] for i in oos_indices]) for series in aligned]
        winner = max(range(n_strategies), key=lambda index: is_scores[index])
        winner_oos_rank = sorted(oos_scores).index(oos_scores[winner])
        if winner_oos_rank < n_strategies / 2:
            overfit += 1
        splits += 1
    return {
        "pbo": (overfit / splits) if splits else None,
        "splits": splits,
        "n_strategies": n_strategies,
    }
