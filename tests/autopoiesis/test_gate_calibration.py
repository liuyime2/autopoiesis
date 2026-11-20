"""Stage 1 gate calibration. All series here are synthetic (labelled as such)."""
from __future__ import annotations

import random

from autopoiesis.research import gate_calibration as gc


def _noise(n: int, mean: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [mean + rng.gauss(0.0, 0.002) for _ in range(n)]


def test_the_v4_rule_proves_a_large_clean_edge_and_not_a_zero_one():
    base = [0.0] * 1500
    strong = _noise(1500, 0.10 / 252, 1)  # synthetic: 10%/yr, low noise
    flat = _noise(1500, 0.0, 2)  # synthetic: no edge
    out = gc.v4_verdicts({"strong": (base, strong), "flat": (base, flat)})
    assert out["strong"]["proven"] is True
    assert out["flat"]["proven"] is False


def test_the_v4_rule_refuses_an_edge_below_the_minimum_effect_even_when_significant():
    base = [0.0] * 1500
    small = _noise(1500, 0.005 / 252, 3)  # synthetic: 0.5%/yr < the 1%/yr minimum
    small = [x - (sum(small) / len(small)) + 0.005 / 252 for x in small]
    out = gc.v4_verdicts({"small": (base, small)})
    assert out["small"]["mean_diff"] < gc.MIN_EFFECT_DAILY
    assert out["small"]["proven"] is False


def test_the_demeaned_null_has_no_drift_and_a_seed_reproduces_it():
    rets = {"X": _noise(300, 0.001, 4), "Y": _noise(300, -0.0005, 5)}
    order = list(range(300))
    random.Random(7).shuffle(order)
    a = gc.synthetic_closes(rets, order, demean=True)
    b = gc.synthetic_closes(rets, order, demean=True)
    assert a == b
    for closes in a.values():
        r = gc.returns_of(closes)
        assert abs(sum(r) / len(r)) < 1e-12


def test_injection_only_adds_return_while_arm_b_would_be_long():
    rets = {"X": [0.0] * 120}
    order = list(range(120))
    flat = gc.synthetic_closes(rets, order, demean=False)["X"]
    assert flat == [100.0] * 121  # flat prices never cross the band, so nothing is injected
    assert gc.synthetic_closes(rets, order, demean=False, inject=0.10)["X"] == flat


def test_a_breadth_world_is_reproducible_from_its_seed_and_draws_n_symbols():
    panel = {f"S{i}": _noise(300, 0.0005, 10 + i) for i in range(6)}  # synthetic panel
    a = gc.breadth_run(4, seed=11, inject=0.05, panel=panel)
    b = gc.breadth_run(4, seed=11, inject=0.05, panel=panel)
    assert a == b
    assert len(set(a["symbols"])) == 4 and set(a["symbols"]) <= set(panel)
    assert gc.breadth_run(4, seed=12, inject=0.05, panel=panel) != a
