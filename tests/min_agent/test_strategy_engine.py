from datetime import datetime, timezone

from min_agent.models import AccountSnapshot, DataSnapshot, StrategyResult, StrategySpec
from min_agent.strategy_engine import (
    INCUMBENT_SHARE,
    PROBATION_CYCLES,
    StrategyExecutor,
    StrategyLibrary,
    StrategyLifecycleManager,
    StrategySelector,
)


def make_snapshot(*, last_price=100, symbol="SPY", market_open=True):
    return DataSnapshot(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        market_open=market_open,
        last_price=last_price,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
    )


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


def test_hold_baseline_returns_hold():
    executor = StrategyExecutor()
    spec = make_spec(kind="HOLD_BASELINE")
    decision = executor.decide(spec, make_snapshot())

    assert decision.action == "HOLD"
    assert decision.strategy_id == "test-strat"


def test_fixed_size_buy_returns_buy():
    executor = StrategyExecutor()
    spec = make_spec(kind="FIXED_SIZE", action="BUY", quantity=5)
    decision = executor.decide(spec, make_snapshot(last_price=100))

    assert decision.action == "BUY"
    assert decision.quantity == 5
    assert decision.strategy_id == "test-strat"


def test_fixed_size_caps_quantity_by_max_position_value():
    executor = StrategyExecutor()
    spec = make_spec(kind="FIXED_SIZE", action="BUY", quantity=1000)
    decision = executor.decide(spec, make_snapshot(last_price=100))

    assert decision.action == "BUY"
    assert decision.quantity <= 100  # max_position_value / last_price


def test_trend_follow_buys_above_threshold():
    executor = StrategyExecutor()
    spec = make_spec(kind="TREND_FOLLOW", reference_price=100, threshold_pct=0.02, quantity=3)
    decision = executor.decide(spec, make_snapshot(last_price=103))

    assert decision.action == "BUY"
    assert decision.quantity == 3


def test_trend_follow_sells_below_threshold():
    executor = StrategyExecutor()
    spec = make_spec(kind="TREND_FOLLOW", reference_price=100, threshold_pct=0.02, quantity=3)
    decision = executor.decide(spec, make_snapshot(last_price=97))

    assert decision.action == "SELL"
    assert decision.quantity == 3


def test_trend_follow_holds_within_threshold():
    executor = StrategyExecutor()
    spec = make_spec(kind="TREND_FOLLOW", reference_price=100, threshold_pct=0.02, quantity=3)
    decision = executor.decide(spec, make_snapshot(last_price=101))

    assert decision.action == "HOLD"


def test_strategy_symbol_mismatch_returns_hold():
    executor = StrategyExecutor()
    spec = make_spec(kind="FIXED_SIZE", action="BUY", quantity=5, symbols=("QQQ",))
    decision = executor.decide(spec, make_snapshot(symbol="SPY"))

    assert decision.action == "HOLD"


