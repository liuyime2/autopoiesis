from datetime import datetime, timezone

import pytest

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
    BrokerOrderSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
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
    # operational 0.5 (1 of 2 cycles faulted) x full exploration, less the
    # headroom reserved for broker-verified PnL that s1 does not have yet.
    assert s1.trade_attempts == 2
    assert s1.strategy_fault_rejections == 1
    assert s1.score == pytest.approx(0.45)
    assert s2.cycles == 1
    assert s2.skipped_orders == 1
    assert s2.rejected_orders == 0
    # A strategy that only ever held no longer earns a perfect score. It used to
    # score 1.0 and win the selector's argmax over strategies that traded, which
    # is what locked the agent into a permanent HOLD cycle.
    assert s2.trade_attempts == 0
    assert s2.score == pytest.approx(0.225)
    assert s1.score > s2.score


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


# ---------------------------------------------------------------------------
# The lot ledger is account-level, attributed to the strategy that opened a lot.
# Before this, lots were tracked per strategy, so the selector returning one
# strategy per cycle made a closed lot structurally unreachable: the strategy
# that opened a lot and the strategy that closed it were always different, and a
# SELL could only match lots in its own tracker. All four SELL decisions this
# agent ever produced therefore closed nothing, and strategy_realized_pnl was
# empty and could never be otherwise.
# ---------------------------------------------------------------------------


def test_a_sell_by_one_strategy_closes_a_lot_opened_by_another():
    opened = make_record(cycle_id="buy", action="BUY", quantity=2, strategy_id="opener")
    closed = make_record(
        cycle_id="sell", action="SELL", quantity=2, strategy_id="closer", last_price=110
    )
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 3, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 4, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="a-buy", order_id="oid-buy", client_order_id="cid-buy",
                symbol="SPY", side="BUY", quantity=2, price=100,
                transaction_time=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
            ),
            BrokerFillActivity(
                activity_id="a-sell", order_id="oid-sell", client_order_id="cid-sell",
                symbol="SPY", side="SELL", quantity=2, price=110,
                transaction_time=datetime(2026, 6, 3, 15, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate([opened, closed], evidence=evidence)

    assert report.pnl.closed_lot_count == 1
    # The lot's PnL belongs to the strategy that opened it, not the one that closed it.
    assert report.pnl.strategy_realized_pnl == {"opener": 20.0}
    assert "closer" not in report.pnl.strategy_realized_pnl
    lot = report.pnl.closed_lots[0]
    assert lot.strategy_id == "opener"
    assert lot.closing_strategy_id == "closer"
    assert lot.realized_pnl == 20.0
    assert report.pnl.open_lot_quantity == {}


def test_an_unmatched_sell_is_reported_instead_of_being_dropped():
    """The old code incremented a tracker field nothing ever read, so a SELL that
    closed nothing vanished without a trace."""
    record = make_record(cycle_id="sell", action="SELL", quantity=5, strategy_id="closer")
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 3, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 4, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="a-sell", order_id="oid-sell", client_order_id="cid-sell",
                symbol="SPY", side="SELL", quantity=5, price=110, fees=1.0,
                transaction_time=datetime(2026, 6, 3, 15, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate([record], evidence=evidence)

    assert report.pnl.closed_lot_count == 0
    assert report.pnl.unmatched_sell_quantity == {"closer": 5.0}
    assert report.pnl.strategy_fees == {"closer": 1.0}, "the sell fee is still charged"
    assert "no prior linked BUY lot" in " ".join(report.pnl.missing_reasons)


def test_journal_confirmed_fills_establish_lots_opened_before_the_window():
    """The broker evidence batch only covers a short window, but the agent's lots
    were opened weeks earlier, so a SELL inside the window matched nothing and
    closed_lot_count stayed at 0 forever. Journal-confirmed fills restore the cost
    basis, and the price in each is one the broker reported."""
    closed = make_record(
        cycle_id="sell", action="SELL", quantity=1, strategy_id="closer", last_price=800
    )
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 18, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 19, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="a-sell", order_id="oid-sell", client_order_id="cid-sell",
                symbol="SPY", side="SELL", quantity=1, price=800,
                transaction_time=datetime(2026, 6, 18, 15, 0, tzinfo=timezone.utc),
            ),
        ),
    )
    seeded = (
        BrokerFillActivity(
            activity_id="cid-old-buy", client_order_id="cid-old-buy", symbol="SPY",
            side="BUY", quantity=1, price=700,
            transaction_time=datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc),
            source="alpaca_journal_confirmed_fill",
            strategy_id="opener",
        ),
    )

    report = DeterministicEvaluator().evaluate([closed], evidence=evidence, seeded_fills=seeded)

    assert report.pnl.closed_lot_count == 1
    assert report.pnl.strategy_realized_pnl == {"opener": 100.0}
    assert report.pnl.closed_lots[0].opened_at == datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc)
    assert any(attribution.seeded for attribution in report.pnl.fill_attributions)


def test_a_fill_present_in_both_sources_is_applied_once():
    record = make_record(cycle_id="buy", action="BUY", quantity=2, strategy_id="s1")
    activity = BrokerFillActivity(
        activity_id="cid-buy", order_id="oid-buy", client_order_id="cid-buy", symbol="SPY",
        side="BUY", quantity=2, price=100,
        transaction_time=datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc),
    )
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 3, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 4, tzinfo=timezone.utc),
        activities=(activity,),
    )

    report = DeterministicEvaluator().evaluate([record], evidence=evidence, seeded_fills=(activity,))

    assert report.pnl.open_lot_quantity == {"s1:SPY": 2}, "the same fill was counted twice"
    assert len(report.pnl.fill_attributions) == 1


