"""Paired block bootstrap and multiple-testing helpers (research only)."""

from __future__ import annotations

import random
from collections.abc import Sequence


def daily_returns(equity_curve: Sequence[float]) -> list[float]:
    out = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev:
            out.append((equity_curve[i] - prev) / prev)
    return out


def paired_block_bootstrap(
    a: Sequence[float],
    b: Sequence[float],
    *,
    block_size: int = 5,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict:
    """Paired block bootstrap CI for mean(b - a)."""
    rng = random.Random(seed)
    n = min(len(a), len(b))
    if n < block_size:
        return {"mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n": n}
    diffs = [b[i] - a[i] for i in range(n)]
    means = []
    for _ in range(n_boot):
        # block bootstrap
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
    return {
        "mean_diff": sum(diffs) / len(diffs),
        "ci_low": lo,
        "ci_high": hi,
        "n": n,
        "n_boot": len(means),
    }


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjustment."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        val = p_values[i] * (m - rank)
        running = max(running, val)
        adjusted[i] = min(1.0, running)
    return adjusted
