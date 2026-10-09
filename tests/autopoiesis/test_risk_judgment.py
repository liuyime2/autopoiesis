"""The risk judgment: a volatility forecast with its own measured quality.

Price series here are SYNTHETIC (a seeded generator with a known volatility regime) and labelled so:
they prove the code computes what it says. That the forecast has skill on real bars is shown in
docs/evidence/signal-research-2026-10-08/, not here.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from autopoiesis import risk_judgment as rj


def _series(vols, per=21, seed=5):
    """Daily closes whose volatility steps through `vols` (annualised, as fractions), `per` days each."""
    rng = random.Random(seed)
    px, out = 100.0, []
    for v in vols:
        for _ in range(per):
            px *= math.exp(rng.gauss(0, v / math.sqrt(252)))
            out.append(px)
    return out


def test_the_ewma_follows_a_volatility_regime_and_rises_after_a_shock():
    calm = rj.forecast_vol_pct(_series([0.10] * 10))
    wild = rj.forecast_vol_pct(_series([0.10] * 9 + [0.40]))
    assert 5 < calm < 18 and wild > 2 * calm


def test_too_little_history_gives_no_forecast_rather_than_a_guess():
    assert rj.forecast_vol_pct([100.0, 101.0, 100.5]) is None
    assert rj.forecast_quality([100.0 + i for i in range(40)])["rank_corr"] is None


def test_the_forecast_ranks_the_volatility_that_follows_when_volatility_persists():
    # regimes that last half a year, so a 21-day forecast window sits almost wholly inside one;
    # regimes that flip faster than the window leave nothing to forecast (checked: -0.31 at 21 days)
    vols = [0.08, 0.30, 0.10, 0.25, 0.09, 0.28] * 3
    q = rj.forecast_quality(_series(vols, per=126))
    assert q["n"] > 100 and q["rank_corr"] > 0.5


def test_the_status_is_earned_by_evidence_and_scaling_is_never_leverage():
    def q(rc, se, n):
        return {"rank_corr": rc, "se": se, "effective_n": n}

    assert rj.quality_status(q(0.45, 0.09, 127)) == "ACTIVE"            # what ten years of real bars gave
    assert rj.quality_status(q(0.047, 0.073, 188)) == "UNVERIFIED"      # no skill: not shown to work, not shown to fail
    assert rj.quality_status(q(-0.30, 0.05, 400)) == "SUSPENDED"        # confidently useless
    assert rj.quality_status(q(0.60, 0.28, 12)) == "UNVERIFIED"         # too few independent outcomes to say
    assert rj.quality_status(q(None, None, 0)) == "UNVERIFIED"
    assert rj.vol_scaled_fraction(6.0, 12.0) == 1.0 and rj.vol_scaled_fraction(24.0, 12.0) == 0.5


def test_a_forecast_with_no_skill_gives_no_scaling_advice():
    rng = random.Random(9)
    px, closes = 100.0, []
    for _ in range(4000):
        px *= math.exp(rng.gauss(0, rng.choice([0.005, 0.03])))   # volatility re-drawn every day: nothing to forecast
        closes.append(px)
    block = rj.RiskJudgment(lambda s: closes, today=lambda: date(2026, 10, 8)).block("X")
    assert block["status"] != "ACTIVE" and "vol_scaled_fraction" not in block


def test_the_quality_reports_the_overlap_it_carries():
    q = rj.forecast_quality(_series([0.1, 0.3] * 20, per=63))
    assert abs(q["effective_n"] - q["n"] / 21) < 1e-9 and abs(q["se"] - 1 / math.sqrt(q["effective_n"])) < 1e-9


def test_the_block_is_cached_for_the_day_and_a_failing_provider_leaves_the_decision_alone():
    calls = []
    closes = _series([0.1, 0.1, 0.3, 0.3] * 20)

    def provider(sym):
        calls.append(sym)
        return closes

    day = [date(2026, 10, 8)]
    risk = rj.RiskJudgment(provider, today=lambda: day[0])
    first = risk.block("SPY")
    risk.block("SPY")
    assert len(calls) == 1 and first["status"] in {"ACTIVE", "SUSPENDED", "UNVERIFIED"} and first["regime"] in {"LOW", "NORMAL", "HIGH"}
    day[0] += timedelta(days=1)
    risk.block("SPY")
    assert len(calls) == 2
    assert rj.RiskJudgment(lambda s: (_ for _ in ()).throw(RuntimeError("down"))).block("SPY") is None
    assert rj.RiskJudgment(lambda s: None).block("SPY") is None


def test_the_decision_context_carries_the_risk_block_and_survives_a_failing_provider():
    from types import SimpleNamespace

    from autopoiesis.llm_decision import HybridDecisionEngine

    def engine(provider):
        e = HybridDecisionEngine.__new__(HybridDecisionEngine)
        e.risk_provider = provider
        return e

    block = {"forecast_vol_pct": 14.2, "status": "ACTIVE", "vol_scaled_fraction": 0.84}
    assert engine(lambda s: block)._risk("SPY") == block
    assert engine(None)._risk("SPY") is None
    assert engine(lambda s: (_ for _ in ()).throw(RuntimeError("x")))._risk("SPY") is None
    assert SimpleNamespace  # keep the import used


def test_a_failing_risk_judgment_says_why_it_failed():
    risk = rj.RiskJudgment(lambda s: (_ for _ in ()).throw(RuntimeError("broker down")))
    assert risk.block("SPY") is None and "RuntimeError: broker down" in risk.last_errors["SPY"]


# --- the quality ladder -------------------------------------------------------------------
#
# These pin the design against the measurement that produced it. Each number below was measured on
# real SPY bars on 2026-10-08, and the shape of them is the argument: a one-year window carries 12
# independent outcomes and cannot clear a 0.10 floor at 95% in either direction, so a "recent window"
# gate could only ever say "I do not know". Gating on it would have suspended a working judgement on
# the one symbol this account trades.


def _ladder_series(n=3000, seed=5):
    import random

    rng = random.Random(seed)
    out, px = [], 100.0
    for _ in range(n):
        px *= 1 + rng.gauss(0.0004, 0.012)
        out.append(px)
    return out


def test_a_window_with_too_few_independent_outcomes_is_unverified_and_says_why():
    """The number that matters is `n_effective`, not the number of days. Twelve independent
    outcomes is what a year of 21-day horizons carries, and it cannot decide anything."""
    from autopoiesis import risk_judgment as rj

    closes = _ladder_series()
    recent = rj.forecast_quality(closes, window=252)
    assert recent["effective_n"] == 12.0
    assert rj.quality_status(recent) == "UNVERIFIED"


def test_the_ladder_decides_at_the_longest_window_that_can_decide_and_reports_the_rest():
    from autopoiesis import risk_judgment as rj

    ladder = rj.quality_ladder(_ladder_series())
    rungs = ladder["rungs"]
    assert [r["window"] for r in rungs] == sorted((r["window"] for r in rungs), reverse=True)
    # Every rung carries the power it was decided with.
    for rung in rungs:
        assert rung["effective_n"] > 0 and rung["rank_corr"] is not None
    decided = [r for r in rungs if r["status"] != "UNVERIFIED"]
    if decided:
        assert ladder["status"] == decided[0]["status"]
        assert ladder["decided_on"] == decided[0]["window"]
    else:
        assert ladder["status"] == "UNVERIFIED" and ladder["decided_on"] is None


def test_a_ladder_that_cannot_decide_is_unmeasured_not_good():
    """UNVERIFIED must stay distinguishable from ACTIVE. A judgement nobody can measure is not a
    judgement that works."""
    from autopoiesis import risk_judgment as rj

    flat = [100.0] * 200  # no returns, so nothing to correlate
    ladder = rj.quality_ladder(flat)
    assert ladder["decided_on"] is None
    assert ladder["status"] == "UNVERIFIED"


def test_the_ladder_never_contradicts_the_whole_history_without_saying_which_window_decided():
    """The two are reported together on purpose. Measured on real bars: SPY reads ACTIVE on the whole
    history (+0.657) and UNVERIFIED on 1008 days (+0.320, lower bound two hundredths short). Both are
    true; which one governs is stated rather than left to whichever the reader finds first."""
    from autopoiesis import risk_judgment as rj

    ladder = rj.quality_ladder(_ladder_series())
    assert ladder["whole_history"] is not None
    assert ladder["whole_history_status"] in {"ACTIVE", "SUSPENDED", "UNVERIFIED"}
    assert ladder["status"] in {"ACTIVE", "SUSPENDED", "UNVERIFIED"}


def test_the_decision_block_carries_the_window_it_was_decided_on():
    """A status with no denominator attached is a claim; this is the check that keeps them together."""
    from autopoiesis import risk_judgment as rj

    judgment = rj.RiskJudgment(lambda symbol: _ladder_series(), today=lambda: __import__("datetime").date(2026, 10, 8))
    block = judgment.block("SPY")
    assert block is not None
    assert block["status"] in {"ACTIVE", "SUSPENDED", "UNVERIFIED"}
    assert "quality_decided_on_days" in block
    assert block["quality_ladder"], "the ladder must travel with the verdict"
    if block["status"] == "ACTIVE":
        assert block["quality_decided_on_days"] is not None