def test_strategy_library_save_and_load(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    spec = make_spec(strategy_id="save-test")
    library.save(spec)

    loaded = library.load("save-test")
    assert loaded.strategy_id == "save-test"
    assert loaded.kind == "HOLD_BASELINE"


def test_strategy_library_exists_and_try_load(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    assert library.exists("missing") is False
    assert library.try_load("missing") is None

    library.save(make_spec(strategy_id="present"))

    assert library.exists("present") is True
    assert library.try_load("present").strategy_id == "present"


def test_strategy_library_lists_saved_strategies(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    library.save(make_spec(strategy_id="a"))
    library.save(make_spec(strategy_id="b"))

    all_strategies = library.list()
    ids = {strategy.strategy_id for strategy in all_strategies}
    assert ids == {"a", "b"}


def test_strategy_selector_prefers_higher_score():
    selector = StrategySelector()
    a = make_spec(strategy_id="a")
    b = make_spec(strategy_id="b")
    results = [
        StrategyResult(strategy_id="a", cycles=10, submitted_orders=3, rejected_orders=0, errors=0, score=0.3, evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=3),
        StrategyResult(strategy_id="b", cycles=10, submitted_orders=5, rejected_orders=0, errors=0, score=0.7, evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=5),
    ]

    chosen = selector.select([a, b], results)
    assert chosen.strategy_id == "b"


def test_strategy_selector_returns_none_if_no_enabled():
    selector = StrategySelector()
    spec = make_spec(enabled=False)

    assert selector.select([spec]) is None


def test_strategy_selector_gives_probation_strategy_exposure_before_baseline():
    selector = StrategySelector(min_probation_cycles=3)
    baseline = make_spec(strategy_id="baseline", kind="HOLD_BASELINE", lifecycle="BASELINE")
    probation = make_spec(strategy_id="probation", kind="FIXED_SIZE", lifecycle="PROBATION", action="BUY", quantity=1)
    results = [
        StrategyResult(strategy_id="baseline", cycles=10, submitted_orders=0, rejected_orders=0, errors=0, score=1.0, evaluated_at=datetime.now(tz=timezone.utc)),
    ]

    chosen = selector.select([baseline, probation], results)

    assert chosen.strategy_id == "probation"


def test_strategy_selector_ignores_paused_and_retired_strategies():
    selector = StrategySelector()
    active = make_spec(strategy_id="active", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    paused = make_spec(strategy_id="paused", kind="FIXED_SIZE", lifecycle="PAUSED", action="BUY", quantity=1)
    retired = make_spec(strategy_id="retired", kind="FIXED_SIZE", lifecycle="RETIRED", action="BUY", quantity=1)

    chosen = selector.select([paused, retired, active])

    assert chosen.strategy_id == "active"


def test_strategy_selector_does_not_force_orders_for_mature_probation():
    selector = StrategySelector(min_probation_cycles=3)
    old = make_spec(strategy_id="old", kind="TREND_FOLLOW", lifecycle="PROBATION")
    new = make_spec(
        strategy_id="new",
        kind="FIXED_SIZE",
        lifecycle="PROBATION",
        action="BUY",
        quantity=1,
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
    )
    results = [
        # cumulative_cycles, not cycles: the budget counts cycles served across the
        # journal, and this strategy has served its full budget of 3.
        StrategyResult(strategy_id="old", cycles=3, cumulative_cycles=3, submitted_orders=0, rejected_orders=0, errors=0, score=0.9, evaluated_at=datetime.now(tz=timezone.utc)),
    ]

    chosen = selector.select([old, new], results)

    assert chosen.strategy_id == "new"


def test_strategy_selector_prefers_tradable_over_baseline_after_probation():
    selector = StrategySelector(min_probation_cycles=3)
    baseline = make_spec(strategy_id="baseline", kind="HOLD_BASELINE", lifecycle="BASELINE")
    tradable = make_spec(strategy_id="tradable", kind="TREND_FOLLOW", lifecycle="PROBATION")
    results = [
        StrategyResult(strategy_id="baseline", cycles=10, submitted_orders=0, rejected_orders=0, errors=0, score=1.0, evaluated_at=datetime.now(tz=timezone.utc)),
        StrategyResult(strategy_id="tradable", cycles=3, submitted_orders=0, rejected_orders=0, errors=0, score=0.1, evaluated_at=datetime.now(tz=timezone.utc)),
    ]

    chosen = selector.select([baseline, tradable], results)

    assert chosen.strategy_id == "tradable"


def test_lifecycle_manager_promotes_probation_after_successful_exposure():
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    strategy = make_spec(strategy_id="trial", kind="FIXED_SIZE", lifecycle="PROBATION", action="BUY", quantity=1)
    result = StrategyResult(strategy_id="trial", cycles=3, cumulative_cycles=3, cumulative_trade_attempts=3, submitted_orders=3, rejected_orders=0, errors=0, score=1.0, evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=3)

    # Phase 6: this strategy has acted and has clean operational metrics, but it has
    # no verifiable outcome yet. It is not promoted - the contract requires evidence,
    # not merely the absence of trouble. With decision evidence it is.
    from min_agent.offline_validation import OfflineValidationResult
    assert manager.review([strategy], [result]) == []

    evidence = OfflineValidationResult(
        strategy_id="trial", verdict="PASS_SCREENED", reason="screened",
        decisions=12, scored=12, good_holds=9, good_hold_ratio=0.75,
    )
    [decision] = manager.review([strategy], [result], {"trial": evidence})

    assert decision.new_lifecycle == "ACTIVE"


def test_lifecycle_manager_pauses_hold_only_trend_after_evidence_cycles():
    """Inverted regression lock.

    This test used to assert that a TREND_FOLLOW strategy which only ever
    produced HOLD cycles was PROMOTED to ACTIVE. It held the top score (1.0),
    was selected every cycle, held again, kept the score, and was selected
    again - 800 of 851 recorded cycles were HOLD. The degenerate guard missed it
    twice: once because it returned False for any kind != "FIXED_SIZE", and
    once because it required lifecycle == "PROBATION".
    """
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    strategy = make_spec(strategy_id="trend", kind="TREND_FOLLOW", lifecycle="PROBATION")
    result = StrategyResult(
        strategy_id="trend",
        cycles=3,
        cumulative_cycles=3,
        submitted_orders=0,
        rejected_orders=0,
        errors=0,
        score=1.0,
        evaluated_at=datetime.now(tz=timezone.utc),
        skipped_orders=3,
        action_counts={"HOLD": 3},
        intended_notional=0,
        trade_attempts=0,
    )

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"


def test_lifecycle_manager_pauses_degenerate_fixed_size_no_exploration():
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    strategy = make_spec(strategy_id="hold-fixed", kind="FIXED_SIZE", lifecycle="PROBATION", action="HOLD", quantity=0)
    result = StrategyResult(
        strategy_id="hold-fixed",
        cycles=3,
        cumulative_cycles=3,
        submitted_orders=0,
        rejected_orders=0,
        errors=0,
        score=1.0,
        evaluated_at=datetime.now(tz=timezone.utc),
        skipped_orders=3,
        action_counts={"HOLD": 3},
        intended_notional=0,
    )

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"
    assert "no exploration" in decision.reason


def test_probation_candidate_is_not_degenerate_before_its_budget():
    """A candidate must be given the cycles it was guaranteed before being judged.

    `StrategySelector` keeps a candidate on probation for `PROBATION_CYCLES` selected
    cycles, and the offline screen refuses to judge below ten informative decisions - so
    the probation-stage verdict has to use the same budget. It used `min_active_cycles`,
    so a candidate that had attempted no trade was paused at cycle 5 of 13 and never
    reached either. Twelve of the seventeen strategies ever paused this way had attempted
    no trade, and nothing was ever promoted.
    """
    manager = StrategyLifecycleManager(probation_cycles=13)
    strategy = make_spec(strategy_id="quiet", kind="FIXED_SIZE", lifecycle="PROBATION", action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="quiet",
        cycles=12,
        submitted_orders=0,
        rejected_orders=0,
        errors=0,
        score=0.0,
        evaluated_at=datetime.now(tz=timezone.utc),
        skipped_orders=12,
        action_counts={"HOLD": 12},
        intended_notional=0,
        trade_attempts=0,
    )

    assert manager.review([strategy], [result]) == []


def test_probation_candidate_is_degenerate_once_its_budget_is_spent():
    """The same candidate, one cycle later, is paused - the budget bounds probation too.

    Without this the fix would be an open-ended probation, which is the failure mode the
    cycle budget exists to prevent.
    """
    manager = StrategyLifecycleManager(probation_cycles=13)
    strategy = make_spec(strategy_id="quiet", kind="FIXED_SIZE", lifecycle="PROBATION", action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="quiet",
        cycles=13,
        cumulative_cycles=13,
        submitted_orders=0,
        rejected_orders=0,
        errors=0,
        score=0.0,
        evaluated_at=datetime.now(tz=timezone.utc),
        skipped_orders=13,
        action_counts={"HOLD": 13},
        intended_notional=0,
        trade_attempts=0,
    )

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"
    assert "no exploration" in decision.reason


def test_promoted_strategy_that_stops_acting_is_still_paused_at_min_active_cycles():
    """A promoted strategy has no probation budget left to spend, so it is judged sooner.

    This is the defect the guard was written for - a promoted strategy that holds forever
    must not be exempt - and it is why the probation budget cannot simply be raised
    everywhere. Recorded as D4; `tools/audit_defects.py` checks the source no longer
    exempts non-probation strategies, and this pins the behaviour it was protecting.
    """
    manager = StrategyLifecycleManager(min_active_cycles=5)
    strategy = make_spec(strategy_id="stalled", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="stalled",
        cycles=5,
        submitted_orders=0,
        rejected_orders=0,
        errors=0,
        score=1.0,
        evaluated_at=datetime.now(tz=timezone.utc),
        skipped_orders=5,
        action_counts={"HOLD": 5},
        intended_notional=0,
        trade_attempts=0,
    )

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"
    assert "no exploration" in decision.reason


def test_a_served_candidate_leaves_probation_even_with_an_empty_window():
    """The budget is cumulative service, so an empty window cannot restart it.

    This is the case that made probation endless. `cycles` is the reflection window -
    50 records shared by everything that traded in it, which tops out at 7 for any one
    strategy - and the budget is 13, so a candidate measured against the window could
    never be released no matter how long it had served. With 18 candidates in the queue
    the probation branch of `select` therefore always won, and the one ACTIVE strategy
    was selected 0 times in the 146 cycles after its promotion.
    """
    selector = StrategySelector()
    budget = selector.min_probation_cycles
    candidate = make_spec(strategy_id="candidate", kind="FIXED_SIZE", lifecycle="PROBATION")
    incumbent = make_spec(strategy_id="incumbent", kind="FIXED_SIZE", lifecycle="ACTIVE")

    served = [
        # Served its whole budget, but has traded nothing in the current window.
        StrategyResult(
            strategy_id="candidate", cycles=0, cumulative_cycles=budget,
            submitted_orders=0, rejected_orders=0, errors=0, score=0.1,
            evaluated_at=datetime.now(tz=timezone.utc),
        ),
        StrategyResult(
            strategy_id="incumbent", cycles=4, cumulative_cycles=40, submitted_orders=8,
            trade_attempts=8, rejected_orders=0, errors=0, score=0.9,
            evaluated_at=datetime.now(tz=timezone.utc),
        ),
    ]

    assert selector.select([candidate, incumbent], served).strategy_id == "incumbent"


def test_a_candidate_with_service_left_stays_in_probation_whatever_the_window_says():
    """The converse, so the first test cannot pass by ignoring the count entirely."""
    selector = StrategySelector()
    candidate = make_spec(strategy_id="candidate", kind="FIXED_SIZE", lifecycle="PROBATION")
    incumbent = make_spec(strategy_id="incumbent", kind="FIXED_SIZE", lifecycle="ACTIVE")

    results = [
        StrategyResult(
            strategy_id="candidate", cycles=selector.min_probation_cycles,
            cumulative_cycles=selector.min_probation_cycles - 1,
            submitted_orders=0, rejected_orders=0, errors=0, score=0.1,
            evaluated_at=datetime.now(tz=timezone.utc),
        ),
        StrategyResult(
            strategy_id="incumbent", cycles=4, cumulative_cycles=40, submitted_orders=8,
            trade_attempts=8, rejected_orders=0, errors=0, score=0.9,
            evaluated_at=datetime.now(tz=timezone.utc),
        ),
    ]

    assert selector.select([candidate, incumbent], results).strategy_id == "candidate"


def test_probation_rates_stay_windowed_so_a_lifetime_count_cannot_become_a_rate():
    """Cumulative service feeds the budget, never the failure rate.

    `cumulative_cycles` is new and it would be easy to reach for it everywhere. These are
    the rules that must keep the windowed number: an error rate is a statement about
    recent reliability, and dividing one error by 44 lifetime cycles would report 2%
    failure for a strategy that failed on a third of the cycles that actually ran.
    """
    manager = StrategyLifecycleManager(max_error_rate=0.25)
    strategy = make_spec(strategy_id="noisy", kind="FIXED_SIZE", lifecycle="ACTIVE",
                         action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="noisy", cycles=3, cumulative_cycles=44,
        submitted_orders=1, rejected_orders=0, errors=1, score=0.1,
        evaluated_at=datetime.now(tz=timezone.utc),
    )

    assert manager._is_degenerate_no_exploration(strategy, result) is False
    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"
    assert "error rate" in decision.reason


def test_a_strategy_result_built_without_the_new_count_behaves_exactly_as_before():
    """The default must be inert, which is what makes the other 892 tests evidence.

    A result with no `cumulative_cycles` reads as zero served, so a PROBATION candidate
    is still inside its budget and is not paused - the same answer the windowed code gave
    before the field existed.
    """
    manager = StrategyLifecycleManager(probation_cycles=13)
    strategy = make_spec(strategy_id="legacy", kind="FIXED_SIZE", lifecycle="PROBATION",
                         action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="legacy", cycles=13, submitted_orders=0, rejected_orders=0, errors=0,
        score=0.0, evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=0,
    )

    assert result.cumulative_cycles == 0
    assert manager._is_degenerate_no_exploration(strategy, result) is False


def test_a_served_candidate_is_judged_on_a_thin_window():
    """The probation verdict gate is a service question, not a freshness one.

    Left on the windowed count, a candidate that has served its whole probation budget
    but holds only a few cycles in the current 50-record window got *no verdict at all* -
    not a refusal, an absence - and waited for a coincidence. Measured on the live
    library, `trend-follow-buy-010` was exactly that case: 15 cumulative cycles, 10
    scored decisions, PASS_SCREENED, and 4 window cycles against a bar of 5.

    The window still has to be non-empty: `cycles <= 0` returns early a few lines above,
    because the rate rules divide by it and a strategy with no activity in the window has
    nothing fresh to judge on. Two window cycles is the case that was broken; zero is not.
    """
    manager = StrategyLifecycleManager(probation_cycles=13)
    strategy = make_spec(strategy_id="served", kind="FIXED_SIZE", lifecycle="PROBATION",
                         action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="served", cycles=2, cumulative_cycles=13, cumulative_trade_attempts=5,
        submitted_orders=5, rejected_orders=0, errors=0, score=0.9,
        evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=5,
    )

    [decision] = manager.review([strategy], [result], {"served": _evidence(scored=12)})

    assert decision.new_lifecycle == "ACTIVE"


def test_a_candidate_short_of_its_budget_is_not_judged_on_a_thin_window_either():
    """The converse, so the first cannot pass by ignoring the count."""
    manager = StrategyLifecycleManager(probation_cycles=13)
    strategy = make_spec(strategy_id="unserved", kind="FIXED_SIZE", lifecycle="PROBATION",
                         action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="unserved", cycles=2, cumulative_cycles=12, cumulative_trade_attempts=5,
        submitted_orders=5, rejected_orders=0, errors=0, score=0.9,
        evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=5,
    )

    assert manager.review([strategy], [result], {"unserved": _evidence(scored=12)}) == []


def test_a_candidate_that_traded_in_its_lifetime_is_not_called_degenerate():
    """The daemon paused this exact case on 2026-10-04, minutes after the fix that
    let the candidate reach a ruling at all.

    `trend-follow-buy-010` had served 15 cycles, made one BUY across its whole life,
    and passed the offline screen on 10 scored decisions. It was paused for "no
    exploration evidence" because `trade_attempts` is windowed and its single BUY fell
    outside the last 50 records. "Did this candidate ever try to trade?" is a lifetime
    question and the window answered it for five hours.
    """
    manager = StrategyLifecycleManager(probation_cycles=13)
    strategy = make_spec(strategy_id="traded-once", kind="FIXED_SIZE", lifecycle="PROBATION",
                         action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="traded-once", cycles=4, cumulative_cycles=15,
        cumulative_trade_attempts=1,          # lifetime: one BUY, outside the window
        trade_attempts=0,                     # windowed: nothing recent
        submitted_orders=1, rejected_orders=0, errors=0, score=0.225,
        evaluated_at=datetime.now(tz=timezone.utc),
    )

    assert manager._is_degenerate_no_exploration(strategy, result) is False


def test_a_candidate_that_never_traded_is_still_paused_on_its_lifetime_count():
    """So the fix is not a licence to keep anything: zero lifetime attempts still pauses."""
    manager = StrategyLifecycleManager(probation_cycles=13)
    strategy = make_spec(strategy_id="never-traded", kind="FIXED_SIZE", lifecycle="PROBATION",
                         action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="never-traded", cycles=4, cumulative_cycles=15,
        cumulative_trade_attempts=0, trade_attempts=0,
        submitted_orders=0, rejected_orders=0, errors=0, score=0.0,
        evaluated_at=datetime.now(tz=timezone.utc),
    )

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"
    assert "no exploration" in decision.reason


def test_lifecycle_manager_pauses_high_rejection_rate():
    manager = StrategyLifecycleManager(max_rejection_rate=0.5, severe_failure_rate=1.0)
    strategy = make_spec(strategy_id="noisy", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    result = StrategyResult(strategy_id="noisy", cycles=4, submitted_orders=0, rejected_orders=3, errors=0, score=0.1, evaluated_at=datetime.now(tz=timezone.utc))

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "PAUSED"


def test_lifecycle_manager_retires_broker_verified_negative_pnl():
    manager = StrategyLifecycleManager()
    strategy = make_spec(strategy_id="loser", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1)
    result = StrategyResult(
        strategy_id="loser",
        cycles=5,
        submitted_orders=2,
        rejected_orders=0,
        errors=0,
        realized_pnl=-1.0,
        pnl_evidence="broker_strategy_closed_lot_pnl_verified",
        score=0.2,
        evaluated_at=datetime.now(tz=timezone.utc),
    )

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "RETIRED"


def test_lifecycle_manager_leaves_baseline_unchanged():
    manager = StrategyLifecycleManager()
    strategy = make_spec(strategy_id="baseline", kind="HOLD_BASELINE", lifecycle="BASELINE")
    result = StrategyResult(strategy_id="baseline", cycles=10, submitted_orders=0, rejected_orders=0, errors=0, score=1.0, evaluated_at=datetime.now(tz=timezone.utc))

    assert manager.review([strategy], [result]) == []


def test_strategy_spec_rejects_code_parameters():
    from pydantic import ValidationError

    try:
        make_spec(code="print('unsafe')")
    except ValidationError:
        pass
    else:
        raise AssertionError("expected ValidationError for code parameter")


# ---------------------------------------------------------------------------
# An exit must never be blocked by a limit on opening risk.
# ---------------------------------------------------------------------------


def test_the_new_exposure_cap_does_not_constrain_a_sell():
    """max_position_value exists to bound new exposure. Applied to a SELL it meant a
    strategy whose cap sat below the share price could never exit: the cap zeroed
    the quantity and the exit was silently rewritten to HOLD. One of the four SELL
    decisions this agent ever made was rejected by exactly that logic."""
    from min_agent.models import DataSnapshot
    from min_agent.strategy_engine import StrategyExecutor

    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        market_open=True,
        last_price=750,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000,
            portfolio_value=100_000, daily_loss=0,
        ),
    )
    strategy = StrategySpec(
        strategy_id="sell-1",
        name="exit",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "SELL", "quantity": 3, "confidence": 0.8},
        max_position_value=100,   # far below one share
        rationale="test",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    decision = StrategyExecutor().decide(strategy, snapshot)

    assert decision.action == "SELL"
    assert decision.quantity == 3


def test_a_buy_is_still_capped_by_the_position_limit():
    from min_agent.models import DataSnapshot
    from min_agent.strategy_engine import StrategyExecutor

    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        market_open=True,
        last_price=750,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000,
            portfolio_value=100_000, daily_loss=0,
        ),
    )
    strategy = StrategySpec(
        strategy_id="buy-1",
        name="enter",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 10, "confidence": 0.8},
        max_position_value=1500,   # two shares at 750
        rationale="test",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    assert StrategyExecutor().decide(strategy, snapshot).quantity == 2


def test_a_buy_that_cannot_afford_one_share_still_holds():
    from min_agent.models import DataSnapshot
    from min_agent.strategy_engine import StrategyExecutor

    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        market_open=True,
        last_price=750,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000,
            portfolio_value=100_000, daily_loss=0,
        ),
    )
    strategy = StrategySpec(
        strategy_id="buy-1",
        name="enter",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=100,
        rationale="test",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    assert StrategyExecutor().decide(strategy, snapshot).action == "HOLD"


def test_broker_verified_negative_pnl_retires_without_cycles_in_the_window():
    """A strategy can open a lot in one window and have it closed in a later one,
    leaving zero cycles in the reflection window. Requiring cycles > 0 before
    evaluating PnL made the only PnL-driven transition in the system unreachable
    for exactly the strategies that had real PnL to report."""
    from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    strategy = StrategySpec(
        strategy_id="s1",
        name="loser",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=1000,
        rationale="test",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        lifecycle="ACTIVE",
    )
    result = StrategyResult(
        strategy_id="s1",
        evaluated_at=datetime.now(tz=timezone.utc),
        cycles=0,
        submitted_orders=1,
        rejected_orders=0,
        skipped_orders=0,
        errors=0,
        score=0.5,
        realized_pnl=-25.0,
        pnl_evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
    )

    decisions = StrategyLifecycleManager().review([strategy], [result])

    assert len(decisions) == 1
    assert decisions[0].new_lifecycle == "RETIRED"
    assert "negative realized PnL" in decisions[0].reason


def test_verified_positive_pnl_alone_does_not_promote():
    """PnL may retire a strategy, but it does not shorten probation. ACTIVE status
    is earned by cycles, not by one lucky trade."""
    from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    strategy = StrategySpec(
        strategy_id="s1",
        name="new",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=1000,
        rationale="test",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        lifecycle="PROBATION",
    )
    result = StrategyResult(
        strategy_id="s1",
        evaluated_at=datetime.now(tz=timezone.utc),
        cycles=1,
        submitted_orders=1,
        rejected_orders=0,
        skipped_orders=0,
        errors=0,
        score=0.9,
        realized_pnl=500.0,
        pnl_evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
    )

    assert StrategyLifecycleManager().review([strategy], [result]) == []


def test_a_strategy_supplying_a_missing_capability_is_served_before_near_duplicates():
    """Curriculum built a SELL strategy precisely because the library could not
    sell, and then a strict oldest-first probation queue deferred exercising it
    behind five near-duplicate BUY strategies - three probation cycles each, about
    105 minutes at the shipped 5-minute interval. A capability the library is
    otherwise missing has no other route to being tried.
    """
    from min_agent.strategy_engine import StrategySelector

    def spec(sid, action, kind="FIXED_SIZE", created="2026-06-15T00:00:00+00:00"):
        return StrategySpec(
            strategy_id=sid, name=sid, kind=kind, symbols=("SPY",),
            parameters={"action": action, "quantity": 1, "confidence": 0.7} if action else {},
            max_position_value=1000, enabled=True, lifecycle="PROBATION",
            created_at=datetime.fromisoformat(created), rationale="t",
        )

    # Five BUY strategies admitted long before the SELL one.
    strategies = [spec(f"buy-{i}", "BUY") for i in range(5)]
    strategies.append(spec("sell-1", "SELL", created="2026-09-28T18:15:18+00:00"))

    selected = StrategySelector().select(strategies, [])

    assert selected is not None
    assert selected.strategy_id == "sell-1", "the only route to a SELL was deferred"


def test_with_every_capability_covered_the_queue_stays_oldest_first():
    from min_agent.strategy_engine import StrategySelector

    def spec(sid, action, created):
        return StrategySpec(
            strategy_id=sid, name=sid, kind="FIXED_SIZE", symbols=("SPY",),
            parameters={"action": action, "quantity": 1, "confidence": 0.7},
            max_position_value=1000, enabled=True, lifecycle="PROBATION",
            created_at=datetime.fromisoformat(created), rationale="t",
        )

    # Both BUY and SELL have two routes each, so no strategy is the sole holder of
    # a capability and the queue is untouched.
    strategies = [
        spec("old-buy", "BUY", "2026-06-15T00:00:00+00:00"),
        spec("old-sell", "SELL", "2026-06-16T00:00:00+00:00"),
        spec("new-buy", "BUY", "2026-09-28T18:00:00+00:00"),
        spec("new-sell", "SELL", "2026-09-28T18:05:00+00:00"),
    ]

    assert StrategySelector().select(strategies, []).strategy_id == "old-buy"


def test_a_hold_baseline_supplies_no_capability():
    from min_agent.strategy_engine import StrategySelector

    baseline = StrategySpec(
        strategy_id="baseline", name="b", kind="HOLD_BASELINE", symbols=("SPY",),
        parameters={}, max_position_value=1000, enabled=True, lifecycle="PROBATION",
        created_at=datetime.fromisoformat("2026-06-10T00:00:00+00:00"), rationale="t",
    )
    buy = StrategySpec(
        strategy_id="only-buy", name="b", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.7},
        max_position_value=1000, enabled=True, lifecycle="PROBATION",
        created_at=datetime.fromisoformat("2026-09-28T18:00:00+00:00"), rationale="t",
    )

    assert StrategySelector._declared_actions(baseline) == set()
    # SELL is uncovered and the baseline cannot supply it, so the BUY is served.
    assert StrategySelector._supplies_capability(buy, {"SELL"}) is False


