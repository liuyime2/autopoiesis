from datetime import datetime, timezone

from min_agent.evaluator import (
    PNL_EVIDENCE_ACCOUNT_VERIFIED,
    PNL_EVIDENCE_MISSING,
    PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
    DeterministicEvaluator,
)
from min_agent.models import (
    AccountSnapshot,
    BrokerEvidenceBatch,
    BrokerFillActivity,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    BrokerOrderSnapshot,
    PortfolioHistoryPoint,
    TradeDecision,
)


def make_record(
    *,
    cycle_id="c1",
    status="SUBMITTED",
    action="BUY",
    quantity=2,
    last_price=100,
    equity=100_000,
    market_open=True,
    source="alpaca",
    strategy_id="s1",
    guardian_approved=True,
    guardian_reason="approved",
    filled_quantity=0,
    error=None,
):
    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
        market_open=market_open,
        last_price=last_price,
        source=source,
        account=AccountSnapshot(
            equity=equity,
            cash=equity,
            buying_power=equity,
            portfolio_value=equity,
            daily_loss=0,
        ),
    )
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=snapshot,
        decision=TradeDecision(
            symbol="SPY",
            action=action,
            quantity=quantity,
            confidence=0.6,
            rationale="test",
            strategy_id=strategy_id,
        ),
        guardian=GuardianResult(approved=guardian_approved, reason=guardian_reason),
        execution=ExecutionResult(
            status=status,
            order_id=f"oid-{cycle_id}" if status == "SUBMITTED" else None,
            client_order_id=f"cid-{cycle_id}" if status == "SUBMITTED" else None,
            filled_quantity=filled_quantity,
            message="test",
        ),
        strategy_id=strategy_id,
        error=error,
    )


def test_evaluator_empty_window_reports_missing_pnl_evidence():
    report = DeterministicEvaluator().evaluate([])

    assert report.window_cycles == 0
    assert report.submitted_orders == 0
    assert report.strategy_metrics == {}
    assert report.pnl_evidence == PNL_EVIDENCE_MISSING
    assert report.observed_equity_delta is None


def test_evaluator_counts_statuses_guardian_reasons_actions_and_notional():
    records = [
        make_record(cycle_id="c1", status="SUBMITTED", action="BUY", quantity=2, last_price=100, filled_quantity=1),
        make_record(
            cycle_id="c2",
            status="REJECTED",
            action="SELL",
            quantity=1,
            last_price=120,
            guardian_approved=False,
            guardian_reason="market is closed",
        ),
        make_record(cycle_id="c3", status="SKIPPED", action="HOLD", quantity=0, market_open=False),
        make_record(cycle_id="c4", status="ERROR", action="BUY", quantity=1, error="decision_error"),
    ]

    report = DeterministicEvaluator().evaluate(records)

    assert report.window_cycles == 4
    assert report.submitted_orders == 1
    assert report.rejected_orders == 1
    assert report.skipped_orders == 1
    assert report.execution_errors == 1
    assert report.error_count == 1
    assert report.guardian_rejections == {"market is closed": 1}
    assert report.action_counts == {"BUY": 2, "SELL": 1, "HOLD": 1}
    assert report.intended_notional == 420
    assert report.submitted_intended_notional == 200
    assert report.filled_quantity == 1
    assert report.market_open_cycles == 3
    assert report.market_closed_cycles == 1
    assert report.data_sources == {"alpaca": 4}


def test_evaluator_records_observed_equity_delta_without_calling_it_pnl():
    records = [
        make_record(cycle_id="c1", equity=100_000),
        make_record(cycle_id="c2", equity=100_500),
    ]

    report = DeterministicEvaluator().evaluate(records)

    assert report.first_observed_equity == 100_000
    assert report.last_observed_equity == 100_500
    assert report.observed_equity_delta == 500
    assert report.pnl_evidence == PNL_EVIDENCE_MISSING


def test_evaluator_keeps_per_strategy_metrics_independent():
    records = [
        make_record(cycle_id="c1", strategy_id="s1", status="SUBMITTED", filled_quantity=2),
        make_record(
            cycle_id="c2",
            strategy_id="s1",
            status="REJECTED",
            guardian_approved=False,
            guardian_reason="hard limit",
        ),
        make_record(cycle_id="c3", strategy_id="s2", status="SKIPPED", action="HOLD", quantity=0),
    ]

    report = DeterministicEvaluator().evaluate(records)
    s1 = report.strategy_metrics["s1"]
    s2 = report.strategy_metrics["s2"]

    assert s1.cycles == 2
    assert s1.submitted_orders == 1
    assert s1.rejected_orders == 1
    assert s1.guardian_rejections == {"hard limit": 1}
    assert s1.filled_quantity == 2
    assert s1.score == 0.5
    assert s2.cycles == 1
    assert s2.skipped_orders == 1
    assert s2.rejected_orders == 0
    assert s2.score == 1.0


def test_evaluator_uses_broker_portfolio_history_for_account_pnl():
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

    report = DeterministicEvaluator().evaluate([make_record()], evidence=evidence)

    assert report.pnl_evidence == PNL_EVIDENCE_ACCOUNT_VERIFIED
    assert report.pnl.account_realized_or_reported_pnl == 1_000
    assert report.pnl.account_return_pct == 0.01


