"""SYNTHETIC calibration of the paired block bootstrap used by the G4 comparisons.

Question: when the true mean difference between two arms is zero but the daily differences are
autocorrelated, how often does `statistics.paired_block_bootstrap` report p_two_sided < 0.05?
A well-calibrated test says about 5%. Every series here is simulated (AR(1) with a stated rho,
daily scale 0.1%); nothing is market data. The production function is called unchanged.

Usage: python bootstrap_calibration.py [runs] [n_boot] [seed]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.research.statistics import paired_block_bootstrap  # noqa: E402

N = 1579
SCALE = 0.001


def ar1(rng: random.Random, rho: float, n: int) -> list[float]:
    x, out = 0.0, []
    sd = (1 - rho * rho) ** 0.5
    for _ in range(n):
        x = rho * x + sd * rng.gauss(0, 1)
        out.append(SCALE * x)
    return out


def rate(runs: int, n_boot: int, seed: int, rho: float, block: int) -> float:
    rng = random.Random(seed + int(rho * 1000) + block)
    hits = 0
    zeros = [0.0] * N
    for i in range(runs):
        diff = ar1(rng, rho, N)  # a - b where the true mean is zero
        res = paired_block_bootstrap(zeros, diff, block_size=block, n_boot=n_boot, seed=i)
        hits += res["p_two_sided"] < 0.05
    return hits / runs


if __name__ == "__main__":
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    n_boot = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 20261107
    table = {
        f"rho={rho:.1f}": {f"block={b}": rate(runs, n_boot, seed, rho, b) for b in (5, 10, 20)}
        for rho in (0.0, 0.2, 0.5)
    }
    print(json.dumps({"synthetic": True, "runs": runs, "n_boot": n_boot, "n": N, "false_positive_rate_at_0.05": table}, indent=2))
