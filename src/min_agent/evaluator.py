from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from min_agent.models import (
    BrokerEvidenceBatch,
    BrokerFillActivity,
    ClosedLotAttribution,
    CycleRecord,
    EvaluationReport,
    FillAttribution,
    PnLEvidence,
    StrategyEvaluation,
)

PNL_EVIDENCE_MISSING = "missing_fill_price_and_broker_activity"
PNL_EVIDENCE_PARTIAL_FILLS = "broker_fills_matched_without_portfolio_history"
PNL_EVIDENCE_ACCOUNT_VERIFIED = "broker_portfolio_history_verified"
PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED = "broker_strategy_closed_lot_pnl_verified"


class DeterministicEvaluator:
    def evaluate(self, records: list[CycleRecord], evidence: BrokerEvidenceBatch | None = None) -> EvaluationReport:
        buckets: dict[str, _StrategyBucket] = {}
        guardian_rejections: dict[str, int] = {}
        action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        data_sources: dict[str, int] = {}
        submitted = 0
        rejected = 0
        skipped = 0
        execution_errors = 0
        error_count = 0
        guardian_approved = 0
        guardian_rejected = 0
        market_open_cycles = 0
        intended_notional = 0.0
        submitted_intended_notional = 0.0
        filled_quantity = 0.0
        order_quantity = 0.0
        submitted_order_quantity = 0.0

        for record in records:
            status = record.execution.status
            action = record.decision.action
            notional = record.decision.quantity * record.snapshot.last_price
            strategy_id = record.strategy_id or record.decision.strategy_id

            action_counts[action] = action_counts.get(action, 0) + 1
            data_sources[record.snapshot.source] = data_sources.get(record.snapshot.source, 0) + 1
            intended_notional += notional
            filled_quantity += record.execution.filled_quantity
            if record.decision.quantity > 0:
                order_quantity += record.decision.quantity

            if record.snapshot.market_open:
                market_open_cycles += 1

            if status == "SUBMITTED":
                submitted += 1
                submitted_intended_notional += notional
                submitted_order_quantity += record.decision.quantity
            elif status == "REJECTED":
                rejected += 1
            elif status == "SKIPPED":
                skipped += 1
            elif status == "ERROR":
                execution_errors += 1

            if status == "ERROR" or record.error:
                error_count += 1

            if record.guardian.approved:
                guardian_approved += 1
            else:
                guardian_rejected += 1
                guardian_rejections[record.guardian.reason] = guardian_rejections.get(record.guardian.reason, 0) + 1

            if strategy_id:
                bucket = buckets.setdefault(strategy_id, _StrategyBucket(strategy_id=strategy_id))
                bucket.add(record, notional)

        market_closed_cycles = len(records) - market_open_cycles
        first_equity = records[0].snapshot.account.equity if records else None
        last_equity = records[-1].snapshot.account.equity if records else None
        observed_delta = None
        if first_equity is not None and last_equity is not None:
            observed_delta = last_equity - first_equity

        pnl = _pnl_evidence(records, evidence)
        for strategy_id, realized_pnl in pnl.strategy_realized_pnl.items():
            if strategy_id in buckets:
                buckets[strategy_id].realized_pnl = realized_pnl
                buckets[strategy_id].fees = pnl.strategy_fees.get(strategy_id, 0.0)
                buckets[strategy_id].pnl_evidence = pnl.status

        return EvaluationReport(
            generated_at=datetime.now(tz=timezone.utc),
            window_cycles=len(records),
            submitted_orders=submitted,
            rejected_orders=rejected,
            skipped_orders=skipped,
            execution_errors=execution_errors,
            error_count=error_count,
            guardian_approved=guardian_approved,
            guardian_rejected=guardian_rejected,
            guardian_rejections=guardian_rejections,
            action_counts={key: value for key, value in action_counts.items() if value},
            data_sources=data_sources,
            market_open_cycles=market_open_cycles,
            market_closed_cycles=market_closed_cycles,
            intended_notional=intended_notional,
            submitted_intended_notional=submitted_intended_notional,
            filled_quantity=filled_quantity,
            fill_quantity_ratio=_ratio(filled_quantity, submitted_order_quantity or order_quantity),
            first_observed_equity=first_equity,
            last_observed_equity=last_equity,
            observed_equity_delta=observed_delta,
            pnl_evidence=pnl.status,
            pnl=pnl,
            strategy_metrics={strategy_id: bucket.to_evaluation() for strategy_id, bucket in buckets.items()},
        )