def test_coverage_is_evaluated_at_the_real_price_not_on_paper():
    """A TREND_FOLLOW nominally covers both sides but only emits the one its
    reference price points at. Counting the live one as an exit is what let the
    library report SELL as covered while being unable to sell - the same
    nominal-versus-executable error already fixed in the curriculum."""
    from min_agent.strategy_engine import StrategySelector

    stale = StrategySpec(
        strategy_id="trend-follow-buy-001", name="tf", kind="TREND_FOLLOW", symbols=("SPY",),
        parameters={"threshold_pct": 0.01, "confidence": 0.55, "quantity": 1,
                    "reference_price": 735.0057},
        max_position_value=5000, enabled=True, lifecycle="ACTIVE",
        created_at=datetime.fromisoformat("2026-06-18T03:21:34+00:00"), rationale="t",
    )

    buy = StrategySpec(
        strategy_id="fixed-size-buy-001", name="b", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.7},
        max_position_value=1000, enabled=True, lifecycle="ACTIVE",
        created_at=datetime.fromisoformat("2026-06-18T03:21:34+00:00"), rationale="t",
    )
    # Nominally the stale trend follower is a route to a SELL...
    assert StrategySelector._declared_actions(stale) == {"BUY", "SELL"}
    assert StrategySelector._uncovered_capabilities([stale, buy]) == {"SELL"}
    # ...but at the real price it only ever buys, so that route does not exist in
    # this regime and SELL stays uncovered for a different, executable reason.
    assert StrategySelector._declared_actions(stale, 767.25) == {"BUY"}
    assert StrategySelector._uncovered_capabilities([stale, buy], 767.25) == {"SELL"}
    # Below its reference it really does sell, and the difference shows up in what
    # it declares - which is what the selector acts on.
    assert StrategySelector._declared_actions(stale, 700.0) == {"SELL"}