def test_evaluator_attributes_realized_pnl_only_to_linked_closed_fills():
    records = [make_record(cycle_id="buy", action="BUY", quantity=1), make_record(cycle_id="sell", action="SELL", quantity=1)]
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="a-buy",
                order_id="oid-buy",
                client_order_id="cid-buy",
                symbol="SPY",
                side="BUY",
                quantity=1,
                price=100,
                fees=1,
                transaction_time=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
            ),
            BrokerFillActivity(
                activity_id="a-sell",
                order_id="oid-sell",
                client_order_id="cid-sell",
                symbol="SPY",
                side="SELL",
                quantity=1,
                price=110,
                fees=1,
                transaction_time=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate(records, evidence=evidence)

    assert report.pnl_evidence == PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    assert report.pnl.strategy_realized_pnl == {"s1": 8.0}
    assert report.pnl.linked_fill_count == 2
    assert report.pnl.unlinked_fill_count == 0
    assert report.pnl.closed_lot_count == 1
    assert report.pnl.closed_lots[0].strategy_id == "s1"
    assert report.pnl.closed_lots[0].buy_fill_id == "a-buy"
    assert report.pnl.closed_lots[0].sell_fill_id == "a-sell"
    assert report.pnl.closed_lots[0].realized_pnl == 8.0
    assert report.pnl.fill_attributions[0].attribution_status == "LINKED"
    assert report.strategy_metrics["s1"].realized_pnl == 8.0


def test_evaluator_reports_unlinked_fills_without_strategy_pnl():
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="a-external",
                order_id="external-order",
                client_order_id="external-client",
                symbol="SPY",
                side="BUY",
                quantity=1,
                price=100,
                transaction_time=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate([make_record(cycle_id="buy", action="BUY", quantity=1)], evidence=evidence)

    assert report.pnl.strategy_realized_pnl == {}
    assert report.pnl.linked_fill_count == 0
    assert report.pnl.unlinked_fill_count == 1
    assert report.pnl.fill_attributions[0].attribution_status == "UNLINKED"
    assert "not linked" in " ".join(report.pnl.missing_reasons)


def test_evaluator_reports_open_lots_without_realized_pnl():
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="a-buy",
                order_id="oid-buy",
                client_order_id="cid-buy",
                symbol="SPY",
                side="BUY",
                quantity=2,
                price=100,
                transaction_time=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate([make_record(cycle_id="buy", action="BUY", quantity=2)], evidence=evidence)

    assert report.pnl.strategy_realized_pnl == {}
    assert report.pnl.linked_fill_count == 1
    assert report.pnl.closed_lot_count == 0
    assert report.pnl.open_lot_quantity == {"s1:SPY": 2}
    assert "no linked SELL" in " ".join(report.pnl.missing_reasons)


def test_evaluator_uses_broker_filled_order_snapshots_as_labeled_fill_evidence():
    records = [make_record(cycle_id="buy", action="BUY", quantity=1), make_record(cycle_id="sell", action="SELL", quantity=1)]
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        orders=(
            BrokerOrderSnapshot(
                order_id="oid-buy",
                client_order_id="cid-buy",
                symbol="SPY",
                side="BUY",
                quantity=1,
                filled_quantity=1,
                filled_avg_price=100,
                status="filled",
                filled_at=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
            ),
            BrokerOrderSnapshot(
                order_id="oid-sell",
                client_order_id="cid-sell",
                symbol="SPY",
                side="SELL",
                quantity=1,
                filled_quantity=1,
                filled_avg_price=112,
                status="filled",
                filled_at=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate(records, evidence=evidence)

    assert report.pnl_evidence == PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    assert report.pnl.strategy_realized_pnl == {"s1": 12.0}
    assert report.pnl.order_derived_fill_count == 2
    assert report.pnl.closed_lot_count == 1
    assert report.pnl.closed_lots[0].source == "alpaca_orders"
    assert "broker order snapshots" in " ".join(report.pnl.missing_reasons)


def test_evaluator_keeps_zero_filled_orders_unproven_with_reason():
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        orders=(
            BrokerOrderSnapshot(
                order_id="oid-buy",
                client_order_id="cid-buy",
                symbol="SPY",
                side="BUY",
                quantity=1,
                filled_quantity=0,
                filled_avg_price=None,
                status="pending_new",
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate([make_record(cycle_id="buy", action="BUY", quantity=1)], evidence=evidence)

    assert report.pnl.strategy_realized_pnl == {}
    assert report.pnl.closed_lot_count == 0
    assert report.pnl.order_derived_fill_count == 0
    assert "no filled quantities/prices" in " ".join(report.pnl.missing_reasons)


def test_evaluator_reports_partial_close_and_remaining_open_lot():
    records = [make_record(cycle_id="buy", action="BUY", quantity=2), make_record(cycle_id="sell", action="SELL", quantity=1)]
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="a-buy",
                order_id="oid-buy",
                client_order_id="cid-buy",
                symbol="SPY",
                side="BUY",
                quantity=2,
                price=100,
                fees=2,
                transaction_time=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
            ),
            BrokerFillActivity(
                activity_id="a-sell",
                order_id="oid-sell",
                client_order_id="cid-sell",
                symbol="SPY",
                side="SELL",
                quantity=1,
                price=110,
                fees=1,
                transaction_time=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate(records, evidence=evidence)

    assert report.pnl.strategy_realized_pnl == {"s1": 8.0}
    assert report.pnl.closed_lots[0].quantity == 1
    assert report.pnl.open_lot_quantity == {"s1:SPY": 1}