class _StrategyBucket:
    def __init__(self, *, strategy_id: str):
        self.strategy_id = strategy_id
        self.cycles = 0
        self.submitted_orders = 0
        self.rejected_orders = 0
        self.skipped_orders = 0
        self.errors = 0
        self.action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        self.guardian_rejections: dict[str, int] = {}
        self.intended_notional = 0.0
        self.submitted_intended_notional = 0.0
        self.filled_quantity = 0.0
        self.order_quantity = 0.0
        self.submitted_order_quantity = 0.0
        self.pnl_evidence = PNL_EVIDENCE_MISSING
        self.realized_pnl: float | None = None
        self.fees: float | None = None

    def add(self, record: CycleRecord, notional: float) -> None:
        self.cycles += 1
        self.action_counts[record.decision.action] = self.action_counts.get(record.decision.action, 0) + 1
        self.intended_notional += notional
        self.filled_quantity += record.execution.filled_quantity
        if record.decision.quantity > 0:
            self.order_quantity += record.decision.quantity

        if record.execution.status == "SUBMITTED":
            self.submitted_orders += 1
            self.submitted_intended_notional += notional
            self.submitted_order_quantity += record.decision.quantity
        elif record.execution.status == "REJECTED":
            self.rejected_orders += 1
        elif record.execution.status == "SKIPPED":
            self.skipped_orders += 1
        elif record.execution.status == "ERROR":
            self.errors += 1

        if record.error:
            self.errors += 1

        if not record.guardian.approved:
            self.guardian_rejections[record.guardian.reason] = self.guardian_rejections.get(record.guardian.reason, 0) + 1

    def to_evaluation(self) -> StrategyEvaluation:
        return StrategyEvaluation(
            strategy_id=self.strategy_id,
            cycles=self.cycles,
            submitted_orders=self.submitted_orders,
            rejected_orders=self.rejected_orders,
            skipped_orders=self.skipped_orders,
            errors=self.errors,
            action_counts={key: value for key, value in self.action_counts.items() if value},
            guardian_rejections=self.guardian_rejections,
            intended_notional=self.intended_notional,
            submitted_intended_notional=self.submitted_intended_notional,
            filled_quantity=self.filled_quantity,
            fill_quantity_ratio=_ratio(self.filled_quantity, self.submitted_order_quantity or self.order_quantity),
            pnl_evidence=self.pnl_evidence,
            realized_pnl=self.realized_pnl,
            fees=self.fees,
            score=_score(self.cycles, self.errors, self.rejected_orders, self.realized_pnl),
        )


@dataclass
class _Lot:
    quantity: float
    price: float
    fees: float
    fill_id: str
    order_id: str | None
    client_order_id: str | None
    opened_at: datetime
    source: str


@dataclass
class _StrategyPnl:
    lots: dict[str, list[_Lot]] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees: float = 0.0
    closed_quantity: float = 0.0
    closed_lots: list[ClosedLotAttribution] = field(default_factory=list)
    unmatched_sell_quantity: float = 0.0


def evaluate_cycles(records: list[CycleRecord]) -> EvaluationReport:
    return DeterministicEvaluator().evaluate(records)


