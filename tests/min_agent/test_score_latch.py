"""Regression tests for the HOLD latch.

The defect this file exists to prevent:

    evaluator._score was `1 - (errors + rejections) / cycles`, so a strategy
    that never acted and was never corrected scored a perfect 1.0, while a
    strategy that placed five real orders scored 0.833. StrategySelector takes a
    deterministic argmax over that value, so the system selected the do-nothing
    strategy, which held again, which kept the 1.0, which was selected again.
    800 of the 851 recorded cycles were HOLD.

The only guard against it was disabled twice: it returned False for any
`kind != "FIXED_SIZE"` (the latch winner was a TREND_FOLLOW), and it required
`lifecycle == "PROBATION"` (permanently false once promoted to ACTIVE).
"""

from datetime import datetime, timezone

import pytest

from min_agent.evaluator import (
    INACTION_FLOOR,
    PNL_BONUS,
    SYSTEM_REJECTION_REASONS,
    DeterministicEvaluator,
    _score,
)
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    StrategyResult,
    StrategySpec,
    TradeDecision,
)
from min_agent.strategy_engine import StrategyLifecycleManager, StrategySelector


def make_spec(
    *,
    strategy_id="test-strat",
    kind="HOLD_BASELINE",
    symbols=("SPY",),
    enabled=True,
    lifecycle="ACTIVE",
    created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
    **param_overrides,
):
    params = {}
    if kind == "FIXED_SIZE":
        params = {"action": "BUY", "quantity": 1, "confidence": 0.5}
    if kind == "TREND_FOLLOW":
        params = {"reference_price": 100, "threshold_pct": 0.02, "quantity": 1, "confidence": 0.5}
    params = {**params, **param_overrides}
    return StrategySpec(
        strategy_id=strategy_id,
        name="Test Strategy",
        kind=kind,
        symbols=symbols,
        parameters=params,
        max_position_value=10_000,
        enabled=enabled,
        lifecycle=lifecycle,
        created_at=created_at,
        rationale="test",
    )

NOW = datetime.now(tz=timezone.utc)

# The recorded per-strategy metrics from runtime/min_agent/reflection.json at
# 2026-06-23, i.e. the state that produced the latch.
RECORDED = {
    # strategy_id: (cycles, submitted, rejected, errors, old_score)
    "trend-follow-sell-001": (36, 0, 0, 0, 1.0),
    "fixed-size-buy-001": (6, 5, 1, 0, 0.8333333333333334),
    "trend-follow-buy-001": (6, 4, 2, 0, 0.6666666666666667),
    "fixed-size-sell-001": (2, 0, 2, 0, 0.0),
}


def _result(**kw):
    base = dict(strategy_id="s", cycles=10, submitted_orders=0, rejected_orders=0, errors=0, score=0.0, evaluated_at=NOW)
    base.update(kw)
    return StrategyResult(**base)


def test_pure_inaction_scores_below_any_strategy_that_acted():
    """The core property: HOLD-only can never be the argmax while one trades."""
    hold_only = _score(36, errors=0, rejected_orders=0, strategy_fault_rejections=0, trade_attempts=0)
    traded = _score(6, errors=0, rejected_orders=1, strategy_fault_rejections=1, trade_attempts=6)

    # INACTION_FLOOR, less the headroom reserved for broker-verified PnL.
    assert hold_only == pytest.approx(INACTION_FLOOR * (1.0 - PNL_BONUS))
    assert traded > hold_only


def test_inaction_never_earns_a_perfect_score():
    assert _score(100, errors=0, rejected_orders=0, strategy_fault_rejections=0, trade_attempts=0) < 1.0


def test_old_formula_gave_inaction_the_top_score_and_the_new_one_does_not():
    """Same inputs, both formulas, side by side."""
    for cycles, submitted, rejected, errors, old_score in RECORDED.values():
        attempts = submitted + rejected  # every non-HOLD cycle was an attempt
        new_score = _score(cycles, errors, rejected, strategy_fault_rejections=rejected, trade_attempts=attempts)
        print(f"cycles={cycles} attempts={attempts} old={old_score:.4f} new={new_score:.4f}")

    old_winner = max(RECORDED, key=lambda sid: RECORDED[sid][4])
    assert old_winner == "trend-follow-sell-001"

    new_scores = {
        sid: _score(c, e, r, strategy_fault_rejections=r, trade_attempts=s + r)
        for sid, (c, s, r, e, _old) in RECORDED.items()
    }
    new_winner = max(new_scores, key=new_scores.get)
    assert new_winner != "trend-follow-sell-001"
    assert new_scores["trend-follow-sell-001"] == pytest.approx(INACTION_FLOOR * (1.0 - PNL_BONUS))


