"""The v5 cross-sectional candidates. All panels here are synthetic (labelled)."""
from __future__ import annotations

import random

from autopoiesis.research import xsection


def _panel(returns: dict[str, list[float]]) -> dict:
    n = len(next(iter(returns.values())))
    return {"returns": returns, "first": dict.fromkeys(returns, 0), "calendar": list(range(n)),
            "grid": [0], "members": [sorted(returns)]}


def test_no_score_reads_a_return_at_or_after_its_session():
    rng = random.Random(5)
    base = {f"S{i}": [rng.gauss(0, 0.02) for _ in range(400)] for i in range(20)}  # synthetic
    t = 300
    for name in xsection.CANDIDATES:
        before = xsection.selector(name, _panel(base))(t, sorted(base))
        changed = {s: r[:t] + [rng.gauss(0, 0.5) for _ in r[t:]] for s, r in base.items()}  # synthetic future
        after = xsection.selector(name, _panel(changed))(t, sorted(base))
        assert before == after, name


def test_each_score_holds_the_half_it_names():
    t = 300
    up = [0.002] * t  # synthetic: steady winner
    down = [-0.002] * t  # synthetic: steady loser
    quiet = [0.0005 if i % 2 else -0.0005 for i in range(t)]  # synthetic: low volatility
    loud = [0.03 if i % 2 else -0.03 for i in range(t)]  # synthetic: high volatility
    p = _panel({"UP": up, "DOWN": down})
    assert xsection.selector("MOM_12_1", p)(t, ["DOWN", "UP"]) == ["UP"]
    assert xsection.selector("REV_1M", p)(t, ["DOWN", "UP"]) == ["DOWN"]
    q = _panel({"QUIET": quiet, "LOUD": loud})
    assert xsection.selector("LOWVOL_3M", q)(t, ["LOUD", "QUIET"]) == ["QUIET"]


def test_a_name_without_enough_history_is_not_selected():
    t = 300
    p = _panel({"NEW": [0.01] * t, "OLD": [0.0] * t})  # synthetic
    p["first"]["NEW"] = 100  # 200 sessions of its own history, MOM_12_1 needs 252
    assert xsection.selector("MOM_12_1", p)(t, ["NEW", "OLD"]) == ["OLD"]


def _tool():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import run_xsection_v5

    return run_xsection_v5


def test_the_holdout_refuses_an_unregistered_spec_and_a_second_open(tmp_path):
    import shutil
    from pathlib import Path

    import pytest

    from autopoiesis.research import prereg

    tool = _tool()
    spec = tmp_path / "v5.spec"
    shutil.copy(Path(__file__).resolve().parents[2] / "docs/research/specs/v5-xsection-top500.spec", spec)
    registry, out = tmp_path / "registry", tmp_path / "out"
    with pytest.raises(SystemExit, match="does not match its registration"):
        tool.main(["--phase", "holdout", "--spec", str(spec), "--registry", str(registry), "--out", str(out)])
    prereg.register(spec, registry)
    out.mkdir()
    (out / "holdout.json").write_text("{}\n")  # a previous open
    with pytest.raises(SystemExit, match="has been opened"):
        tool.main(["--phase", "holdout", "--spec", str(spec), "--registry", str(registry), "--out", str(out)])


def _diffs():
    rng = random.Random(9)
    return {c: [0.0004 + rng.gauss(0, 0.004) for _ in range(600)] for c in ("A", "B", "C")}  # synthetic


def test_a_residual_run_is_demeaned_and_adds_exactly_ir_times_te():
    d = _diffs()
    zero = xsection.residual_run(d, 600, seed=1, inject_ir={})
    one = xsection.residual_run(d, 600, seed=1, inject_ir={"A": 1.0})
    for c in d:
        assert abs(zero[c]["annual_excess"]) < 1e-9  # the full window of a demeaned series
    assert abs(one["A"]["annual_excess"] - xsection.tracking_error(d["A"])) < 1e-9
    assert one["B"]["annual_excess"] == zero["B"]["annual_excess"]  # only A was injected


def test_a_residual_run_is_reproducible_from_its_seed():
    d = _diffs()
    assert xsection.residual_run(d, 300, seed=4, inject_ir={"B": 0.5}) == xsection.residual_run(
        d, 300, seed=4, inject_ir={"B": 0.5})
