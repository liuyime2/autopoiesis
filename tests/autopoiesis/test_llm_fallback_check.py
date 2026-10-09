"""A fallback must not be able to hide.

The objective states that no failure may be masked by a fallback. When the model
call exceeds the engine timeout, `HybridDecisionEngine` substitutes the
deterministic policy engine and returns a decision that looks entirely ordinary.
That is the safe direction - the substitute is still Guardian-gated and a
deterministic rule is not a hallucinating one - but the LLM has silently left the
decision loop.

Nothing reported it. `model_registry` counts the source, so a fallback is not
`UNATTRIBUTED` and looks fully accounted for; `decision quality` scores whatever
decision arrived. The two 2026-09-28 fallbacks appear in the journal and in neither
warning. These tests pin that they now appear somewhere.
"""
from __future__ import annotations

from autopoiesis.doctor import DoctorReport, _check_llm_fallback


class _Cfg:
    model = "qwen3.8:27b"
    allowlist = ["SPY"]


class _Dec:
    def __init__(self, source):
        self.decision_source = source
        self.action = "HOLD"


class _Rec:
    def __init__(self, source):
        self.decision = _Dec(source)


def _run(*sources):
    report = DoctorReport()
    _check_llm_fallback(report, _Cfg(), [_Rec(s) for s in sources])
    return next(c for c in report.checks if c.name == "llm fallback")


def test_all_model_decisions_is_ok():
    check = _run(*(["llm"] * 50))
    assert check.status.value == "ok", check.detail
    assert "0 of 50" in check.detail
    assert "50 came from the model" in check.detail


def test_one_bypassed_model_is_still_reported():
    """A threshold here would hide exactly the case that matters most - the first
    occurrence, before anyone is watching."""
    check = _run(*(["llm"] * 67), "fallback_policy_engine")
    assert check.status.value == "warn", f"{check.status.value}: {check.detail}"
    assert "1 of 68" in check.detail
    assert "instead of the model" in check.detail


def test_two_consecutive_fallbacks_out_of_three_is_a_failure():
    """I expected a warning here and the check returned a failure, correctly: two
    fallbacks out of three decisions is a 67% bypass rate, and the share is the
    right thing to escalate on rather than the raw count. A count threshold would
    call this 'two, that's fine' when the model authored a third of the decisions."""
    check = _run("llm", "fallback_policy_engine", "fallback_policy_engine")
    assert check.status.value == "fail", f"{check.status.value}: {check.detail}"
    assert "2 of 3" in check.detail
    assert "67%" in check.detail


def test_a_low_rate_of_bypasses_stays_a_warning():
    """The real shape on 2026-09-28 seen against a full session: rare, but not
    zero, and worth saying."""
    check = _run(*(["llm"] * 67), "fallback_policy_engine")
    assert check.status.value == "warn"
    assert "1 of 68" in check.detail


def test_a_sustained_bypass_is_a_failure():
    """Most decisions not authored by the model is not a warning."""
    check = _run(*(["fallback_policy_engine"] * 20), *(["llm"] * 3))
    assert check.status.value == "fail", f"{check.status.value}: {check.detail}"
    assert "87%" in check.detail


def test_the_hint_says_the_substitute_is_still_gated():
    """The message must not imply the decision was unsafe - it was not."""
    check = _run("llm", "fallback_policy_engine")
    assert "Guardian-gated" in check.hint


def test_no_decisions_at_all_is_ok_not_a_crash():
    check = _run()
    assert check.status.value == "ok"
