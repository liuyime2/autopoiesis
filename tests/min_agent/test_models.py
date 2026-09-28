from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from min_agent.models import (
    AccountSnapshot,
    CurriculumTask,
    DataSnapshot,
    GuardianResult,
    HeartbeatPayload,
    KnowledgeArtifact,
    StrategySpec,
    TradeDecision,
)


def test_trade_decision_rejects_sell_with_zero_quantity():
    with pytest.raises(ValidationError):
        TradeDecision(symbol="SPY", action="SELL", quantity=0, confidence=0.8, rationale="exit")


def test_trade_decision_normalizes_symbol_and_action():
    decision = TradeDecision(symbol="spy", action="buy", quantity=1, confidence=0.8, rationale="trend")

    assert decision.symbol == "SPY"
    assert decision.action == "BUY"


def test_guardian_rejection_requires_reason():
    with pytest.raises(ValidationError):
        GuardianResult(approved=False, reason="")


def test_data_snapshot_requires_real_source_label():
    account = AccountSnapshot(
        equity=100_000,
        cash=90_000,
        buying_power=90_000,
        portfolio_value=100_000,
        daily_loss=0,
    )

    with pytest.raises(ValidationError):
        DataSnapshot(
            symbol="SPY",
            timestamp=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
            market_open=True,
            last_price=100,
            source="mock",
            account=account,
        )


def test_strategy_spec_normalizes_symbols_and_rejects_code_parameters():
    with pytest.raises(ValidationError):
        StrategySpec(
            strategy_id="trend-1",
            name="Trend",
            kind="trend_follow",
            symbols=["spy"],
            parameters={"code": "print('unsafe')"},
            max_position_value=100,
            created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
            rationale="test",
        )

    spec = StrategySpec(
        strategy_id="trend-1",
        name="Trend",
        kind="trend_follow",
        symbols=["spy"],
        parameters={"reference_price": 100, "threshold_pct": 0.02, "quantity": 1, "confidence": 0.7},
        max_position_value=100,
        created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        rationale="test",
    )

    assert spec.kind == "TREND_FOLLOW"
    assert spec.symbols == ("SPY",)


def test_strategy_spec_rejects_unsupported_params():
    with pytest.raises(ValidationError):
        StrategySpec(
            strategy_id="bad-fixed",
            name="Bad Fixed",
            kind="FIXED_SIZE",
            symbols=["spy"],
            parameters={"fixed_size": 1000000.0, "holding_period": 1, "target_daily_return_pct": 10.0},
            max_position_value=100,
            created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
            rationale="test",
        )


def test_strategy_spec_enforces_per_kind_params():
    baseline = StrategySpec(
        strategy_id="baseline",
        name="Baseline",
        kind="HOLD_BASELINE",
        symbols=["spy"],
        parameters={},
        max_position_value=100,
        created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        rationale="test",
    )
    fixed = StrategySpec(
        strategy_id="fixed",
        name="Fixed",
        kind="FIXED_SIZE",
        symbols=["spy"],
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.7},
        max_position_value=100,
        created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        rationale="test",
    )

    assert baseline.parameters == {}
    assert fixed.parameters["quantity"] == 1

    with pytest.raises(ValidationError):
        StrategySpec(
            strategy_id="bad-trend",
            name="Bad Trend",
            kind="TREND_FOLLOW",
            symbols=["spy"],
            parameters={"reference_price": 100, "threshold_pct": 0.30, "quantity": 1, "confidence": 0.7},
            max_position_value=100,
            created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
            rationale="test",
        )


def test_curriculum_strategy_task_requires_strategy_spec():
    with pytest.raises(ValidationError):
        CurriculumTask(
            task_id="task-1",
            task_type="strategy_spec",
            summary="create a candidate",
            rationale="explore safely",
        )


def test_knowledge_artifact_requires_citations_for_non_operator_sources():
    with pytest.raises(ValidationError):
        KnowledgeArtifact(
            artifact_id="qa one",
            artifact_type="qa",
            question="What happened?",
            answer="A journal event was observed.",
            summary="Journal observation.",
            tags=("lesson",),
            source_kind="journal",
            source_refs=(),
            created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
            rationale="test",
        )


def test_knowledge_artifact_normalizes_id_and_rejects_executable_content():
    artifact = KnowledgeArtifact(
        artifact_id="QA One",
        artifact_type="qa",
        question="What should be reused?",
        answer="Use Guardian checks before orders.",
        summary="Guardian checks are mandatory.",
        tags=("Risk",),
        source_kind="operator_note",
        source_refs=(),
        created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        rationale="operator note",
    )

    assert artifact.artifact_id == "qa-one"
    assert artifact.artifact_type == "QA"
    assert artifact.tags == ("risk",)

    with pytest.raises(ValidationError):
        KnowledgeArtifact(
            artifact_id="bad-qa",
            artifact_type="QA",
            answer="Run subprocess to trade.",
            summary="unsafe",
            tags=(),
            source_kind="operator_note",
            source_refs=(),
            created_at=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
            rationale="test",
        )


def test_heartbeat_status_is_normalized():
    heartbeat = HeartbeatPayload(
        pid=123,
        status="running",
        timestamp=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        cycle_count=1,
        error_count=0,
    )

    assert heartbeat.status == "RUNNING"