def test_absence_of_pnl_evidence_grants_no_credit():
    """No PnL evidence used to mean no penalty, which was a free ride."""
    with_none = _score(10, 0, 0, strategy_fault_rejections=0, trade_attempts=10, realized_pnl=None)
    with_profit = _score(10, 0, 0, strategy_fault_rejections=0, trade_attempts=10, realized_pnl=5.0)
    # Reliable trading earns a high score, but verified profit must beat it:
    # without reserved headroom both saturate at 1.0 and PnL cannot discriminate.
    assert 0.0 < with_none < with_profit <= 1.0
    assert _score(10, 0, 0, strategy_fault_rejections=0, trade_attempts=0, realized_pnl=None) < with_none


def test_broker_verified_pnl_only_adjusts_a_score_earned_by_acting():
    acted, positive, negative = 10, 1, -1
    base = _score(acted, 0, 0, strategy_fault_rejections=0, trade_attempts=acted)
    win = _score(acted, 0, 0, strategy_fault_rejections=0, trade_attempts=acted, realized_pnl=positive)
    loss = _score(acted, 0, 0, strategy_fault_rejections=0, trade_attempts=acted, realized_pnl=negative)

    assert win > base > loss
    # And PnL cannot rescue inaction.
    assert _score(acted, 0, 0, strategy_fault_rejections=0, trade_attempts=0, realized_pnl=positive) < base


def test_zero_cycles_scores_zero():
    assert _score(0, 0, 0, trade_attempts=0) == 0.0


# --- system rejections are not strategy faults -------------------------------


def _snapshot(market_open=True, price=500.0):
    return DataSnapshot(
        symbol="SPY",
        timestamp=NOW,
        market_open=market_open,
        last_price=price,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
    )


def _record(strategy_id, action, status, reason="approved", approved=True, market_open=True, quantity=1):
    return CycleRecord(
        cycle_id=f"c-{strategy_id}-{action}-{status}-{market_open}",
        snapshot=_snapshot(market_open=market_open),
        decision=TradeDecision(
            symbol="SPY", action=action, quantity=quantity, confidence=0.8, rationale="t", strategy_id=strategy_id
        ),
        guardian=GuardianResult(approved=approved, reason=reason),
        execution=ExecutionResult(status=status, order_id=None, filled_quantity=0, message="m"),
        strategy_id=strategy_id,
    )


@pytest.mark.parametrize("reason", sorted(SYSTEM_REJECTION_REASONS))
def test_risk_systems_own_refusals_are_not_counted_as_strategy_faults(reason):
    report = DeterministicEvaluator().evaluate([_record("s", "BUY", "REJECTED", reason=reason, approved=False)])
    metrics = report.strategy_metrics["s"]

    assert metrics.rejected_orders == 1
    assert metrics.strategy_fault_rejections == 0
    # It still tried to trade, so it still counts as exploration.
    assert metrics.trade_attempts == 1


@pytest.mark.parametrize(
    "reason",
    [
        "cannot sell 1 SPY; the agent holds 0. The account may hold more, but those "
        "shares are not the agent's to sell",
        "cannot sell more than known holdings",
        "the agent's own holding of SPY is unknown, so a SELL cannot be bounded; "
        "refusing rather than risk selling the account owner's shares",
        "agent exposure 900.00 + 2500.00 exceeds the 3000.00 limit "
        "(allowlist symbols only; account total is 400.00)",
    ],
)
def test_rejections_caused_by_system_state_are_not_strategy_faults(reason):
    """A rejection the strategy could not have avoided is not its fault.

    These reasons interpolate the offending quantity and the limit breached, so an
    exact-equality test against SYSTEM_REJECTION_REASONS could never match them and
    they were charged to the strategy. The cost was concrete: the only SELL-capable
    strategy in the library was retired with "severe operational failure rate 1.00"
    for three refusals whose stated cause was "the agent holds 0" - a quantity derived
    from the journal, not anything the strategy did. A strategy was removed from the
    library for obeying the system.

    This test previously asserted the opposite for "cannot sell more than known
    holdings", treating it as a fault. That check is `_has_position` against a broker
    snapshot (`guardian.py:90`), the same class of system-derived fact as the journal-
    derived agent holding at `guardian.py:110`.
    """
    report = DeterministicEvaluator().evaluate(
        [_record("s", "SELL", "REJECTED", reason=reason, approved=False)]
    )
    metrics = report.strategy_metrics["s"]

    assert metrics.rejected_orders == 1
    assert metrics.strategy_fault_rejections == 0
    # Still an attempt: the strategy acted and the system stopped it.
    assert metrics.trade_attempts == 1