def test_the_sell_strategy_is_served_when_it_is_the_only_working_exit():
    from min_agent.strategy_engine import StrategySelector

    stale = StrategySpec(
        strategy_id="trend-follow-buy-001", name="tf", kind="TREND_FOLLOW", symbols=("SPY",),
        parameters={"threshold_pct": 0.01, "confidence": 0.55, "quantity": 1,
                    "reference_price": 735.0057},
        max_position_value=5000, enabled=True, lifecycle="ACTIVE",
        created_at=datetime.fromisoformat("2026-06-18T03:21:34+00:00"), rationale="t",
    )
    exit_spec = StrategySpec(
        strategy_id="trend-follow-sell-002", name="exit", kind="TREND_FOLLOW", symbols=("SPY",),
        parameters={"threshold_pct": 0.01, "confidence": 0.6, "quantity": 1,
                    "reference_price": 780.0},
        max_position_value=5000, enabled=True, lifecycle="PROBATION",
        created_at=datetime.fromisoformat("2026-09-28T18:15:18+00:00"), rationale="t",
    )

    # At 767 the stale trend follower buys, the new one sells, and SELL has exactly
    # one working route - so it is the strategy that must be exercised.
    assert StrategySelector._supplies_capability(exit_spec, {"SELL"}, 767.25) is True
    assert StrategySelector._supplies_capability(stale, {"SELL"}, 767.25) is False


