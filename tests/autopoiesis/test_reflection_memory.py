from datetime import datetime, timezone

from autopoiesis.evaluator import PNL_EVIDENCE_MISSING
from autopoiesis.models import (
    AccountSnapshot,
    BrokerEvidenceBatch,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    PortfolioHistoryPoint,
    ReflectionRecord,
    TradeDecision,
)
from autopoiesis.reflection_memory import ReflectionMemory


def record(*, status="SUBMITTED", guardian_approved=True, guardian_reason="approved", strategy_id=None, has_error=None):
    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime.now(tz=timezone.utc),
        market_open=True,
        last_price=100,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
    )
    return CycleRecord(
        cycle_id="c1",
        snapshot=snapshot,
        decision=TradeDecision(
            symbol="SPY",
            action="BUY" if status != "SKIPPED" else "HOLD",
            quantity=1 if status != "SKIPPED" else 0,
            confidence=0.6,
            rationale="test",
            strategy_id=strategy_id,
        ),
        guardian=GuardianResult(approved=guardian_approved, reason=guardian_reason),
        execution=ExecutionResult(
            status=status if guardian_approved else ("REJECTED" if not guardian_approved else status),
            order_id="oid-1" if status == "SUBMITTED" else None,
            filled_quantity=0,
            message="test",
        ),
        strategy_id=strategy_id,
        error=has_error,
    )


def test_reflection_empty_records():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    reflection = memory.reflect([])
    assert reflection.window_cycles == 0
    assert reflection.submitted_orders == 0
    assert reflection.rejected_orders == 0
    assert reflection.error_count == 0
    assert reflection.strategy_scores == {}
    assert reflection.evaluation["pnl_evidence"] == PNL_EVIDENCE_MISSING


def test_reflection_counts_submitted_and_rejected():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    records = [
        record(status="SUBMITTED", strategy_id="s1"),
        record(status="REJECTED", guardian_approved=False, guardian_reason="market closed", strategy_id="s1"),
        record(status="SKIPPED", strategy_id="s2"),
    ]
    reflection = memory.reflect(records)
    assert reflection.window_cycles == 3
    assert reflection.submitted_orders == 1
    assert reflection.rejected_orders >= 1
    assert reflection.evaluation["skipped_orders"] == 1


def test_reflection_accepts_broker_evidence():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        portfolio_history=(
            PortfolioHistoryPoint(
                timestamp=datetime(2026, 6, 10, 20, 0, tzinfo=timezone.utc),
                equity=101_000,
                profit_loss=1_000,
                profit_loss_pct=0.01,
            ),
        ),
    )

    reflection = memory.reflect([record(status="SUBMITTED", strategy_id="s1")], evidence=evidence)

    assert reflection.evaluation["pnl_evidence"] == "broker_portfolio_history_verified"
    assert "pnl_evidence=broker_portfolio_history_verified" in reflection.summary


def test_reflection_records_guardian_rejection_reasons():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    rejection_reason = "market is closed"
    records = [
        record(status="SUBMITTED", strategy_id="s1"),
        record(status="REJECTED", guardian_approved=False, guardian_reason=rejection_reason, strategy_id="s1"),
    ]
    reflection = memory.reflect(records)
    assert rejection_reason in reflection.guardian_rejections


def test_reflection_counts_errors():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    records = [
        record(status="SUBMITTED", strategy_id="s1"),
        record(status="SUBMITTED", strategy_id="s1", has_error="LLM timeout"),
    ]
    reflection = memory.reflect(records)
    assert reflection.error_count == 1


def test_reflection_save_and_load(tmp_path):
    memory = ReflectionMemory(tmp_path / "reflect.json")
    reflection = memory.reflect([])
    memory.save(reflection)
    loaded = memory.load()
    assert loaded is not None
    assert loaded.window_cycles == 0


def test_strategy_results_use_per_strategy_counts():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    records = [
        record(status="SUBMITTED", strategy_id="s1"),
        record(status="SUBMITTED", strategy_id="s1"),
        record(status="REJECTED", guardian_approved=False, guardian_reason="market closed", strategy_id="s1"),
        record(status="SUBMITTED", strategy_id="s2"),
    ]
    reflection = memory.reflect(records)
    results = memory.strategy_results(reflection)
    by_id = {result.strategy_id: result for result in results}

    assert by_id["s1"].cycles == 3
    assert by_id["s1"].submitted_orders == 2
    assert by_id["s1"].rejected_orders == 1
    assert by_id["s2"].cycles == 1
    assert by_id["s2"].submitted_orders == 1
    assert by_id["s2"].rejected_orders == 0
    assert by_id["s1"].pnl_evidence == PNL_EVIDENCE_MISSING


def test_strategy_results_preserve_strategy_pnl_fields():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    reflection = ReflectionRecord(
        generated_at=datetime.now(tz=timezone.utc),
        window_cycles=1,
        submitted_orders=1,
        rejected_orders=0,
        error_count=0,
        guardian_rejections={},
        strategy_scores={"s1": 0.4},
        summary="test",
        strategy_metrics={
            "s1": {
                "cycles": 1,
                "submitted_orders": 1,
                "rejected_orders": 0,
                "skipped_orders": 0,
                "errors": 0,
                "action_counts": {"BUY": 1},
                "guardian_rejections": {},
                "intended_notional": 100.0,
                "filled_quantity": 1.0,
                "realized_pnl": -2.5,
                "fees": 0.1,
                "pnl_evidence": "broker_strategy_closed_lot_pnl_verified",
            }
        },
    )

    [result] = memory.strategy_results(reflection)

    assert result.realized_pnl == -2.5
    assert result.fees == 0.1
    assert result.pnl_evidence == "broker_strategy_closed_lot_pnl_verified"


def test_strategy_results_load_older_reflection_shape():
    memory = ReflectionMemory("/tmp/test-reflect.json")
    reflection = ReflectionRecord(
        generated_at=datetime.now(tz=timezone.utc),
        window_cycles=5,
        submitted_orders=2,
        rejected_orders=1,
        error_count=1,
        guardian_rejections={},
        strategy_scores={"legacy": 0.8},
        summary="legacy",
    )

    [result] = memory.strategy_results(reflection)

    assert result.strategy_id == "legacy"
    assert result.cycles == 5
    assert result.submitted_orders == 2
    assert result.rejected_orders == 1
