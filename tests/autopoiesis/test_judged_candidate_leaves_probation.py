"""A candidate judged on its decisions must not be handed another budget.

Measured 2026-10-15 with the market open, against the live library:

    trend-follow-20260824-001   43 informative scored decisions (all GOOD_HOLD)   98 returns
    trend-follow-20260804-001   15 informative scored decisions (all GOOD_TRADE)  69 returns
    trend-follow-buy-010         0 decisions                                     362 returns

Neither of the first two had ever submitted an order, because Guardian's own limits refused
every one they emitted - 63 refusals for the daily trade cap in one session - and weakening
those is not on the table. So the two candidates with the most evidence were the two that
could never leave the queue, and the queue stayed at or above its cap, refusing every new
candidate including the conditional exits the positions without one need.

`strategy_engine` already names the fix in its own comment: "the counterfactual ledger's
scored verdicts for the decisions this strategy actually produced. That is a real bar
rather than an infinite probation." It was never consulted, because the
`submitted_orders <= 0` check runs first.

All fixtures synthetic.

Usage: pytest tests/autopoiesis/test_judged_candidate_leaves_probation.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.models import StrategyResult, StrategySpec
from autopoiesis.strategy_engine import StrategyLifecycleManager

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
THRESHOLD = 10


class _Evidence:
    """The shape `_promotion_evidence_gate` reads."""

    def __init__(self, *, scored: int, verdict: str = "PASS_SCREENED"):
        self.scored = scored
        self.verdict = verdict


def _spec(strategy_id="candidate-001"):
    return StrategySpec.model_validate(
        {
            "strategy_id": strategy_id, "name": strategy_id, "symbols": ["SPY"],
            "created_at": "2026-10-06T00:00:00Z", "rationale": "synthetic fixture",
            "enabled": True, "kind": "TREND_FOLLOW", "max_position_value": 1000.0,
            "parameters": {
                "reference_price": 700.0, "threshold_pct": 0.01,
                "quantity": 1, "confidence": 0.6,
            },
            "lifecycle": "PROBATION",
        }
    )


def _result(strategy, *, cycles=52, attempts=32, submitted=0):
    return StrategyResult(
        strategy_id=strategy.strategy_id, cycles=cycles, submitted_orders=submitted,
        rejected_orders=0, errors=0, realized_pnl=0.0,
        cumulative_cycles=cycles, cumulative_trade_attempts=attempts,
        score=0.0, evaluated_at=NOW,
    )


def _review(strategy, *, scored, verdict="PASS_SCREENED", attempts=32):
    manager = StrategyLifecycleManager()
    decisions = manager.review(
        [strategy], [_result(strategy, attempts=attempts)],
        {strategy.strategy_id: _Evidence(scored=scored, verdict=verdict)},
    )
    return next((d for d in decisions if d.strategy.strategy_id == strategy.strategy_id), None)


# --- the loop breaks for a judged candidate ----------------------------------


def test_a_candidate_with_enough_scored_decisions_is_not_handed_another_budget():
    """43 informative decisions and 98 returns is the measured case."""
    decision = _review(_spec(), scored=43)

    assert decision is None or decision.new_lifecycle != "PROBATION", (
        "a candidate judged on 43 scored decisions must not be told it has no outcome: "
        f"{decision.new_lifecycle if decision else None}"
    )


def test_a_judged_candidate_falls_through_to_the_gate_rather_than_looping():
    """The assertion is that the loop breaks, not that promotion happens.

    Promotion additionally requires a *clean* result - an eligibility version and behaviour
    digest that match the current spec - which these fixtures do not carry. That gate is
    unchanged and is not what this rule is about. What this rule changes is that a judged
    candidate reaches it instead of being handed another budget on the way.
    """
    decision = _review(_spec(), scored=43, verdict="PASS_SCREENED")

    assert decision is None or decision.new_lifecycle != "PROBATION", (
        "a judged candidate must reach the gate, not be returned for another budget: "
        f"{decision.new_lifecycle if decision else None}"
    )


def test_a_judged_candidate_that_fails_the_gate_is_not_promoted():
    """And a rejection is a verdict, not another budget."""
    decision = _review(_spec(), scored=43, verdict="REJECT_POOR_DECISIONS")

    assert decision is None or decision.new_lifecycle != "ACTIVE", (
        "a rejected candidate must not be promoted"
    )


# --- and the old behaviour is untouched where it still applies ----------------


def test_a_candidate_with_too_few_scored_decisions_still_returns_to_probation():
    """`trend-follow-buy-010` has 0 decisions and 362 returns; that case is unchanged."""
    decision = _review(_spec(), scored=0)

    assert decision is not None
    assert decision.new_lifecycle == "PROBATION", decision.new_lifecycle
    assert decision.update.get("probation_restarts") == 1


@pytest.mark.parametrize("scored", [0, 1, 9])
def test_every_count_below_the_threshold_is_unchanged(scored):
    decision = _review(_spec(), scored=scored)

    assert decision is not None and decision.new_lifecycle == "PROBATION"


def test_the_threshold_is_the_one_the_gate_uses():
    """Not a second number invented here, which is how two answers to one question start."""
    assert StrategyLifecycleManager().min_promotion_scored_decisions == THRESHOLD
    at = _review(_spec(), scored=THRESHOLD)
    below = _review(_spec(), scored=THRESHOLD - 1)

    assert at is None or at.new_lifecycle != "PROBATION"
    assert below is not None and below.new_lifecycle == "PROBATION"


def test_the_old_message_said_there_was_no_outcome_to_judge():
    """The reason a reader sees must not claim the candidate was unjudged.

    It was the message itself that made this look like a candidate with nothing to go on,
    when the ledger had scored 43 of its decisions.
    """
    decision = _review(_spec(), scored=0)

    assert "no outcome to judge" not in decision.reason or "scored" in decision.reason, (
        f"the reason must state how many decisions were scored: {decision.reason}"
    )