def test_verified_positive_pnl_drives_the_promotion_and_is_recorded_as_the_reason():
    """PnL has to be able to promote as well as retire, or it is only a veto and the
    lifecycle is not evidence-driven in the direction that matters. Once probation
    is complete, broker-verified realized PnL is what decides the outcome, and the
    reason recorded in the journal says so with the figure."""
    from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    strategy = StrategySpec(
        strategy_id="winner", name="winner", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=1000, rationale="t", lifecycle="PROBATION",
        created_at=datetime.now(tz=timezone.utc),
    )
    result = StrategyResult(
        strategy_id="winner",
        evaluated_at=datetime.now(tz=timezone.utc),
        cycles=5,
        cumulative_cycles=PROBATION_CYCLES,   # probation bar met at the default budget
        cumulative_trade_attempts=5,
        submitted_orders=5,
        rejected_orders=0,
        skipped_orders=0,
        errors=0,
        score=0.9,
        trade_attempts=5,
        strategy_fault_rejections=0,
        realized_pnl=362.66,
        pnl_evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
    )

    decisions = StrategyLifecycleManager().review([strategy], [result])

    assert len(decisions) == 1
    assert decisions[0].new_lifecycle == "ACTIVE"
    assert "362.66" in decisions[0].reason
    assert "broker-verified" in decisions[0].reason


def test_pnl_does_not_shorten_probation():
    """A single lucky trade must not promote anything. The cycle bar is still the
    gate; PnL only decides the outcome once probation is complete."""
    from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    strategy = StrategySpec(
        strategy_id="lucky", name="lucky", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=1000, rationale="t", lifecycle="PROBATION",
        created_at=datetime.now(tz=timezone.utc),
    )
    result = StrategyResult(
        strategy_id="lucky",
        evaluated_at=datetime.now(tz=timezone.utc),
        cycles=1,                       # one cycle short of the bar
        submitted_orders=1,
        rejected_orders=0,
        skipped_orders=0,
        errors=0,
        score=0.9,
        trade_attempts=1,
        strategy_fault_rejections=0,
        realized_pnl=5000.0,
        pnl_evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
    )

    assert StrategyLifecycleManager().review([strategy], [result]) == []


def test_no_closed_lot_needs_decision_evidence_not_operational_metrics():
    """A strategy with nothing to close has no PnL to be judged on, so promotion
    needs the *other* verifiable evidence: the counterfactual verdicts for the
    decisions it actually produced.

    This test previously asserted the opposite. It claimed that with no closed lot
    promotion "falls back to the operational metrics", which promoted a strategy for
    having not errored - the exact promotion-without-evidence the objective forbids
    in Phase 6. The concern that motivated it still holds and is asserted below: with
    evidence present the strategy is promoted, so probation is a gate and not a freeze.
    """
    from min_agent.evaluator import PNL_EVIDENCE_MISSING
    from min_agent.models import StrategyResult
    from min_agent.offline_validation import OfflineValidationResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    strategy = StrategySpec(
        strategy_id="no-lots", name="no-lots", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.8},
        max_position_value=1000, rationale="t", lifecycle="PROBATION",
        created_at=datetime.now(tz=timezone.utc),
    )
    result = StrategyResult(
        strategy_id="no-lots",
        evaluated_at=datetime.now(tz=timezone.utc),
        cycles=5,
        cumulative_cycles=PROBATION_CYCLES,
        cumulative_trade_attempts=5,
        submitted_orders=5,
        rejected_orders=0,
        skipped_orders=0,
        errors=0,
        score=0.9,
        trade_attempts=5,
        strategy_fault_rejections=0,
        pnl_evidence=PNL_EVIDENCE_MISSING,
    )
    manager = StrategyLifecycleManager()

    # Five submitted orders and zero errors is an absence of trouble, not evidence of
    # value, so it does not promote.
    assert manager.review([strategy], [result]) == []

    # With decision-quality evidence the same strategy is promoted.
    evidence = OfflineValidationResult(
        strategy_id="no-lots", verdict="PASS_SCREENED",
        reason="screened through", decisions=14, scored=14, good_holds=10,
        good_hold_ratio=10 / 14,
    )
    decisions = manager.review([strategy], [result], {"no-lots": evidence})

    assert len(decisions) == 1
    assert decisions[0].new_lifecycle == "ACTIVE"
    assert "submitted order" in decisions[0].reason


def _dup(strategy_id, *, kind="FIXED_SIZE", action="BUY", quantity=1,
         confidence=0.7, max_position=5000.0, lifecycle="PROBATION", day=1):
    from min_agent.models import StrategySpec

    return StrategySpec(
        strategy_id=strategy_id, name=strategy_id, kind=kind, symbols=("SPY",),
        parameters={"action": action, "quantity": quantity, "confidence": confidence},
        max_position_value=max_position, enabled=True, lifecycle=lifecycle,
        created_at=datetime(2026, 6, day, tzinfo=timezone.utc), rationale="t",
    )


def test_identical_strategies_are_retired_and_the_evidenced_one_is_kept():
    """Nine were FIXED_SIZE/SPY/BUY/qty=1/5000 differing only in confidence 0.6-0.95.
    All nine emit a byte-identical order, so the library had 13 selectable
    strategies of which 9 were the same strategy."""
    from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    winner = _dup("winner", confidence=0.6)
    # More cycles but no order ever placed, and no PnL.
    busy = _dup("busy", confidence=0.95, day=2)
    other = _dup("other", confidence=0.75, day=3)

    def result_for(sid, *, cycles, submitted, pnl, evidence):
        return StrategyResult(
            strategy_id=sid, evaluated_at=datetime.now(tz=timezone.utc), cycles=cycles,
            submitted_orders=submitted, rejected_orders=0, skipped_orders=0, errors=0,
            score=0.5, trade_attempts=submitted,
            realized_pnl=pnl, pnl_evidence=evidence,
        )

    results = [
        result_for("winner", cycles=0, submitted=23, pnl=120.37,
                   evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED),
        result_for("busy", cycles=9, submitted=0, pnl=None,
                   evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED),
        result_for("other", cycles=0, submitted=0, pnl=None,
                   evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED),
    ]

    decisions = StrategyLifecycleManager().review([winner, busy, other], results)

    retired = {d.strategy.strategy_id: d for d in decisions
               if "behavioural duplicate" in d.reason}
    assert set(retired) == {"busy", "other"}
    assert "winner" not in retired, "the evidenced copy must survive"
    assert "fixed" not in str(retired["busy"].reason) and "winner" in retired["busy"].reason


def test_a_single_strategy_is_never_treated_as_its_own_duplicate():
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    alone = _dup("alone")
    result = StrategyResult(
        strategy_id="alone", evaluated_at=datetime.now(tz=timezone.utc), cycles=9,
        submitted_orders=9, rejected_orders=0, skipped_orders=0, errors=0,
        score=0.5, trade_attempts=9,
    )

    decisions = StrategyLifecycleManager().review([alone], [result])

    assert not [d for d in decisions if "behavioural duplicate" in d.reason]


def test_differing_quantity_is_a_genuinely_different_strategy():
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    one = _dup("one", quantity=1)
    two = _dup("two", quantity=2)
    results = [
        StrategyResult(strategy_id=sid, evaluated_at=datetime.now(tz=timezone.utc),
                       cycles=5, submitted_orders=1, rejected_orders=0, skipped_orders=0,
                       errors=0, score=0.5, trade_attempts=1)
        for sid in ("one", "two")
    ]

    decisions = StrategyLifecycleManager().review([one, two], results)

    assert not [d for d in decisions if "behavioural duplicate" in d.reason]


def test_buy_and_sell_are_not_duplicates_of_each_other():
    from min_agent.models import StrategyResult
    from min_agent.strategy_engine import StrategyLifecycleManager

    buy = _dup("buy", action="BUY")
    sell = _dup("sell", action="SELL")
    results = [
        StrategyResult(strategy_id=sid, evaluated_at=datetime.now(tz=timezone.utc),
                       cycles=5, submitted_orders=1, rejected_orders=0, skipped_orders=0,
                       errors=0, score=0.5, trade_attempts=1)
        for sid in ("buy", "sell")
    ]

    decisions = StrategyLifecycleManager().review([buy, sell], results)

    assert not [d for d in decisions if "behavioural duplicate" in d.reason]


