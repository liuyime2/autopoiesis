from datetime import datetime, timezone

from autopoiesis.models import AccountSnapshot, DataSnapshot, ReflectionRecord, StrategySpec
from autopoiesis.policy_engine import PolicyEngine
from autopoiesis.reflection_memory import ReflectionMemory
from autopoiesis.strategy_engine import StrategyLibrary


def make_snapshot(*, symbol="SPY", last_price=100):
    return DataSnapshot(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        market_open=True,
        last_price=last_price,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
    )


def make_spec(*, strategy_id="test-strat", kind="FIXED_SIZE", score=None, enabled=True):
    return StrategySpec(
        strategy_id=strategy_id,
        name="Test Strategy",
        kind=kind,
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 3, "confidence": 0.8},
        max_position_value=10_000,
        enabled=enabled,
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        rationale="test",
    )


def test_policy_engine_falls_back_to_hold_without_enabled_strategy(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    engine = PolicyEngine(strategy_library=library)

    decision = engine.decide_snapshot(make_snapshot())

    assert decision.action == "HOLD"
    assert decision.quantity == 0
    assert decision.strategy_id is None


def test_policy_engine_executes_selected_strategy(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    library.save(make_spec(strategy_id="fixed-buy"))
    engine = PolicyEngine(strategy_library=library)

    decision = engine.decide_snapshot(make_snapshot(last_price=100))

    assert decision.action == "BUY"
    assert decision.quantity == 3
    assert decision.strategy_id == "fixed-buy"


def test_policy_engine_uses_reflection_scores(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    library.save(make_spec(strategy_id="low"))
    library.save(make_spec(strategy_id="high"))
    reflection_memory = ReflectionMemory(tmp_path / "reflection.json")
    reflection_memory.save(
        ReflectionRecord(
            generated_at=datetime.now(tz=timezone.utc),
            window_cycles=10,
            submitted_orders=0,
            rejected_orders=0,
            error_count=0,
            guardian_rejections={},
            strategy_scores={"low": 0.2, "high": 0.9},
            summary="test",
        )
    )
    engine = PolicyEngine(strategy_library=library, reflection_memory=reflection_memory)

    decision = engine.decide_snapshot(make_snapshot())

    assert decision.strategy_id == "high"


def test_policy_engine_ignores_disabled_strategies(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    library.save(make_spec(strategy_id="disabled", enabled=False))
    engine = PolicyEngine(strategy_library=library)

    decision = engine.decide_snapshot(make_snapshot())

    assert decision.action == "HOLD"
    assert decision.strategy_id is None