def test_verified_pnl_reaches_the_metrics_layer_without_cycles_in_the_window():
    """A strategy can close a lot and have no cycle in the current window. The old
    `if strategy_id in buckets` guard dropped broker-verified PnL on the floor in
    exactly that case: the number was computed, reported in
    pnl.strategy_realized_pnl, and discarded before the lifecycle layer could see
    it, with no log line."""
    closed = make_record(
        cycle_id="sell", action="SELL", quantity=1, strategy_id="closer", last_price=800
    )
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 18, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 19, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="cid-old-buy", client_order_id="cid-old-buy", symbol="SPY",
                side="BUY", quantity=1, price=700, strategy_id="opener",
                transaction_time=datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc),
            ),
            BrokerFillActivity(
                activity_id="a-sell", order_id="oid-sell", client_order_id="cid-sell",
                symbol="SPY", side="SELL", quantity=1, price=800,
                transaction_time=datetime(2026, 6, 18, 15, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    report = DeterministicEvaluator().evaluate([closed], evidence=evidence)

    metrics = report.strategy_metrics
    assert metrics["opener"].realized_pnl == 100.0
    assert metrics["opener"].cycles == 0
    assert metrics["opener"].pnl_evidence == PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED


def test_a_split_fill_reported_as_several_activities_is_one_order():
    """One 52-share market order appears in the journal as a single entry keyed by
    the client_order_id the agent generated, and in the broker feed as two
    activities of 16 and 36 under its own execution ids. Keyed on activity_id the
    two sources never matched, the SELL was applied twice, and the ledger reported
    81 unmatched shares against 23 real lots.

    The order is the common identity, so the broker's activities are summed into
    one fill at the volume-weighted price and the journal's summary is discarded
    rather than added.
    """
    from min_agent.evaluator import _order_key

    opened = make_record(cycle_id="buy", action="BUY", quantity=23, strategy_id="opener")
    sold = make_record(cycle_id="sell", action="SELL", quantity=52, strategy_id="closer")

    journal_view = BrokerFillActivity(
        activity_id="min-agent-coid", order_id="oid-sell", client_order_id="min-agent-coid",
        symbol="SPY", side="SELL", quantity=52, price=766.57,
        transaction_time=datetime(2026, 9, 28, 15, 1, 4, tzinfo=timezone.utc),
    )
    broker_view = (
        BrokerFillActivity(
            activity_id="2026...::aaa", order_id="oid-sell", symbol="SPY", side="SELL",
            quantity=16, price=766.57,
            transaction_time=datetime(2026, 9, 28, 19, 1, 14, tzinfo=timezone.utc),
        ),
        BrokerFillActivity(
            activity_id="2026...::bbb", order_id="oid-sell", symbol="SPY", side="SELL",
            quantity=36, price=766.57,
            transaction_time=datetime(2026, 9, 28, 19, 1, 14, tzinfo=timezone.utc),
        ),
    )
    assert _order_key(journal_view) == _order_key(broker_view[0]), (
        "both views of one order must resolve to the same key"
    )
    assert _order_key(broker_view[0]) == _order_key(broker_view[1])

    seeded = (journal_view,) + tuple(
        BrokerFillActivity(
            activity_id=f"min-agent-buy-{i}", order_id=f"oid-buy-{i}",
            client_order_id=f"min-agent-buy-{i}", symbol="SPY", side="BUY",
            quantity=1, price=742.03, strategy_id="opener",
            transaction_time=datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc),
        )
        for i in range(23)
    )
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 9, 28, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 29, tzinfo=timezone.utc),
        activities=broker_view,
    )

    report = DeterministicEvaluator().evaluate(
        [opened, sold], evidence=evidence, seeded_fills=seeded
    )

    # 23 agent lots close; 52 - 23 = 29 shares were never the agent's.
    assert report.pnl.closed_lot_count == 23
    assert report.pnl.unmatched_sell_quantity == {"closer": 29.0}
    assert report.pnl.strategy_realized_pnl["opener"] == pytest.approx(
        (766.57 - 742.03) * 23, abs=0.01
    )
    sells = [a for a in report.pnl.fill_attributions if a.side == "SELL"]
    assert len(sells) == 1, "one order is one attribution, however the broker split it"


def test_a_split_fill_at_two_prices_is_volume_weighted():
    opened = make_record(cycle_id="buy", action="BUY", quantity=2, strategy_id="opener")
    sold = make_record(cycle_id="sell", action="SELL", quantity=2, strategy_id="closer")
    evidence = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 9, 28, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 29, tzinfo=timezone.utc),
        activities=(
            BrokerFillActivity(
                activity_id="p1", order_id="oid-sell", symbol="SPY", side="SELL",
                quantity=1, price=100.0,
                transaction_time=datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc),
            ),
            BrokerFillActivity(
                activity_id="p2", order_id="oid-sell", symbol="SPY", side="SELL",
                quantity=1, price=200.0,
                transaction_time=datetime(2026, 9, 28, 19, 0, 1, tzinfo=timezone.utc),
            ),
        ),
    )
    seeded = (
        BrokerFillActivity(
            activity_id="min-agent-buy", order_id="oid-buy", client_order_id="min-agent-buy",
            symbol="SPY", side="BUY", quantity=2, price=50.0, strategy_id="opener",
            transaction_time=datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc),
        ),
    )

    report = DeterministicEvaluator().evaluate(
        [opened, sold], evidence=evidence, seeded_fills=seeded
    )

    # Volume-weighted: (1*100 + 1*200) / 2 = 150, so (150 - 50) * 2 = 200.
    assert report.pnl.strategy_realized_pnl["opener"] == pytest.approx(200.0, abs=0.01)