def test_admission_rejects_a_strategy_that_would_behave_identically_to_one_in_the_library():
    import pathlib
    import tempfile

    from min_agent.guardian import Guardian
    from min_agent.strategy_admission import StrategyAdmission
    from min_agent.strategy_engine import StrategyLibrary

    with tempfile.TemporaryDirectory() as tmp:
        library = StrategyLibrary(pathlib.Path(tmp) / "strategies")
        # The progression gate requires a baseline before any trading skill, so
        # the duplicate rule is only reachable past it.
        library.save(StrategySpec(
            strategy_id="baseline", name="baseline", kind="HOLD_BASELINE",
            symbols=("SPY",), parameters={}, max_position_value=1.0, enabled=True,
            lifecycle="BASELINE", created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            rationale="t",
        ))
        existing = _dup("existing", confidence=0.6, day=2)
        library.save(existing)
        admission = StrategyAdmission(
            guardian=Guardian(allowlist={"SPY"}, max_position_value=5000, max_daily_loss=500),
            strategy_library=library,
            market_prices={"SPY": 767.0},
        )
        # Same behaviour, different id and a different confidence - which admission
        # previously accepted, because it only rejected id and TREND_FOLLOW equality.
        twin = _dup("twin", confidence=0.95, day=2)

        result = admission.admit(twin)

        assert result.accepted is False
        assert "behaviourally identical" in result.reason
        assert "existing" in result.reason


def _trend(strategy_id, reference, threshold=0.02, quantity=1, max_position_value=1.0):
    from min_agent.models import StrategySpec
    return StrategySpec(
        strategy_id=strategy_id, name=strategy_id, kind="TREND_FOLLOW",
        symbols=("SPY",),
        parameters={
            "reference_price": reference, "threshold_pct": threshold,
            "quantity": quantity, "confidence": 0.55,
        },
        max_position_value=max_position_value,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        rationale="r",
    )


def test_two_identical_trend_follow_strategies_have_the_same_signature():
    """TREND_FOLLOW used to return None, so no TREND_FOLLOW was ever compared to any
    other and the zoo check was blind to all twelve of them. Three were exact
    duplicates. The side is derived from the price, but the *rule* is fully
    determined by the declared parameters, so two strategies with the same
    reference, threshold and quantity act identically on every snapshot."""
    from min_agent.strategy_engine import behavioural_signature

    a = behavioural_signature(_trend("a", 100.0))
    b = behavioural_signature(_trend("b", 100.0))
    assert a is not None, "a comparable TREND_FOLLOW must produce a signature"
    assert a == b


def test_trend_follow_strategies_differing_in_rule_differ_in_signature():
    from min_agent.strategy_engine import behavioural_signature

    base = behavioural_signature(_trend("base", 100.0, threshold=0.02))
    assert base != behavioural_signature(_trend("x", 150.0)), "reference differs"
    assert base != behavioural_signature(_trend("y", 100.0, threshold=0.05)), "threshold"
    assert base != behavioural_signature(_trend("z", 100.0, quantity=5)), "quantity"


def test_confidence_still_does_not_split_a_trend_follow():
    """confidence is an admission gate, not a behaviour. Including it is what let
    one strategy split into several."""
    from min_agent.models import StrategySpec
    from min_agent.strategy_engine import behavioural_signature

    loud = _trend("loud", 100.0)
    quiet = loud.model_copy(update={
        "strategy_id": "quiet",
        "parameters": {**loud.parameters, "confidence": 0.95},
    })
    assert behavioural_signature(loud) == behavioural_signature(quiet)


def test_a_trend_follow_with_non_numeric_parameters_has_no_signature():
    """Compared rather than crashed: one malformed spec must not take down a batch
    admission run."""
    from min_agent.strategy_engine import behavioural_signature

    broken = _trend("broken", 100.0)
    broken.parameters.pop("threshold_pct")
    assert behavioural_signature(broken) is None

# --------------------------------------------------------------------------
# Phase 6: promotion is evidence-gated
# --------------------------------------------------------------------------

def _probation_spec(strategy_id="p1", action="BUY"):
    return make_spec(
        strategy_id=strategy_id, kind="FIXED_SIZE", lifecycle="PROBATION",
        action=action, quantity=1,
    )


def _clean_result(strategy_id="p1", **over):
    from min_agent.models import StrategyResult
    base = dict(
        strategy_id=strategy_id, cycles=5, cumulative_cycles=5, cumulative_trade_attempts=5,
        submitted_orders=5, rejected_orders=0,
        skipped_orders=0, errors=0, score=1.0, trade_attempts=5,
        strategy_fault_rejections=0,
        evaluated_at=datetime.now(tz=timezone.utc),
    )
    base.update(over)
    return StrategyResult(**base)


def _evidence(verdict="PASS_SCREENED", scored=12, strategy_id="p1"):
    from min_agent.offline_validation import OfflineValidationResult
    return OfflineValidationResult(
        strategy_id=strategy_id, verdict=verdict, reason="test",
        decisions=scored, scored=scored, good_holds=max(0, scored - 3),
        good_hold_ratio=(max(0, scored - 3) / scored) if scored else None,
    )


def test_a_strategy_that_never_traded_is_not_promoted_however_clean_it_looked():
    """Five cycles, zero errors, zero rejections - and it never did anything. A
    strategy with no orders has produced no evidence of value, and the absence of
    trouble is not evidence."""
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    result = _clean_result(submitted_orders=0, trade_attempts=0)

    decisions = manager.review([_probation_spec()], [result], {"p1": _evidence()})

    # Asserting "not ACTIVE" rather than "no decision" on purpose: the degenerate
    # exploration guard may legitimately fire here, and a test that conflates the two
    # would pass for the wrong reason and hide which guard did the work.
    assert all(d.new_lifecycle != "ACTIVE" for d in decisions), decisions


def test_absent_evidence_blocks_promotion_rather_than_defaulting_to_it():
    """The direction that matters. No verdict yet reads as 'not yet', never as
    'assumed fine' - which is precisely what the version this replaced did."""
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    for kwargs in ({}, {"p1": _evidence(verdict="")}):
        decisions = manager.review([_probation_spec()], [_clean_result()], kwargs or None)
        assert all(d.new_lifecycle != "ACTIVE" for d in decisions), (kwargs, decisions)


def test_an_inconclusive_verdict_does_not_promote():
    """INCONCLUSIVE means the screen could not tell. Promoting on it would read
    ignorance as a pass, which is the error this whole gate exists to prevent."""
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    verdict = "INCONCLUSIVE_INSUFFICIENT_EVIDENCE"
    decisions = manager.review(
        [_probation_spec()], [_clean_result()], {"p1": _evidence(verdict=verdict)}
    )
    assert all(d.new_lifecycle != "ACTIVE" for d in decisions), decisions


def test_a_rejected_verdict_does_not_promote():
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    decisions = manager.review(
        [_probation_spec()], [_clean_result()],
        {"p1": _evidence(verdict="REJECT_POOR_DECISIONS")},
    )
    assert all(d.new_lifecycle != "ACTIVE" for d in decisions), decisions


def test_too_few_scored_decisions_does_not_promote():
    """A handful of decisions is not enough to judge, even when none of them was
    wrong."""
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    decisions = manager.review(
        [_probation_spec()], [_clean_result()], {"p1": _evidence(scored=3)}
    )
    assert all(d.new_lifecycle != "ACTIVE" for d in decisions), decisions


def test_a_clean_strategy_with_evidence_is_promoted():
    """The gate must be satisfiable. A gate that nothing can pass is a freeze, and
    would silently stop the lifecycle from ever advancing."""
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    [decision] = manager.review(
        [_probation_spec()], [_clean_result()], {"p1": _evidence()}
    )

    assert decision.new_lifecycle == "ACTIVE"
    assert "submitted order" in decision.reason


def test_the_gate_states_what_is_missing():
    """A gate that can only say no leaves the journal unable to explain why a
    strategy is stuck."""
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    reason = manager._promotion_evidence_gate(None)
    assert reason and "no offline validation evidence" in reason

    reason = manager._promotion_evidence_gate(
        _evidence(verdict="INCONCLUSIVE_INSUFFICIENT_EVIDENCE")
    )
    assert reason and "not a pass" in reason


