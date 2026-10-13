"""The probation retry counter counts one thing, and the budget stops growing.

This is a pinning test, not a regression fix. Nothing here was broken and nothing was
changed: it records the live 2026-10-13 measurement next to the contract it exercised, so
the next reader does not have to re-derive either.

Measured, first open session after deploying source `9fb9066d820f27c9`:
`trend-follow-20260824-001` was returned to probation five times between 10:02 and 10:24 EDT
and its state file reached `probation_restarts: 5`, each event saying "served its full
N-cycle probation budget without submitting an order" with N climbing 13, 14, 15, 15, 16.

`MAX_PROBATION_RESTARTS` is documented as how many times a candidate may be returned to
probation "before the lifecycle stops believing another attempt would produce one". It does
not do that, and deliberately so: `test_returning_to_probation_keeps_counting_retries_
without_a_verdict_from_the_count` pins this branch as unbounded, on the grounds that
retiring the unevaluable candidate here was tried and removed because it shortened the
replay's evidence run until `tools/self_evolution_probe.py` could score nothing.

What actually closes the loop is `_needs_probation`, which grants
`PROBATION_CYCLES * (1 + min(probation_restarts, MAX_PROBATION_RESTARTS))`. Past the third
restart that grant stops growing, so the candidate drains once `cumulative_cycles` reaches
it. The fourth and fifth returns buy no extra budget and the counter becomes bookkeeping
past that point - which is why the first draft of this file asserted a bound on the returns
and was dropped: it contradicted a pinned contract and produced no behavioural gain.

Usage: pytest tests/autopoiesis/test_probation_restart_budget.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.models import StrategyResult, StrategySpec
from autopoiesis.strategy_engine import (
    MAX_PROBATION_RESTARTS,
    PROBATION_CYCLES,
    StrategyLifecycleManager,
    StrategySelector,
)

NOW = datetime(2026, 10, 13, 14, 24, 46, tzinfo=timezone.utc)


def _spec(restarts: int) -> StrategySpec:
    return StrategySpec(
        strategy_id="trend-follow-20260824-001",
        name="SPY trend",
        kind="TREND_FOLLOW",
        symbols=("SPY",),
        parameters={"reference_price": 700.0, "threshold_pct": 0.02, "quantity": 1, "confidence": 0.5},
        max_position_value=25000.0,
        enabled=True,
        lifecycle="PROBATION",
        probation_restarts=restarts,
        created_at=NOW,
        rationale="t",
    )


def _no_outcome(*, cumulative_cycles: int = 16) -> StrategyResult:
    """The shape the live reflection produced: cycles served, nothing submitted, nothing wrong.

    `cumulative_trade_attempts` is 1 because that is what kept the live candidate out of the
    earlier branch. `_is_degenerate_no_exploration` pauses a PROBATION candidate whose
    *lifetime* attempts are zero; the eight SPY cycles recorded between 09:30 and 10:30 were
    all HOLD, but the strategy had attempted a trade before that window.
    """
    return StrategyResult(
        strategy_id="trend-follow-20260824-001",
        cycles=50,
        cumulative_cycles=cumulative_cycles,
        submitted_orders=0,
        rejected_orders=0,
        errors=0,
        score=0.0,
        trade_attempts=1,
        cumulative_trade_attempts=1,
        evaluated_at=NOW,
    )


@pytest.mark.parametrize("restarts", [0, 1, 2, 3, 4, 5])
def test_a_candidate_with_no_outcome_is_returned_to_probation_whatever_its_restart_count(restarts):
    """The live shape at every restart count the session produced, including 5.

    Deliberately asserts the behaviour rather than a bound on it. `probation_restarts` is
    bookkeeping for the unevaluable case and is not an attempt limit; the comment on
    `MAX_PROBATION_RESTARTS` in `strategy_engine.py` records that retiring here was tried
    and reverted for shortening the evidence run `self_evolution_probe.py` depends on.
    """
    manager = StrategyLifecycleManager()

    [decision] = manager.review([_spec(restarts)], [_no_outcome()])

    assert decision.new_lifecycle == "PROBATION"
    assert decision.update == {"probation_restarts": restarts + 1}


def test_the_granted_budget_stops_growing_at_the_cap():
    """What the cap is actually for, pinned against the 2026-10-12 escalation.

    `trend-follow-buy-010` needed 4160 cycles to leave the queue when the budget scaled
    without bound, held 151 of the 155 probation cycles served that day, and the admission
    gate then refused every new candidate for the whole session. The bound is on the granted
    budget and it stays a bound.
    """
    granted = {
        restarts: PROBATION_CYCLES * (1 + min(restarts, MAX_PROBATION_RESTARTS))
        for restarts in (0, 1, 2, 3, 4, 5, 319)
    }

    assert granted[0] == PROBATION_CYCLES
    assert granted[3] == granted[4] == granted[5] == granted[319] == PROBATION_CYCLES * 4


def test_the_queue_drains_rather_than_livelocking():
    """The correction to the first reading of this file's measurement.

    Five restarts in 45 minutes reads like a livelock. It is not: the granted budget is
    finite, so the candidate stops being served once `cumulative_cycles` reaches it. The
    live strategy was at 16 of a 52-cycle grant at 10:24 EDT and still improving, so calling
    that a livelock would have been a wrong conclusion drawn from a right number.
    """
    selector = StrategySelector(min_probation_cycles=PROBATION_CYCLES)
    strategy = _spec(5)
    ceiling = PROBATION_CYCLES * (1 + MAX_PROBATION_RESTARTS)

    assert selector._needs_probation(strategy, _no_outcome(cumulative_cycles=ceiling - 1)) is True
    assert selector._needs_probation(strategy, _no_outcome(cumulative_cycles=ceiling)) is False
