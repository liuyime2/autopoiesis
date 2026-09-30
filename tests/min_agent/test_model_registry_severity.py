"""Severity must be proportional to how much of the record can be attributed.

The model-registry check warned only when *every* decision was unattributed, so a
record that is 93% unattributed - 920 of 987, the state it is actually in - reported
OK on the strength of the 67 decisions that do carry a model name. An all-or-nothing
threshold on a quantity that is inherently a ratio can only be right at one value.
"""
from __future__ import annotations

from min_agent.doctor import DoctorReport, _check_model_registry
from min_agent.model_registry import build


class _Cfg:
    model = "qwen3.8:27b"
    allowlist = ["SPY"]


class _Dec:
    def __init__(self, model):
        if model is not None:
            self.model = model

    def __bool__(self):
        return True


class _Rec:
    def __init__(self, model):
        self.decision = _Dec(model)


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