def test_broker_verified_pnl_still_promotes_without_offline_evidence():
    """The evidence gate supplements PnL, it does not replace it. A strategy with a
    closed lot and verified PnL has the strongest evidence available and must not be
    held back by a screen that has nothing to say."""
    from min_agent.evaluator import PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    from min_agent.models import StrategyResult

    result = StrategyResult(
        strategy_id="p1", cycles=5, cumulative_cycles=5, cumulative_trade_attempts=5, submitted_orders=5, rejected_orders=0,
        skipped_orders=0, errors=0, score=1.0, trade_attempts=5,
        strategy_fault_rejections=0, realized_pnl=120.0,
        pnl_evidence=PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
        evaluated_at=datetime.now(tz=timezone.utc),
    )
    manager = StrategyLifecycleManager(min_active_cycles=3, probation_cycles=3)
    [decision] = manager.review([_probation_spec()], [result])

    assert decision.new_lifecycle == "ACTIVE"
    assert "broker-verified realized PnL" in decision.reason


# --- the probation queue is not served blind to whether a strategy can act ----
#
# The queue is oldest-first, so it is the one place in `select` that ignores whether a
# strategy could trade on this snapshot; argmax over scores naturally avoids one that
# has never traded. Replaying the recorded price path over the live library: the old
# queue spent 39 cycles, 30 of them (76%) on strategies that emitted nothing, and
# released none. Serving only what can act spends 15, wastes 0, and releases 313 of
# 325 cycles to the strategies that do trade.
#
# Raising `min_probation_cycles` was the obvious alternative and was measured: at 13
# the candidates reaching the ten-scored gate fell from 3 to 2, and the informative
# decisions produced by probation candidates fell from 62.8 to 35.8.


def test_probation_queue_skips_a_candidate_that_cannot_trade_this_snapshot():
    selector = StrategySelector(min_probation_cycles=3)
    # band [98.00, 102.00] around a reference of 100.0 - 767 is far above it, so this
    # one can act. The other is anchored on spot and can only HOLD.
    can_act = make_spec(
        strategy_id="can-act",
        kind="TREND_FOLLOW",
        lifecycle="PROBATION",
        reference_price=100,
        threshold_pct=0.02,
    )
    older_but_cannot = make_spec(
        strategy_id="older-cannot",
        kind="TREND_FOLLOW",
        lifecycle="PROBATION",
        reference_price=767,
        threshold_pct=0.02,
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )

    chosen = selector.select([older_but_cannot, can_act], None, 767.0)

    assert chosen.strategy_id == "can-act"


def test_an_untradeable_probation_queue_falls_through_instead_of_freezing():
    """The dangerous direction: skipping must not become stopping.

    If every probation candidate would HOLD, the queue is skipped and the cycle goes to
    the non-probation path. Returning None here would be an agent that has stopped
    trading because its candidates are dormant - a freeze wearing a fix's clothes.
    """
    selector = StrategySelector(min_probation_cycles=3)
    dormant = make_spec(
        strategy_id="dormant",
        kind="TREND_FOLLOW",
        lifecycle="PROBATION",
        reference_price=767,
        threshold_pct=0.02,
    )
    active = make_spec(
        strategy_id="active", kind="FIXED_SIZE", lifecycle="ACTIVE", action="BUY", quantity=1
    )

    chosen = selector.select([dormant, active], None, 767.0)

    assert chosen is not None, "a dormant probation queue must not stop the agent trading"
    assert chosen.strategy_id == "active"


def test_the_queue_decision_follows_the_price_rather_than_purging():
    """Reversibility, which is why this is a selector change and not a retirement.

    A strategy anchored on spot is dormant, not wrong. When the market moves out of its
    band it becomes selectable again, and nothing about its lifecycle was touched.
    """
    selector = StrategySelector(min_probation_cycles=3)
    anchored = make_spec(
        strategy_id="anchored",
        kind="TREND_FOLLOW",
        lifecycle="PROBATION",
        reference_price=767,
        threshold_pct=0.02,
    )
    other = make_spec(
        strategy_id="other",
        kind="TREND_FOLLOW",
        lifecycle="PROBATION",
        reference_price=100,
        threshold_pct=0.02,
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )

    # 767 is inside [752.34, 781.66], so `anchored` holds and `other` buys above 102.
    assert selector.select([anchored, other], None, 767.0).strategy_id == "other"
    # 700 is below the band, so `anchored` now emits SELL and the queue reverts to
    # oldest-first, which is `other` again only because it is older.
    assert selector.select([anchored, other], None, 700.0).strategy_id == "other"
    # Take `other` out of the picture and `anchored` is served once it can act.
    assert selector.select([anchored], None, 700.0).strategy_id == "anchored"
    assert anchored.lifecycle == "PROBATION", "selection must not change a lifecycle"


def test_a_probation_strategy_with_no_price_is_still_served():
    """Without a price there is nothing to judge executability by, so the queue runs.

    Skipping on a missing price would strand every candidate the moment the daemon
    started, which is the opposite of the intent.
    """
    selector = StrategySelector(min_probation_cycles=3)
    probation = make_spec(
        strategy_id="probation", kind="FIXED_SIZE", lifecycle="PROBATION", action="BUY", quantity=1
    )

    assert selector.select([probation], None, None).strategy_id == "probation"


def test_the_default_probation_budget_covers_the_screen_gate():
    """The budget is set to what the screen needs, not to a round number.

    `offline_validation.DEFAULT_MIN_SCORED_DECISIONS` is ten informative decisions, and
    the offline screen is the only promotion route that does not require broker-verified
    PnL. A FIXED_SIZE produces informative decisions at an observed 0.75-0.91 per selected
    cycle, so eleven to thirteen selected cycles. The budget was 3, which bought about
    two informative decisions: every candidate was judged before it could be measured, and
    the screen returned INCONCLUSIVE for all of them. No trading strategy has ever passed
    it in this project's history.
    """
    from min_agent import offline_validation as ov

    assert StrategySelector().min_probation_cycles >= ov.DEFAULT_MIN_SCORED_DECISIONS


def test_a_candidate_leaves_probation_only_once_it_could_be_screened():
    """Three cycles is not "served"; it is "not yet measurable".

    Pins the behaviour the budget exists for, without coupling the test to the exact
    constant: a candidate short of the default budget stays in the queue, and one that has
    reached it is released.
    """
    selector = StrategySelector()
    budget = selector.min_probation_cycles
    probation = make_spec(strategy_id="candidate", kind="FIXED_SIZE", lifecycle="PROBATION")
    incumbent = make_spec(strategy_id="incumbent", kind="FIXED_SIZE", lifecycle="ACTIVE")

    def with_cycles(n):
        # `cumulative_cycles` is the quantity the budget is measured against: cycles
        # served across the whole journal. `cycles` is the reflection window, which
        # tops out at 7 for any one strategy and so can never express a budget of 13.
        return [
            StrategyResult(
                strategy_id="candidate", cycles=3, cumulative_cycles=n,
                submitted_orders=0, rejected_orders=0,
                errors=0, score=0.5, evaluated_at=datetime.now(tz=timezone.utc),
            ),
            # The incumbent outscores the candidate, so once the candidate leaves
            # probation the argmax hands the cycle over rather than returning it. Without
            # this the test would pass for the wrong reason - the candidate has the higher
            # score and would win either way.
            #
            # `trade_attempts` matters too: the selector's exploration floor fires when
            # nothing in the library has traded, and its probe prefers the smallest order
            # rather than the best score. With the floor live this test would be measuring
            # the probe, not probation.
            StrategyResult(
                strategy_id="incumbent", cycles=50, submitted_orders=10, trade_attempts=10,
                rejected_orders=0, errors=0, score=0.9,
                evaluated_at=datetime.now(tz=timezone.utc),
            ),
        ]

    assert selector.select([probation, incumbent], with_cycles(budget - 1)).strategy_id == "candidate"
    assert selector.select([probation, incumbent], with_cycles(budget)).strategy_id == "incumbent"


def test_a_strategy_carries_the_reason_for_its_lifecycle_on_the_strategy_itself():
    """The reason used to live only in the journal, so a reload could not read it.

    `StrategySpec` carried `lifecycle: PAUSED` and nothing else, which made a strategy
    rejected on evidence indistinguishable from one that had merely never been
    testable. It is why the doctor spent 17 passes reconciling files it described as
    carrying "a gated life" it could not explain, and why `experiment_registry` kept a
    `lifecycle_reason` the authoritative file did not have - the reason survived in the
    derived view and not in the single source of truth.

    It also blocks any re-adjudication: a rule cannot ask whether a reason still holds
    when the reason was not kept.
    """
    spec = make_spec(strategy_id="s", lifecycle="PAUSED")

    assert "lifecycle_reason" in type(spec).model_fields
    assert spec.lifecycle_reason == "", "a strategy written before the field existed must still load"


