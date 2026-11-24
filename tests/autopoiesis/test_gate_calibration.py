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


def test_a_cross_section_world_is_reproducible_and_has_no_edge_without_one():
    panel = {f"S{i}": _noise(200, 0.0, 20 + i) for i in range(8)}  # synthetic panel
    a = gc.cross_section_run(6, seed=3, inject=0.0, panel=panel)
    assert a == gc.cross_section_run(6, seed=3, inject=0.0, panel=panel)
    flat = {f"S{i}": [0.0] * 200 for i in range(4)}  # synthetic: nothing moves
    zero = gc.cross_section_run(4, seed=1, inject=0.0, panel=flat)
    # Equal holdings of names that never move: only the costs of the first switch separate them.
    assert -0.001 < zero["annual_excess"] <= 0.0


def test_a_slower_rebalance_re_ranks_only_on_its_closes():
    panel = {f"S{i}": _noise(200, 0.0, 40 + i) for i in range(8)}  # synthetic panel
    daily = gc.cross_section_run(6, seed=5, inject=0.05, panel=panel)
    weekly = gc.cross_section_run(6, seed=5, inject=0.05, panel=panel, rebalance_every=5)
    # 200 closes, ranking from close 20: daily re-ranks at 180 closes, weekly at 1 + those on a multiple of 5.
    assert daily["rebalances"] == 180
    assert weekly["rebalances"] == 1 + sum(1 for step in range(21, 200) if step % 5 == 0)
    assert weekly["annual_excess"] != daily["annual_excess"]


# --- universe placebo (synthetic bars, labelled) --------------------------------------------

def _ubars(closes: list[float], start_day: int = 0):
    from datetime import date, datetime, timedelta, timezone

    from autopoiesis.replay import ReplayBar

    out = []
    for i, c in enumerate(closes):
        stamp = datetime.combine(date(2024, 1, 1) + timedelta(days=start_day + i), datetime.min.time(),
                                 tzinfo=timezone.utc)
        out.append(ReplayBar(timestamp=stamp, open=c, high=c, low=c, close=c, volume=1.0))
    return out


def _day(i: int):
    from datetime import date, timedelta

    return date(2024, 1, 1) + timedelta(days=i)


def test_a_universe_return_is_zero_after_the_last_bar_and_nothing_at_the_cutoff_is_read():
    bars = {"AAA": _ubars([10, 11, 12, 13, 14, 15]),  # synthetic
            "BBB": _ubars([10, 20, 40])}  # synthetic: data ends on day 2
    panel = gc.universe_panel(bars, {_day(1): ["AAA", "BBB"]}, cutoff=_day(5))
    assert panel["calendar"] == [_day(i) for i in range(5)]  # day 5 (close 15) is not read
    assert panel["returns"]["BBB"] == [0.0, 1.0, 1.0, 0.0, 0.0]
    assert abs(panel["returns"]["AAA"][4] - (14 / 13 - 1)) < 1e-12


def test_a_universe_rebalance_takes_the_next_grid_points_members():
    flat = [10.0] * 12
    bars = {s: _ubars(flat) for s in ("A", "B", "C", "D")}  # synthetic
    up = _ubars([10 * 1.01 ** i for i in range(12)])  # synthetic: +1%/day
    bars["UP"] = up
    members = {_day(0): ["A", "B"], _day(6): ["UP", "C"]}
    panel = gc.universe_panel(bars, members, cutoff=_day(12))
    assert panel["grid"] == [0, 6]
    bench, _cand, start = gc.placebo_series(12, 1, seed=1, inject=0.0, panel=panel)
    assert start == 0  # only one window of 12 fits
    # Before day 6 the members are flat; from day 6 the benchmark holds UP at half weight.
    assert bench[:6] == [0.0] * 6
    assert all(abs(x - 0.005) < 1e-12 for x in bench[6:])


def test_injection_raises_the_placebo_excess_and_a_seed_reproduces_the_run():
    rng = random.Random(7)
    bars = {f"S{i}": _ubars([100 * (1 + rng.gauss(0, 0.01)) ** 1 for _ in range(300)]) for i in range(40)}  # synthetic
    members = {_day(t): sorted(bars) for t in range(0, 300, 5)}
    panel = gc.universe_panel(bars, members, cutoff=_day(300))
    a = gc.placebo_universe_run(200, 1, seed=3, inject=0.0, panel=panel)
    b = gc.placebo_universe_run(200, 1, seed=3, inject=0.0, panel=panel)
    c = gc.placebo_universe_run(200, 1, seed=3, inject=0.20, panel=panel)
    assert a == b
    assert a["start"] == c["start"]
    assert abs((c["annual_excess"] - a["annual_excess"]) - 0.20) < 1e-9


def test_the_placebo_series_is_unchanged_by_the_selection_refactor():
    import hashlib
    import json

    rng = random.Random(11)
    bars = {f"S{i}": _ubars([100 * (1 + rng.gauss(0, 0.01)) for _ in range(300)], start_day=(i % 7) * 10)
            for i in range(30)}  # synthetic
    members = {_day(t): sorted(rng.sample(sorted(bars), 20)) for t in range(80, 300, 5)}
    panel = gc.universe_panel(bars, members, cutoff=_day(300))
    out = [gc.placebo_series(150, e, s, inj, panel) for e in (1, 4) for s in (1, 2, 3) for inj in (0.0, 0.05)]
    # Recorded 2026-11-24 from the code before `selection_series` was extracted.
    assert hashlib.sha256(json.dumps(out).encode()).hexdigest() == (
        "042f40db042b932e72b21c2fbc83a769b56c3f38efc2650453d35252d56a69f1")
