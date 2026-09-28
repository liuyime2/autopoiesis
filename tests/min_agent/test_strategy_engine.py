from datetime import datetime, timezone

from min_agent.models import AccountSnapshot, DataSnapshot, StrategyResult, StrategySpec
from min_agent.strategy_engine import StrategyExecutor, StrategyLibrary, StrategyLifecycleManager, StrategySelector


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
    selector = StrategySelector(min_probation_cycles=3, min_probation_submitted_orders=1)
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
    selector = StrategySelector(min_probation_cycles=3, min_probation_submitted_orders=1)
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
        StrategyResult(strategy_id="old", cycles=3, submitted_orders=0, rejected_orders=0, errors=0, score=0.9, evaluated_at=datetime.now(tz=timezone.utc)),
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
    manager = StrategyLifecycleManager(min_active_cycles=3, min_active_submitted_orders=1)
    strategy = make_spec(strategy_id="trial", kind="FIXED_SIZE", lifecycle="PROBATION", action="BUY", quantity=1)
    result = StrategyResult(strategy_id="trial", cycles=3, submitted_orders=3, rejected_orders=0, errors=0, score=1.0, evaluated_at=datetime.now(tz=timezone.utc), trade_attempts=3)

    [decision] = manager.review([strategy], [result])

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
    manager = StrategyLifecycleManager(min_active_cycles=3)
    strategy = make_spec(strategy_id="trend", kind="TREND_FOLLOW", lifecycle="PROBATION")
    result = StrategyResult(
        strategy_id="trend",
        cycles=3,
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
    manager = StrategyLifecycleManager(min_active_cycles=3)
    strategy = make_spec(strategy_id="hold-fixed", kind="FIXED_SIZE", lifecycle="PROBATION", action="HOLD", quantity=0)
    result = StrategyResult(
        strategy_id="hold-fixed",
        cycles=3,
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