def _pnl_evidence(records: list[CycleRecord], evidence: BrokerEvidenceBatch | None) -> PnLEvidence:
    if evidence is None:
        return PnLEvidence(status=PNL_EVIDENCE_MISSING, missing_reasons=(PNL_EVIDENCE_MISSING,))

    order_to_strategy = _order_strategy_map(records)
    strategy_pnl: dict[str, _StrategyPnl] = {}
    missing_reasons = list(evidence.missing_reasons)
    fill_candidates, order_derived_fill_count = _fill_candidates(evidence)
    fill_attributions: list[FillAttribution] = []
    linked_buy_count = 0
    linked_sell_count = 0
    unlinked_fill_count = 0

    for activity in sorted(fill_candidates, key=lambda item: item.transaction_time):
        strategy_id = _activity_strategy(activity, order_to_strategy)
        status = "LINKED" if strategy_id else "UNLINKED"
        fill_attributions.append(_fill_attribution(activity, strategy_id=strategy_id, status=status))
        if strategy_id is None:
            unlinked_fill_count += 1
            continue
        if activity.side == "BUY":
            linked_buy_count += 1
        if activity.side == "SELL":
            linked_sell_count += 1
        tracker = strategy_pnl.setdefault(strategy_id, _StrategyPnl())
        _apply_activity(tracker, activity, strategy_id=strategy_id)

    closed_lots = [lot for tracker in strategy_pnl.values() for lot in tracker.closed_lots]
    strategy_realized = {
        strategy_id: round(tracker.realized_pnl, 10)
        for strategy_id, tracker in strategy_pnl.items()
        if tracker.closed_quantity > 0
    }
    strategy_fees = {
        strategy_id: round(tracker.fees, 10)
        for strategy_id, tracker in strategy_pnl.items()
        if tracker.fees > 0
    }
    open_lot_quantity = _open_lot_quantity(strategy_pnl)

    if order_derived_fill_count:
        missing_reasons.append(
            f"used broker order snapshots as fill evidence for {order_derived_fill_count} filled order(s) without FILL activities"
        )
    if evidence.orders and not fill_candidates:
        missing_reasons.append("broker orders present but no filled quantities/prices were available")
    if unlinked_fill_count:
        missing_reasons.append(
            f"broker fills present but {unlinked_fill_count} fill(s) were not linked to journaled order_id/client_order_id"
        )
    if fill_candidates and not closed_lots:
        if linked_buy_count and not linked_sell_count:
            missing_reasons.append("linked BUY fills exist but no linked SELL fills closed lots")
        elif linked_sell_count and not linked_buy_count:
            missing_reasons.append("linked SELL fills exist but no prior linked BUY lots existed in this evidence window")
        elif linked_buy_count or linked_sell_count:
            missing_reasons.append("no linked closed lots in evidence window")
        elif unlinked_fill_count:
            missing_reasons.append("broker fills were excluded from strategy PnL because they were unlinked")

    account_pnl = None
    account_return_pct = None
    if evidence.portfolio_history:
        last_point = sorted(evidence.portfolio_history, key=lambda item: item.timestamp)[-1]
        account_pnl = last_point.profit_loss
        account_return_pct = last_point.profit_loss_pct

    status = PNL_EVIDENCE_MISSING
    if strategy_realized:
        status = PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    elif evidence.portfolio_history:
        status = PNL_EVIDENCE_ACCOUNT_VERIFIED
    elif evidence.activities or evidence.orders:
        status = PNL_EVIDENCE_PARTIAL_FILLS
    else:
        missing_reasons.append("broker evidence contained no fills, orders, or portfolio history")

    return PnLEvidence(
        status=status,
        account_realized_or_reported_pnl=account_pnl,
        account_return_pct=account_return_pct,
        strategy_realized_pnl=strategy_realized,
        strategy_fees=strategy_fees,
        unattributed_pnl=0.0 if unlinked_fill_count else None,
        window_start=evidence.window_start,
        window_end=evidence.window_end,
        evidence_source=evidence.source_account,
        missing_reasons=tuple(dict.fromkeys(missing_reasons)),
        fill_attributions=tuple(fill_attributions),
        closed_lots=tuple(closed_lots),
        linked_fill_count=len(fill_attributions) - unlinked_fill_count,
        unlinked_fill_count=unlinked_fill_count,
        closed_lot_count=len(closed_lots),
        order_derived_fill_count=order_derived_fill_count,
        open_lot_quantity=open_lot_quantity,
    )


def _order_strategy_map(records: list[CycleRecord]) -> dict[str, str]:
    mapping = {}
    for record in records:
        strategy_id = record.strategy_id or record.decision.strategy_id
        if not strategy_id:
            continue
        if record.execution.order_id:
            mapping[record.execution.order_id] = strategy_id
        if record.execution.client_order_id:
            mapping[record.execution.client_order_id] = strategy_id
    return mapping


def _activity_strategy(activity: BrokerFillActivity, order_to_strategy: dict[str, str]) -> str | None:
    if activity.order_id and activity.order_id in order_to_strategy:
        return order_to_strategy[activity.order_id]
    if activity.client_order_id and activity.client_order_id in order_to_strategy:
        return order_to_strategy[activity.client_order_id]
    return None


