"""The risk judgment: a volatility forecast with its own measured quality.

Price series here are SYNTHETIC (a seeded generator with a known volatility regime) and labelled so:
they prove the code computes what it says. That the forecast has skill on real bars is shown in
docs/evidence/signal-research-2026-10-08/, not here.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from min_agent import risk_judgment as rj


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

    from min_agent.llm_decision import HybridDecisionEngine

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