def test_the_reason_is_written_when_the_lifecycle_is_written():
    """A field nothing populates is documentation, not a fix."""
    import inspect

    from min_agent import daemon, strategy_admission

    lifecycle_writes = inspect.getsource(daemon.AgentDaemon)
    assert 'model_copy(update={"lifecycle": decision.new_lifecycle' not in lifecycle_writes
    assert '"lifecycle_reason": decision.reason' in lifecycle_writes
    assert '"lifecycle_reason": review.reason' in inspect.getsource(strategy_admission)


# --------------------------------------------------------------------------
# selection pressure lives in the rules, not in the daemon's control flow
# --------------------------------------------------------------------------

def _screen_evidence(verdict, *, scored=0, correct=0):
    from min_agent.offline_validation import OfflineValidationResult

    return OfflineValidationResult(
        strategy_id="s", verdict=verdict, reason="test", decisions=scored, scored=scored,
        good_holds=correct, missed_alpha=0, false_trades=scored - correct, good_trades=0,
    )


def _probation_result(strategy_id="s"):
    return StrategyResult(
        strategy_id=strategy_id, cycles=60, submitted_orders=45, rejected_orders=0,
        errors=0, score=0.5, trade_attempts=45,
        evaluated_at=datetime.now(tz=timezone.utc),
    )


def test_a_screen_verdict_of_reject_retires_the_strategy_with_its_evidence():
    """The defect this fixes, measured.

    Over 847 real bars: 580 scored decisions, 304 of them losing trades,
    `correct_outcome_ratio` 0.476, verdict REJECT_POOR_DECISIONS - and
    `StrategyLifecycleManager.review()` returned no ruling at all. The veto lived in
    `AgentDaemon._apply_offline_rejection`, so any caller of the rule set got the promotion
    gate but not the rejection, and the journal could hold a REJECT for a strategy still
    marked PROBATION with nothing saying why.
    """
    spec = make_spec(strategy_id="s", kind="FIXED_SIZE", lifecycle="PROBATION")

    decisions = StrategyLifecycleManager().review(
        [spec], [_probation_result()],
        {"s": _screen_evidence("REJECT_POOR_DECISIONS", scored=580, correct=276)},
    )

    assert len(decisions) == 1
    assert decisions[0].new_lifecycle == "RETIRED"
    reason = decisions[0].reason
    assert "580 scored" in reason
    assert "0.476" in reason, "the ruling must carry the numbers, not an assertion"


def test_the_rejection_veto_does_not_fire_on_silence():
    """INCONCLUSIVE means "cannot tell yet". Retiring on it would empty the library.

    This is the same error the admission gate made once already: a first-cycle candidate
    told INCONCLUSIVE came back paused, because silence was being read as failure. A rule
    that retires on silence is not strict, it is broken.
    """
    spec = make_spec(strategy_id="s", kind="FIXED_SIZE", lifecycle="PROBATION")
    manager = StrategyLifecycleManager()

    inconclusive = manager.review([spec], [_probation_result()], {"s": _screen_evidence("INCONCLUSIVE")})
    no_evidence = manager.review([spec], [_probation_result()], {})

    assert not [d for d in inconclusive if d.new_lifecycle == "RETIRED"]
    assert not [d for d in no_evidence if d.new_lifecycle == "RETIRED"]


def test_a_passing_verdict_is_not_a_ruling_against_the_strategy():
    """The veto must not fire on a pass either - promotion handles that."""
    spec = make_spec(strategy_id="s", kind="FIXED_SIZE", lifecycle="PROBATION")

    decisions = StrategyLifecycleManager().review(
        [spec], [_probation_result()],
        {"s": _screen_evidence("PASS_SCREENED", scored=40, correct=28)},
    )

    assert not [d for d in decisions if d.new_lifecycle == "RETIRED"]


def test_an_already_retired_strategy_is_not_re_judged():
    """A terminal state must stay terminal however the evidence reads."""
    spec = make_spec(strategy_id="s", kind="FIXED_SIZE", lifecycle="RETIRED")

    decisions = StrategyLifecycleManager().review(
        [spec], [_probation_result()],
        {"s": _screen_evidence("REJECT_POOR_DECISIONS", scored=580, correct=276)},
    )

    assert decisions == []


def _service(strategy_id, cumulative, *, cycles=0, trade_attempts=0, score=0.5):
    return StrategyResult(
        strategy_id=strategy_id, cycles=cycles, cumulative_cycles=cumulative,
        submitted_orders=trade_attempts, rejected_orders=0, errors=0, score=score,
        evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=trade_attempts,
    )


def test_a_promoted_strategy_is_served_on_its_cadence_turn():
    """A strategy that is promoted and then never trades has not been promoted.

    Probation has absolute priority and admission holds the backlog at the cap, so the
    queue is effectively never empty - 5.6e-05 of cycles on the live library - and the one
    ACTIVE strategy was selected 0 times in the 146 cycles after its promotion.
    """
    selector = StrategySelector(incumbent_share=INCUMBENT_SHARE)
    candidate = make_spec(strategy_id="candidate", kind="FIXED_SIZE", lifecycle="PROBATION",
                          action="BUY", quantity=1)
    incumbent = make_spec(strategy_id="incumbent", kind="FIXED_SIZE", lifecycle="ACTIVE",
                          action="BUY", quantity=1)

    # total cumulative service 9, share 5 -> not a cadence turn
    between = [_service("candidate", 8), _service("incumbent", 1, score=0.9)]
    on_turn = [_service("candidate", 9), _service("incumbent", 1, score=0.9)]

    assert selector.select([candidate, incumbent], between, last_price=100.0).strategy_id == "candidate"
    assert selector.select([candidate, incumbent], on_turn, last_price=100.0).strategy_id == "incumbent"


def test_the_incumbent_cadence_advances_on_cumulative_service_not_the_window():
    """The distinction this session has been about, and the one that broke probation twice.

    Both results carry zero windowed cycles - the incumbent has not traded recently - and
    differ only in cumulative service. If the cadence were read from the windowed count it
    would sit at zero and never advance, which is the bug that made probation unfinishable.
    """
    selector = StrategySelector(incumbent_share=INCUMBENT_SHARE)
    candidate = make_spec(strategy_id="candidate", kind="FIXED_SIZE", lifecycle="PROBATION",
                          action="BUY", quantity=1)
    incumbent = make_spec(strategy_id="incumbent", kind="FIXED_SIZE", lifecycle="ACTIVE",
                          action="BUY", quantity=1)
    results = [_service("candidate", 4, cycles=0), _service("incumbent", 1, cycles=0, score=0.9)]

    assert selector.select([candidate, incumbent], results, last_price=100.0).strategy_id == "incumbent"


def test_with_no_promoted_strategy_selection_is_unchanged():
    """The reserved share must cost nothing when there is nothing to reserve for."""
    selector = StrategySelector(incumbent_share=INCUMBENT_SHARE)
    only = make_spec(strategy_id="only", kind="FIXED_SIZE", lifecycle="PROBATION",
                     action="BUY", quantity=1)

    for cumulative in (0, 5, 10, 25):
        results = [_service("only", cumulative, cycles=1)]
        assert selector.select([only], results, last_price=100.0).strategy_id == "only"


def test_an_incumbent_that_cannot_act_leaves_every_turn_to_probation():
    """A reserved turn is not a wasted turn: the share is for cycles that can do something.

    A FIXED_SIZE always declares, so this uses a TREND_FOLLOW anchored so its band contains
    the price - it is ACTIVE, on a cadence turn, and cannot fire.
    """
    selector = StrategySelector(incumbent_share=INCUMBENT_SHARE)
    candidate = make_spec(strategy_id="candidate", kind="FIXED_SIZE", lifecycle="PROBATION",
                          action="BUY", quantity=1)
    dormant = make_spec(strategy_id="incumbent", kind="TREND_FOLLOW", lifecycle="ACTIVE",
                        reference_price=100.0, threshold_pct=0.05)
    results = [_service("candidate", 5), _service("incumbent", 0, score=0.9)]

    assert selector.select([candidate, dormant], results, last_price=100.0).strategy_id == "candidate"
