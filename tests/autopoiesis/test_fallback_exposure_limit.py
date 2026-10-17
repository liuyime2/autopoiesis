"""When the model does not answer, a rule may reduce but must not open a position.

Reported 2026-10-14: on 10-13, 16 of that day's sells were placed by the fallback rather
than by any decision, and the fallback took the rule's action verbatim - including a BUY.
A parse failure therefore became a new position with no judgement behind it, at the
moment the system had already said it could not think.

The asymmetry is the whole argument. Reducing exposure while the model is silent is still
acting on the model's own strategy. Opening one is taking a position nobody asked for.

All fixtures synthetic: no model, no broker, no account.

Usage: pytest tests/autopoiesis/test_fallback_exposure_limit.py -q
"""

from __future__ import annotations

import pytest

from autopoiesis.llm_decision import HybridDecisionEngine


class _Decision:
    """The minimum TradeDecision shape the limit reads and rewrites."""

    def __init__(self, action: str, quantity: int = 1, rationale: str = "rule"):
        self.action = action
        self.quantity = quantity
        self.rationale = rationale
        self.hold_reason = None

    def model_copy(self, update=None):
        clone = _Decision(self.action, self.quantity, self.rationale)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


def _limit(action: str, quantity: int = 1):
    engine = HybridDecisionEngine.__new__(HybridDecisionEngine)
    return engine._fallback_exposure_note(_Decision(action, quantity))


# --- the ability that was removed --------------------------------------------


@pytest.mark.parametrize("action", ["BUY", "SHORT"])
def test_an_exposure_adding_action_is_downgraded_to_hold(action):
    """The defect, asserted directly. Both of these add exposure."""
    note = _limit(action)

    assert note is not None, f"{action} must not survive a model that did not answer"
    assert action in note, (
        "the note must name what was refused, or the record hides the refusal: " + note
    )
    assert "may only hold or reduce" in note


def test_a_short_is_blocked_too_because_it_opens_exposure():
    """SHORT opens or adds to a short. It is the same defect facing the other way."""
    assert _limit("SHORT") is not None


# --- what must keep working ---------------------------------------------------


@pytest.mark.parametrize("action", ["HOLD", "SELL", "COVER"])
def test_reducing_and_holding_actions_are_untouched(action):
    """SELL reduces a long and COVER reduces a short, so both are allowed through.

    Narrowing these would strand a position the system is trying to exit while the model
    is unavailable, which is the failure the fallback exists to prevent.
    """
    assert _limit(action) is None, f"{action} reduces exposure and must pass unchanged"


def test_the_note_is_a_sentence_or_none_and_never_a_decision():
    """`_fallback` composes it into the rationale, so a decision would be ignored."""
    for action in ("BUY", "SELL", "HOLD"):
        note = _limit(action)
        assert note is None or isinstance(note, str)


def test_the_allowed_set_is_stated_once_and_is_the_reducing_one():
    """The set is named rather than written inline, so it cannot drift per call site."""
    assert frozenset({"HOLD", "SELL", "COVER"}) == HybridDecisionEngine.REDUCING_ACTIONS
    for adding in ("BUY", "SHORT"):
        assert adding not in HybridDecisionEngine.REDUCING_ACTIONS


def test_every_order_action_is_classified():
    """No action may be unclassified: an unclassified one would silently pass.

    Read from the model's own literal rather than a copy, so an action added to the
    schema fails this test instead of reaching the fallback unexamined.
    """
    from typing import get_args

    from autopoiesis.models import ORDER_ACTIONS, Action

    declared = set(get_args(Action))
    classified = HybridDecisionEngine.REDUCING_ACTIONS | {"BUY", "SHORT"}
    assert declared == classified, (
        f"every Action must be either reducing or blocked; unclassified: "
        f"{declared - classified}"
    )
    assert {"BUY", "SELL", "SHORT", "COVER"} == ORDER_ACTIONS, ORDER_ACTIONS
    assert {"BUY", "SHORT"} & ORDER_ACTIONS == {"BUY", "SHORT"}

# --- and it must actually be wired in ----------------------------------------


class _Spec:
    strategy_id = "fixed-size-spy-buy-001"


class _Snapshot:
    symbol = "SPY"


def _real_fallback(rule_action: str, quantity: int = 1):
    """Drive the real `_fallback`, so the composition is tested and not just the helper."""
    engine = HybridDecisionEngine.__new__(HybridDecisionEngine)
    engine._selected_spec = _Spec()
    engine._selected_provenance = None
    engine._rule_decision = _Decision(rule_action, quantity, "rule says so")
    return engine._fallback(_Snapshot(), "ValueError: LLM response did not contain a JSON object")


def test_a_parse_failure_cannot_become_a_new_position():
    """The end-to-end statement: a BUY the rule wanted, refused because the model was silent."""
    decision = _real_fallback("BUY", quantity=3)

    assert decision.action == "HOLD", (
        "a parse failure must not become a position: the rule wanted BUY and the "
        f"fallback produced {decision.action}"
    )
    assert decision.quantity == 0
    assert decision.decision_source == "fallback_policy_engine", (
        "it is still a fallback decision, and must say so"
    )
    assert decision.rule_action == "BUY", (
        "the record must keep what the rule wanted, or the refusal is invisible"
    )
    assert "llm_unavailable" in decision.rationale


def test_a_parse_failure_can_still_exit_a_position():
    """The other half: reducing must survive, or the limit strands a position."""
    decision = _real_fallback("SELL", quantity=1)

    assert decision.action == "SELL", (
        "a fallback that cannot sell cannot get out of a position while the model is down"
    )
    assert decision.quantity == 1
    assert decision.decision_source == "fallback_policy_engine"
    assert "llm_unavailable" in decision.rationale