@pytest.mark.parametrize(
    "reason",
    [
        "position value exceeds hard limit",
        "strategy position value exceeds hard limit",
        "decision confidence below minimum",
        "symbol is outside the allowlist",
    ],
)
def test_a_genuine_strategy_fault_is_still_charged(reason):
    """The fault counter still does its job.

    Each of these is the strategy asking for something its own specification makes
    impermissible: a position larger than the hard limit, more than it can afford, or a
    symbol it is not permitted to trade.
    """
    report = DeterministicEvaluator().evaluate(
        [_record("s", "BUY", "REJECTED", reason=reason, approved=False)]
    )

    assert report.strategy_metrics["s"].strategy_fault_rejections == 1


def test_closed_market_cycles_are_not_trade_attempts():
    report = DeterministicEvaluator().evaluate(
        [_record("s", "BUY", "REJECTED", reason="market is closed", approved=False, market_open=False)]
    )

    assert report.strategy_metrics["s"].trade_attempts == 0


# --- lifecycle ---------------------------------------------------------------


def test_daily_trade_limit_rejections_do_not_retire_a_strategy():
    """fixed-size-sell-001 was RETIRED with 'severe operational failure rate 1.00'
    because every one of its orders was correctly blocked."""
    manager = StrategyLifecycleManager(severe_failure_rate=0.75, max_rejection_rate=0.5)
    strategy = make_spec(strategy_id="s", kind="FIXED_SIZE", lifecycle="ACTIVE", action="SELL", quantity=1)
    result = _result(cycles=2, rejected_orders=2, strategy_fault_rejections=0, trade_attempts=2)

    assert manager.review([strategy], [result]) == []


