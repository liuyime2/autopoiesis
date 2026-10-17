"""A stale refusal is the system's refusal, not the strategy's fault.

Measured defect, not hypothetical. 2026-10-13, the first open session after deploying source
`9fb9066d820f27c9`: at 09:34:40 EDT the BIL cycle's price quote was 1546s old against the 900s
`stale_after_seconds` limit, so Guardian refused with

    'the price quote is 1611s stale, beyond the 900s limit'

and sent nothing. Fifty-eight seconds later `STRATEGY_LIFECYCLE_UPDATED` retired
`fixed-size-bil-sell-001` with `severe operational failure rate 1.00`, charged for that one
cycle.

Why it was charged: `is_system_rejection` matched either the exact strings in
`SYSTEM_REJECTION_REASONS` or the prefixes in `SYSTEM_REJECTION_REASON_PREFIXES`. The
non-interpolated branch's text, `data snapshot is stale`, was in the exact set. The
interpolated branch's text - `f"the {label} is {abs(age):.0f}s {direction}, beyond the
{limit}s limit"` - was in neither. So a refusal the strategy could not have influenced, and
that Guardian had correctly enforced, was booked as `strategy_fault_rejections`, and with one
cycle in the window `failure_rate` came to exactly 1.00.

This is the same defect class the prefix list already fixed once. The comment on that list
records a live incident of its own: interpolating messages escaped exact equality, and the
library's only SELL-capable strategy was retired for obeying the system. The staleness
messages were the reachable ones left out.

Nothing here loosens `stale_after_seconds` or relaxes Guardian. Guardian still refuses the
stale quote; only the learner stops blaming the strategy for the refusal.

Usage: pytest tests/autopoiesis/test_staleness_not_strategy_fault.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from synthetic_evidence import synthetic_clean_record

from autopoiesis.evaluator import DeterministicEvaluator, is_system_rejection
from autopoiesis.guardian import Guardian
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    PositionSnapshot,
    StrategyResult,
    StrategySpec,
    TradeDecision,
)
from autopoiesis.strategy_engine import StrategyLifecycleManager

NOW = datetime(2026, 10, 13, 13, 34, 40, tzinfo=timezone.utc)
LIMIT = 900
STRATEGY_ID = "fixed-size-bil-sell-001"


def _guardian(limit: int = LIMIT) -> Guardian:
    return Guardian(
        allowlist=frozenset({"BIL", "SPY"}),
        max_position_value=25000.0,
        max_daily_loss=500.0,
        max_total_exposure=90000.0,
        max_trades_per_day=100,
        min_confidence=0.5,
        max_snapshot_age_seconds=limit,
    )


def _snapshot(*, clock: datetime, price_time: datetime | None, symbol: str = "BIL") -> DataSnapshot:
    # The live account held 209 BIL, which is what let a SELL be sized at all.
    return DataSnapshot(
        symbol=symbol,
        timestamp=clock,
        market_open=True,
        last_price=91.505,
        source="alpaca",
        account=AccountSnapshot(
            equity=99815.0, cash=37824.41, buying_power=324872.0, portfolio_value=99815.0, daily_loss=0.0
        ),
        positions=(PositionSnapshot(symbol=symbol, quantity=209.0, market_value=209.0 * 91.505),),
        quote_time=price_time,
    )


def _decision(action: str = "SELL") -> TradeDecision:
    return TradeDecision(symbol="BIL", action=action, quantity=1, confidence=0.5, rationale="t")


def _live_refusal(clock: datetime, price_time: datetime | None) -> str:
    """The refusal Guardian actually produced for these inputs, not a copied literal."""
    result = _guardian().review(_decision(), _snapshot(clock=clock, price_time=price_time), now=clock)
    assert result.approved is False
    return result.reason


# --- the classification ------------------------------------------------------


def test_the_exact_message_from_the_live_session_is_a_system_rejection():
    """The string read out of journal.jsonl at 09:34:40 EDT on 2026-10-13.

    `1611s` against a `900s` limit is the observed gap between the cycle timestamp and the
    quote's own observation time as Guardian measured them.
    """
    reason = "the price quote is 1611s stale, beyond the 900s limit"

    assert is_system_rejection(reason)


@pytest.mark.parametrize("label", ["snapshot", "price quote"])
@pytest.mark.parametrize("direction", ["stale", "in the future"])
def test_every_staleness_refusal_guardian_can_emit_is_a_system_rejection(label, direction):
    """The prefixes are pinned to the code path, not to a hand-copied literal.

    `_stale_reason` chooses between two labels and two directions and interpolates the age
    and the limit into both. A test that copied today's wording would keep passing after
    somebody reworded it and silently reclassified the message back to a strategy fault,
    which is exactly the failure this file exists to prevent. So the string is produced here
    by the real Guardian over the real branches.
    """
    price_time = NOW - timedelta(seconds=1611) if direction == "stale" else NOW + timedelta(seconds=1611)
    reason = _live_refusal(NOW, price_time)

    assert reason.startswith(f"the {label} is ") or label == "snapshot", reason
    assert direction in reason, reason
    assert is_system_rejection(reason), reason


def test_the_non_interpolated_staleness_message_is_still_a_system_rejection():
    """The exact-string branch stays covered alongside the interpolated ones."""
    reason = _guardian(limit=0).review(_decision(), _snapshot(clock=NOW, price_time=None), now=NOW).reason

    assert is_system_rejection(reason), reason


def test_a_fresh_quote_is_not_refused_for_staleness_at_all():
    """The guard must not become a permanent disable: a quote from seconds ago still trades.

    `agent_position_quantity` is what the daemon passes from its own journal-derived holding;
    without it Guardian refuses any SELL as unbounded, which is a different refusal and would
    make this test pass for the wrong reason.
    """
    result = _guardian().review(
        _decision(),
        _snapshot(clock=NOW, price_time=NOW - timedelta(seconds=2)),
        agent_position_quantity=209.0,
        now=NOW,
    )

    assert result.approved is True
    assert result.reason == "approved"


# --- the live consequence ----------------------------------------------------


def _cycle(reason: str) -> CycleRecord:
    return synthetic_clean_record(CycleRecord(
        cycle_id="c-bil-stale",
        symbol="BIL",
        snapshot=_snapshot(clock=NOW, price_time=NOW - timedelta(seconds=1611)),
        decision=TradeDecision(
            symbol="BIL", action="SELL", quantity=1, confidence=0.5, rationale="t", strategy_id=STRATEGY_ID
        ),
        guardian=GuardianResult(approved=False, reason=reason),
        execution=ExecutionResult(status="REJECTED", order_id=None, filled_quantity=0, message="m"),
        strategy_id=STRATEGY_ID,
    ))


def _spec() -> StrategySpec:
    return StrategySpec(
        strategy_id=STRATEGY_ID,
        name="BIL sell",
        kind="FIXED_SIZE",
        symbols=("BIL",),
        parameters={"action": "SELL", "quantity": 1, "confidence": 0.5},
        max_position_value=25000.0,
        enabled=True,
        lifecycle="PROBATION",
        created_at=NOW,
        rationale="t",
    )


def test_a_stale_refusal_is_not_counted_as_a_strategy_fault():
    reason = "the price quote is 1611s stale, beyond the 900s limit"

    metrics = DeterministicEvaluator().evaluate([_cycle(reason)]).strategy_metrics[STRATEGY_ID]

    assert metrics.rejected_orders == 1
    assert metrics.strategy_fault_rejections == 0
    # It still asked to trade, so it still counts as exploration.
    assert metrics.trade_attempts == 1


def test_a_stale_refusal_alone_cannot_retire_a_strategy():
    """`fixed-size-bil-sell-001`, reproduced: one stale refusal, retired at failure_rate 1.00.

    The live inputs go through the real evaluator and the real lifecycle manager, and the
    verdict is what comes back. Before the fix this returned
    `StrategyLifecycleDecision(RETIRED, 'severe operational failure rate 1.00')`.
    """
    reason = "the price quote is 1611s stale, beyond the 900s limit"
    metrics = DeterministicEvaluator().evaluate([_cycle(reason)]).strategy_metrics[STRATEGY_ID]

    manager = StrategyLifecycleManager(severe_failure_rate=0.75, max_rejection_rate=0.5)
    result = StrategyResult(
        strategy_id=STRATEGY_ID,
        cycles=metrics.cycles,
        submitted_orders=metrics.submitted_orders,
        rejected_orders=metrics.rejected_orders,
        errors=metrics.errors,
        score=0.0,
        evaluated_at=NOW,
    ).model_copy(update={
        "strategy_fault_rejections": metrics.strategy_fault_rejections,
        "trade_attempts": metrics.trade_attempts,
    })

    assert manager.review([_spec()], [result]) == [], (
        "a strategy refused because the market did not print must not be retired for the refusal"
    )


def test_a_genuine_fault_still_retires_on_the_same_inputs():
    """The floor is only about attribution. One real fault on one cycle is still one fault.

    This is what would have happened to `fixed-size-bil-sell-001` if the refusal had named
    something the strategy could have changed, and it is the reason the fix cannot be
    expressed as "never retire on a small sample".
    """
    reason = "strategy position value exceeds hard limit"
    metrics = DeterministicEvaluator().evaluate([_cycle(reason)]).strategy_metrics[STRATEGY_ID]
    result = StrategyResult(
        strategy_id=STRATEGY_ID,
        cycles=metrics.cycles,
        submitted_orders=metrics.submitted_orders,
        rejected_orders=metrics.rejected_orders,
        errors=metrics.errors,
        score=0.0,
        evaluated_at=NOW,
    ).model_copy(update={
        "strategy_fault_rejections": metrics.strategy_fault_rejections,
        "trade_attempts": metrics.trade_attempts,
    })

    [decision] = StrategyLifecycleManager(severe_failure_rate=0.75).review([_spec()], [result])

    assert decision.new_lifecycle == "RETIRED"


# --- the prefix must not be a net -------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        # The four `evaluator.py` deliberately leaves charged, verbatim from its own comment.
        "position value exceeds hard limit",
        "strategy position value exceeds hard limit",
        "decision confidence below minimum",
        "symbol is outside the allowlist",
    ],
)
def test_a_genuine_fault_is_still_charged(reason):
    """Widening the prefix list must not swallow a refusal the strategy is answerable for."""
    metrics = DeterministicEvaluator().evaluate([_cycle(reason)]).strategy_metrics[STRATEGY_ID]

    assert metrics.strategy_fault_rejections == 1


@pytest.mark.parametrize(
    "reason",
    [
        "symbol is a warrant, unit or right, not a stock",
        "symbol is not a tradable US equity on a major exchange",
        "insufficient buying power",
        "conflicting open order exists",
        "market is closed",
        "daily loss limit reached",
    ],
)
def test_an_existing_system_rejection_is_still_one(reason):
    """The reasons the exact-match set already covers keep their classification.

    Read out of the 2026-10-13 session's rejection reasons plus the exact set itself, so that
    a future edit to the prefix list has to make a decision about each of them rather than
    inheriting one.
    """
    metrics = DeterministicEvaluator().evaluate([_cycle(reason)]).strategy_metrics[STRATEGY_ID]

    assert metrics.strategy_fault_rejections == 0


def test_the_price_floor_is_a_system_rejection_and_stays_one():
    """PFAI at 09:38:41 EDT, for the record.

    `symbol is priced below the minimum` was already in `SYSTEM_REJECTION_REASONS`. A symbol
    selected by the attention filter at $5.00 and snapshotted at $4.755 is refused by the
    market's price, not by anything the strategy specified, and this test is here so that
    widening the prefix list is not the reason that stops being true.
    """
    metrics = DeterministicEvaluator().evaluate(
        [_cycle("symbol is priced below the minimum")]
    ).strategy_metrics[STRATEGY_ID]

    assert metrics.strategy_fault_rejections == 0
