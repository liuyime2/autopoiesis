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
        StrategyResult(strategy_id="a", cycles=10, submitted_orders=3, rejected_orders=0, errors=0, score=0.3, evaluated_at=datetime.now(tz=timezone.utc)),
        StrategyResult(strategy_id="b", cycles=10, submitted_orders=5, rejected_orders=0, errors=0, score=0.7, evaluated_at=datetime.now(tz=timezone.utc)),
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
    result = StrategyResult(strategy_id="trial", cycles=3, submitted_orders=0, rejected_orders=0, errors=0, score=1.0, evaluated_at=datetime.now(tz=timezone.utc))

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "ACTIVE"


def test_lifecycle_manager_promotes_hold_only_trend_after_evidence_cycles():
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
    )

    [decision] = manager.review([strategy], [result])

    assert decision.new_lifecycle == "ACTIVE"


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