def test_genuine_rejection_storms_still_retire():
    manager = StrategyLifecycleManager(severe_failure_rate=0.75, max_rejection_rate=0.5)
    strategy = make_spec(strategy_id="s", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    result = _result(cycles=2, rejected_orders=2, strategy_fault_rejections=2, trade_attempts=2)

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "RETIRED"


def test_unsplit_result_is_read_conservatively():
    """A result without the split must not silently under-charge rejections."""
    manager = StrategyLifecycleManager(severe_failure_rate=0.75)
    strategy = make_spec(strategy_id="s", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    result = _result(cycles=2, rejected_orders=2, trade_attempts=2)
    assert result.strategy_fault_rejections is None

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "RETIRED"


def test_promoted_hold_only_strategy_is_paused():
    """The guard used to require lifecycle == PROBATION, so a promoted strategy
    that stopped acting was never caught."""
    manager = StrategyLifecycleManager(min_active_cycles=5)
    strategy = make_spec(strategy_id="trend", kind="TREND_FOLLOW", lifecycle="ACTIVE")
    result = _result(strategy_id="trend", cycles=36, trade_attempts=0, score=1.0)

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"
    assert "no exploration" in decision.reason


def test_baseline_strategies_are_exempt_from_the_degenerate_guard():
    manager = StrategyLifecycleManager(min_active_cycles=5)
    strategy = make_spec(strategy_id="b", kind="HOLD_BASELINE", lifecycle="BASELINE")
    result = _result(strategy_id="b", cycles=50, trade_attempts=0)

    assert manager.review([strategy], [result]) == []


# --- selector ----------------------------------------------------------------


def test_selector_prefers_the_strategy_that_actually_traded():
    """Reproduces the recorded latch through the real selector."""
    hold_only = make_spec(strategy_id="trend-follow-sell-001", kind="TREND_FOLLOW", lifecycle="ACTIVE")
    traded = make_spec(strategy_id="tiny-fixed-size-001", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    results = [
        # Inaction scores 0.25 * 0.9 = 0.225; acting reliably scores 1.0 * 0.9 = 0.9.
        _result(strategy_id="trend-follow-sell-001", cycles=36, score=0.225, trade_attempts=0),
        _result(strategy_id="tiny-fixed-size-001", cycles=15, submitted_orders=13, rejected_orders=2,
                score=0.9, trade_attempts=15, strategy_fault_rejections=0),
    ]

    chosen = StrategySelector().select([hold_only, traded], results)

    assert chosen.strategy_id == "tiny-fixed-size-001"


def test_selector_probes_when_nothing_has_trade_evidence():
    """A fresh install with no history must acquire evidence, not HOLD forever."""
    holder = make_spec(strategy_id="idle", kind="TREND_FOLLOW", lifecycle="ACTIVE")
    buyer = make_spec(strategy_id="probe", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    results = [
        _result(strategy_id="idle", cycles=20, score=0.25, trade_attempts=0),
    ]

    chosen = StrategySelector().select([holder, buyer], results)

    assert chosen.strategy_id == "probe"


def test_selector_does_not_probe_before_there_is_a_window_to_learn_from():
    holder = make_spec(strategy_id="idle", kind="TREND_FOLLOW", lifecycle="ACTIVE")
    results = [
        _result(strategy_id="idle", cycles=2, score=0.25, trade_attempts=0),
    ]

    assert StrategySelector(exploration_floor_cycles=5).select([holder], results).strategy_id == "idle"


def test_selector_never_probes_with_a_sell():
    seller = make_spec(strategy_id="seller", kind="FIXED_SIZE", lifecycle="ACTIVE", action="SELL", quantity=1)
    results = [
        _result(strategy_id="seller", cycles=20, score=0.25, trade_attempts=0),
    ]

    chosen = StrategySelector().select([seller], results)

    # Falls back to the plain argmax; it must not turn a sell-only library into
    # an unhedged short.
    assert chosen.parameters.get("action") == "SELL"


def test_selector_prefers_the_smallest_legal_probe():
    big = make_spec(strategy_id="big", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=20)
    small = make_spec(strategy_id="small", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    results = [
        _result(strategy_id="big", cycles=20, trade_attempts=0, score=0.25),
        _result(strategy_id="small", cycles=20, trade_attempts=0, score=0.25),
    ]

    assert StrategySelector().select([big, small], results).strategy_id == "small"


# --- reflection plumbing -----------------------------------------------------


def test_strategy_result_carries_the_new_fields_through_reflection(tmp_path):
    from min_agent.reflection_memory import ReflectionMemory

    records = [_record("trader", "BUY", "SUBMITTED"), _record("idler", "HOLD", "SKIPPED", quantity=0)]
    memory = ReflectionMemory(tmp_path / "reflection.json")
    record = memory.reflect(records)
    memory.save(record)

    results = {r.strategy_id: r for r in ReflectionMemory(tmp_path / "reflection.json").strategy_results(record)}

    assert results["trader"].trade_attempts == 1
    assert results["idler"].trade_attempts == 0
    assert results["trader"].score > results["idler"].score


# --- the real journal --------------------------------------------------------


def test_real_recorded_journal_no_longer_elects_a_hold_only_strategy():
    """Replays the actual 851 recorded cycles.

    Skips when runtime/ is absent (it is gitignored), so this is a live check in
    the working tree rather than a CI guarantee. The frozen RECORDED fixture
    above is the portable version of the same assertion.
    """
    from pathlib import Path

    from min_agent.journal import JsonlJournal

    journal = Path("runtime/min_agent/journal.jsonl")
    if not journal.exists():
        pytest.skip("runtime journal not present")

    records = JsonlJournal(journal).read_all()
    if not records:
        pytest.skip("journal is empty")

    metrics = DeterministicEvaluator().evaluate(records).strategy_metrics
    assert metrics, "expected per-strategy metrics from the recorded journal"

    best = max(metrics.values(), key=lambda m: m.score)
    hold_only = [m for m in metrics.values() if m.action_counts.get("HOLD", 0) == m.cycles]

    for metric in hold_only:
        assert metric.score <= INACTION_FLOOR * (1.0 - PNL_BONUS) + 1e-9, (
            f"{metric.strategy_id} only held yet scored {metric.score}"
        )
    if hold_only and len(metrics) > len(hold_only):
        assert best.trade_attempts > 0, f"argmax winner {best.strategy_id} never attempted a trade"
