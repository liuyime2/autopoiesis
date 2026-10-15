"""Severity must be proportional to how much of the record can be attributed.

The model-registry check warned only when *every* decision was unattributed, so a
record that is 93% unattributed - 920 of 987, the state it is actually in - reported
OK on the strength of the 67 decisions that do carry a model name. An all-or-nothing
threshold on a quantity that is inherently a ratio can only be right at one value.
"""
from __future__ import annotations

from autopoiesis.doctor import DoctorReport, _check_model_registry
from autopoiesis.model_registry import build


class _Cfg:
    model = "qwen3.8:27b"
    allowlist = ["SPY"]


class _Dec:
    """A decision the model answered. `decision_source` is what makes it one.

    Without it these fixtures modelled the rule path, where `model` is None by
    definition - and the ratio under test is over decisions the model answered.
    """

    def __init__(self, model, source="llm"):
        if model is not None:
            self.model = model
        self.decision_source = source

    def __bool__(self):
        return True


class _Rec:
    def __init__(self, model, source="llm"):
        self.decision = _Dec(model, source)


def _report(n_named, n_unattributed):
    records = [_Rec("qwen3.8:27b") for _ in range(n_named)]
    records += [_Rec(None) for _ in range(n_unattributed)]
    rep = DoctorReport()
    _check_model_registry(rep, _Cfg(), records)
    return next(c for c in rep.checks if c.name == "model registry")


def test_a_mostly_unattributed_record_is_not_ok():
    """The real state: 67 attributed, 920 unattributed."""
    check = _report(67, 920)
    assert check.status.value == "warn", (
        f"93% unattributed reported {check.status.value}: {check.detail}"
    )
    assert "93%" in check.detail, check.detail


def test_total_absence_still_warns():
    assert _report(0, 100).status.value == "warn"


def test_a_mostly_attributed_record_is_ok():
    """The gate must not cry wolf once provenance actually works."""
    check = _report(900, 20)
    assert check.status.value == "ok", check.detail


def test_full_attribution_is_ok():
    assert _report(100, 0).status.value == "ok"


def test_exactly_half_is_not_reported_as_ok():
    """The boundary is documented, so pin it rather than leave it to drift."""
    assert _report(50, 50).status.value == "warn"
    assert _report(51, 49).status.value == "ok"


def test_a_rule_decision_is_not_an_attribution_failure():
    """The model was never asked, so there is no model to attribute and nothing is wrong.

    Measured 2026-10-14: the check reported "1824 of 3221 decision(s) (57%) cannot be
    attributed to a model" while only 67 of those 1824 were decisions the model made.
    The other 1757 were the rule path. That is the same conflation
    `_post_cycle_model_flag` had to fix for `last_model_ok`, and a 57% failure that is
    really a 4% one is a monitor that gets ignored.
    """
    records = [_Rec("qwen3.8:27b") for _ in range(900)]
    records += [_Rec(None, source="policy_engine") for _ in range(2000)]
    rep = DoctorReport()
    _check_model_registry(rep, _Cfg(), records)

    check = next(c for c in rep.checks if c.name == "model registry")
    assert check.status.value == "ok", check.detail
    assert "57%" not in check.detail, check.detail
    assert "never asked" in check.detail, (
        "the rule path must be stated rather than folded into the attribution gap: "
        + check.detail
    )
    assert "qwen3.8:27b 900" in check.detail, check.detail
    assert "%" not in check.detail, (
        "no attribution ratio belongs here: nothing the model answered is missing a "
        "model, so there is no gap to express as a percentage: " + check.detail
    )


def test_the_ratio_is_over_the_decisions_the_model_answered():
    """Not over every decision on the journal. Same gap, correct denominator."""
    records = [_Rec("qwen3.8:27b") for _ in range(300)]
    records += [_Rec(None) for _ in range(700)]          # asked, unattributed
    records += [_Rec(None, source="policy_engine") for _ in range(2000)]
    rep = DoctorReport()
    _check_model_registry(rep, _Cfg(), records)

    check = next(c for c in rep.checks if c.name == "model registry")
    assert check.status.value == "warn", check.detail
    assert "700 of the 1000" in check.detail, (
        f"the denominator must be the 1000 asked, not all 3000: {check.detail}"
    )
    assert "70%" in check.detail, check.detail