def _fill_candidates(evidence: BrokerEvidenceBatch) -> tuple[list[BrokerFillActivity], int]:
    candidates = list(evidence.activities)
    activity_order_ids = {activity.order_id for activity in candidates if activity.order_id}
    order_derived = 0
    for order in evidence.orders:
        if order.order_id in activity_order_ids:
            continue
        if order.filled_quantity <= 0 or order.filled_avg_price is None:
            continue
        transaction_time = order.filled_at or order.submitted_at
        if transaction_time is None:
            continue
        candidates.append(
            BrokerFillActivity(
                activity_id=f"order-fill:{order.order_id}",
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.filled_quantity,
                price=order.filled_avg_price,
                gross_amount=order.filled_quantity * order.filled_avg_price,
                fees=0,
                transaction_time=transaction_time,
                source="alpaca_orders",
            )
        )
        order_derived += 1
    return candidates, order_derived


def _fill_attribution(activity: BrokerFillActivity, *, strategy_id: str | None, status: str) -> FillAttribution:
    return FillAttribution(
        fill_id=activity.activity_id,
        order_id=activity.order_id,
        client_order_id=activity.client_order_id,
        strategy_id=strategy_id,
        symbol=activity.symbol,
        side=activity.side,
        quantity=activity.quantity,
        price=activity.price,
        fees=activity.fees,
        transaction_time=activity.transaction_time,
        source=activity.source,
        attribution_status=status,
    )


def _open_lot_quantity(strategy_pnl: dict[str, _StrategyPnl]) -> dict[str, float]:
    quantities: dict[str, float] = {}
    for strategy_id, tracker in strategy_pnl.items():
        for symbol, lots in tracker.lots.items():
            quantity = sum(lot.quantity for lot in lots)
            if quantity > 0:
                quantities[f"{strategy_id}:{symbol}"] = round(quantity, 10)
    return quantities


def _apply_activity(tracker: _StrategyPnl, activity: BrokerFillActivity, *, strategy_id: str) -> None:
    tracker.fees += activity.fees
    if activity.side == "BUY":
        tracker.lots.setdefault(activity.symbol, []).append(
            _Lot(
                quantity=activity.quantity,
                price=activity.price,
                fees=activity.fees,
                fill_id=activity.activity_id,
                order_id=activity.order_id,
                client_order_id=activity.client_order_id,
                opened_at=activity.transaction_time,
                source=activity.source,
            )
        )
        return

    remaining = activity.quantity
    lots = tracker.lots.setdefault(activity.symbol, [])
    sell_fee_remaining = activity.fees
    while remaining > 0 and lots:
        lot = lots[0]
        matched = min(remaining, lot.quantity)
        buy_fee = lot.fees * (matched / lot.quantity) if lot.quantity else 0.0
        sell_fee = sell_fee_remaining * (matched / remaining) if remaining else 0.0
        realized_pnl = (activity.price - lot.price) * matched - buy_fee - sell_fee
        tracker.realized_pnl += realized_pnl
        tracker.closed_quantity += matched
        tracker.closed_lots.append(
            ClosedLotAttribution(
                strategy_id=strategy_id,
                symbol=activity.symbol,
                buy_fill_id=lot.fill_id,
                sell_fill_id=activity.activity_id,
                buy_order_id=lot.order_id,
                sell_order_id=activity.order_id,
                buy_client_order_id=lot.client_order_id,
                sell_client_order_id=activity.client_order_id,
                quantity=matched,
                buy_price=lot.price,
                sell_price=activity.price,
                buy_fees=buy_fee,
                sell_fees=sell_fee,
                realized_pnl=realized_pnl,
                opened_at=lot.opened_at,
                closed_at=activity.transaction_time,
                source=_combined_source(lot.source, activity.source),
            )
        )
        lot.quantity -= matched
        lot.fees -= buy_fee
        remaining -= matched
        sell_fee_remaining -= sell_fee
        if lot.quantity <= 0:
            lots.pop(0)
    tracker.unmatched_sell_quantity += remaining


def _combined_source(open_source: str, close_source: str) -> str:
    if open_source == close_source:
        return open_source
    return f"{open_source}+{close_source}"


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _score(cycles: int, errors: int, rejected_orders: int, realized_pnl: float | None = None) -> float:
    if cycles <= 0:
        return 0.0
    penalty = errors + rejected_orders
    operational_score = max(0.0, 1.0 - (penalty / cycles))
    if realized_pnl is None:
        return operational_score
    if realized_pnl > 0:
        return min(1.0, operational_score + 0.1)
    if realized_pnl < 0:
        return max(0.0, operational_score - 0.2)
    return operational_score
