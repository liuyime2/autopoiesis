from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import cast

from min_agent.models import (
    BROKER_SIDE,
    AttributionStatus,
    BrokerEvidenceBatch,
    BrokerFillActivity,
    ClosedLotAttribution,
    CycleRecord,
    EvaluationReport,
    FillAttribution,
    OpenLotAttribution,
    PnLEvidence,
    Side,
    StrategyEvaluation,
)

PNL_EVIDENCE_MISSING = "missing_fill_price_and_broker_activity"
PNL_EVIDENCE_PARTIAL_FILLS = "broker_fills_matched_without_portfolio_history"
PNL_EVIDENCE_ACCOUNT_VERIFIED = "broker_portfolio_history_verified"
PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED = "broker_strategy_closed_lot_pnl_verified"

# Fraction of the reliability score a strategy keeps for pure inaction. Anything
# above 0 and below 1 makes doing nothing strictly worse than acting while
# reliable, which is what the selector's argmax needs to see.
INACTION_FLOOR = 0.25
PNL_BONUS = 0.1
PNL_PENALTY = 0.2

# Guardian refusals that are the risk system working correctly, not the strategy
# misbehaving. A strategy stopped because the market closed, or because the
# daily trade limit was already reached, obeyed the system. Charging it for that
# is what made fixed-size-sell-001 get RETIRED for "severe operational failure
# rate 1.00" when every one of its orders was correctly blocked for selling
# shares the gateway could not see.
SYSTEM_REJECTION_REASONS = frozenset({
    "the tradable universe is unknown, so a symbol outside the allowlist is refused",
    "symbol is not a tradable US equity on a major exchange",
    "symbol is priced below the minimum",
    "symbol is a warrant, unit or right, not a stock",
    "market is closed",
    "max trades per day reached",
    "data snapshot is stale",
    "daily loss limit reached",
    "insufficient buying power",
    "conflicting open order exists",
    "only paper mode is allowed",
    "account day-start equity unavailable",
})

#: Rejection reasons that describe the *system's* state rather than a fault of the
#: strategy, matched on a stable literal prefix rather than the whole message.
#:
#: Several Guardian reasons interpolate the offending quantity and the limit they
#: breached, which is worth having in the journal and worth reading. It also means an
#: exact-equality test against `SYSTEM_REJECTION_REASONS` could never match them, so
#: they were charged to the strategy as its own fault. The cost was concrete: the only
#: SELL-capable strategy in the library was retired with "severe operational failure
#: rate 1.00" for three rejections whose stated cause was "the agent holds 0" - a
#: figure derived from the journal, not anything the strategy did.
#:
#: Prefixes rather than a code field on `GuardianResult`: 21 rejection branches would
#: each need one, and the message already carries the detail a reader needs. These
#: prefixes are stable literals in `guardian.py`; a test pins them so an edit that
#: rewords a message cannot silently reclassify it.
#:
#: Deliberately *not* here: "position value exceeds hard limit", "decision confidence
#: below minimum", "strategy position value exceeds hard limit". Those are the
#: strategy asking for something its own specification makes impermissible, which is
#: exactly what the fault counter is for.
SYSTEM_REJECTION_REASON_PREFIXES = (
    # The agent's own holding is derived from the journal, and the account's positions
    # come from a broker snapshot. Neither is a property of the strategy.
    "cannot sell ",
    "the agent's own holding of ",
    # Measured against the account's position, not the strategy's own specification.
    #
    # The comment below this list deliberately excludes "position value exceeds hard
    # limit", and that is right for a strategy whose *own* max_position_value its order
    # breaches. It does not hold for this one, which brackets the position the agent
    # already holds: "this buy would leave 13854.96 SPY held against a max_position_value
    # of 5000.00".
    #
    # tiny-fixed-size-001 declares max_position_value=1000 and its order is one share at
    # $769.72, comfortably inside its own limit. What breached the cap was a 17-share
    # position that predated its promotion, accumulated while the rule still measured the
    # order rather than the position. The strategy is charged for account state it can
    # neither see nor change, and its only correct response - decline to buy - is the very
    # thing that would have avoided the refusal. Measured live on 2026-10-05: every cycle
    # refused on this message gives failure_rate 1.00 against a 0.75 threshold, so the
    # library's only ACTIVE strategy was being retired for refusals it did not cause -
    # which is the no-ACTIVE-strategy state 3407ebc was written to end.
    #
    # Same reasoning as the two entries above it: derived from a broker snapshot, so not a
    # property of the strategy. The Guardian still sizes a single order against the
    # strategy's own limit, so a strategy asking for more than its specification permits
    # is still charged by that check, and the aggregate caps keep their own prefixes.
    "this buy would leave ",
    # Aggregate account state the strategy cannot see or choose.
    "agent exposure ",
    "account total ",
    "total exposure exceeds hard limit",
)


def is_system_rejection(reason: str) -> bool:
    """Whether a Guardian rejection is the system's state rather than the strategy's fault."""
    if reason in SYSTEM_REJECTION_REASONS:
        return True
    return any(reason.startswith(prefix) for prefix in SYSTEM_REJECTION_REASON_PREFIXES)


class DeterministicEvaluator:
    def evaluate(
        self,
        records: list[CycleRecord],
        evidence: BrokerEvidenceBatch | None = None,
        fills: Mapping[str, float] | None = None,
        seeded_fills: Sequence[BrokerFillActivity] | None = None,
        cumulative_cycles: Mapping[str, int] | None = None,
        owner_fills: Sequence[BrokerFillActivity] | None = None,
        cumulative_trade_attempts: Mapping[str, int] | None = None,
    ) -> EvaluationReport:
        buckets: dict[str, _StrategyBucket] = {}
        guardian_rejections: dict[str, int] = {}
        action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        hold_reasons: dict[str, int] = {}
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
        shadowed = 0

        for record in records:
            status = record.execution.status
            action = record.decision.action
            notional = record.decision.quantity * record.snapshot.last_price
            strategy_id = record.strategy_id or record.decision.strategy_id

            action_counts[action] = action_counts.get(action, 0) + 1
            if action == "HOLD" and record.decision.hold_reason:
                hold_reasons[record.decision.hold_reason] = (
                    hold_reasons.get(record.decision.hold_reason, 0) + 1
                )
            data_sources[record.snapshot.source] = data_sources.get(record.snapshot.source, 0) + 1
            intended_notional += notional
            # A market order's submit response always reports filled_qty 0.
            # Prefer the broker-confirmed fill when the FillReconciler has
            # resolved it, so fill accounting is real rather than zero.
            resolved_fill = _resolved_fill(record, fills)
            filled_quantity += resolved_fill
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
            elif status == "SHADOWED":
                # Counted, never valued. A shadow order proves the path ran; it
                # proves no money moved. It must not reach the realized-PnL figure,
                # and it must not be silently dropped either - the count is how
                # anyone can see the stage is actually being exercised.
                shadowed += 1
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
                bucket.add(record, notional, resolved_fill=resolved_fill)

        market_closed_cycles = len(records) - market_open_cycles
        first_equity = records[0].snapshot.account.equity if records else None
        last_equity = records[-1].snapshot.account.equity if records else None
        observed_delta = None
        if first_equity is not None and last_equity is not None:
            observed_delta = last_equity - first_equity

        pnl = _pnl_evidence(records, evidence, seeded_fills or (), owner_fills=owner_fills or ())
        for strategy_id, realized_pnl in pnl.strategy_realized_pnl.items():
            # Not `if strategy_id in buckets`. A strategy can close a lot without
            # appearing in the current cycle window, and the old guard dropped
            # broker-verified PnL on the floor in exactly that case - the number was
            # computed, reported in pnl.strategy_realized_pnl, and then discarded
            # before the lifecycle layer could see it, with no log line.
            bucket = buckets.setdefault(strategy_id, _StrategyBucket(strategy_id=strategy_id))
            bucket.realized_pnl = realized_pnl
            bucket.fees = pnl.strategy_fees.get(strategy_id, 0.0)
            bucket.pnl_evidence = pnl.status

        return EvaluationReport(
            generated_at=datetime.now(tz=timezone.utc),
            window_cycles=len(records),
            submitted_orders=submitted,
            rejected_orders=rejected,
            skipped_orders=skipped,
            shadowed_orders=shadowed,
            execution_errors=execution_errors,
            error_count=error_count,
            guardian_approved=guardian_approved,
            guardian_rejected=guardian_rejected,
            guardian_rejections=guardian_rejections,
            action_counts={key: value for key, value in action_counts.items() if value},
            hold_reasons={key: value for key, value in hold_reasons.items() if value},
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
            strategy_metrics={
                strategy_id: bucket.to_evaluation(
                    (cumulative_cycles or {}).get(strategy_id, 0),
                    (cumulative_trade_attempts or {}).get(strategy_id, 0),
                )
                for strategy_id, bucket in buckets.items()
            },
        )


class _StrategyBucket:
    def __init__(self, *, strategy_id: str):
        self.strategy_id = strategy_id
        self.cycles = 0
        self.submitted_orders = 0
        self.rejected_orders = 0
        self.skipped_orders = 0
        self.errors = 0
        self.trade_attempts = 0
        self.strategy_fault_rejections = 0
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

    def add(self, record: CycleRecord, notional: float, *, resolved_fill: float | None = None) -> None:
        self.cycles += 1
        self.action_counts[record.decision.action] = self.action_counts.get(record.decision.action, 0) + 1
        self.intended_notional += notional
        self.filled_quantity += (
            resolved_fill if resolved_fill is not None else record.execution.filled_quantity
        )
        if record.decision.quantity > 0:
            self.order_quantity += record.decision.quantity

        # An attempt is any cycle in which the strategy wanted to trade while
        # the market was open, whether or not the order survived Guardian.
        if record.decision.action in ("BUY", "SELL") and record.snapshot.market_open:
            self.trade_attempts += 1

        if record.execution.status == "SHADOWED":
            return
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
            if not is_system_rejection(record.guardian.reason):
                self.strategy_fault_rejections += 1

    def to_evaluation(
        self, cumulative_cycles: int = 0, cumulative_trade_attempts: int = 0
    ) -> StrategyEvaluation:
        return StrategyEvaluation(
            strategy_id=self.strategy_id,
            cycles=self.cycles,
            cumulative_cycles=cumulative_cycles,
            cumulative_trade_attempts=cumulative_trade_attempts,
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
            trade_attempts=self.trade_attempts,
            strategy_fault_rejections=self.strategy_fault_rejections,
            score=_score(
                self.cycles,
                self.errors,
                self.rejected_orders,
                strategy_fault_rejections=self.strategy_fault_rejections,
                trade_attempts=self.trade_attempts,
                realized_pnl=self.realized_pnl,
            ),
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
    strategy_id: str = ""


@dataclass
class _LotLedger:
    """Lots are account-level, keyed by symbol, and attributed to the strategy that
    opened them.

    The ledger used to be per-strategy, which made a closed lot structurally
    unreachable: the selector returns one strategy per cycle, so the strategy that
    opened a lot and the strategy that closes it are different strategies, and a
    SELL could only ever match lots in its own tracker. Every one of the four SELL
    decisions this agent has ever made produced no closed lot, so
    `strategy_realized_pnl` was empty and could never be otherwise.
    """

    lots: dict[str, list[_Lot]] = field(default_factory=dict)
    #: Open short lots, keyed by symbol. Opened only by a SELL fill whose order was a SHORT
    #: decision; covered FIFO by later BUY fills.
    short_lots: dict[str, list[_Lot]] = field(default_factory=dict)
    #: The account owner's lots, from fills no agent order made: [quantity, price] FIFO.
    owner_lots: dict[str, list[list[float]]] = field(default_factory=dict)
    #: What agent sales beyond the agent's own lots took out of the owner's lots.
    owner_exit_quantity: float = 0.0
    owner_exit_cost: float = 0.0
    owner_exit_proceeds: float = 0.0
    per_strategy: dict[str, _StrategyPnl] = field(default_factory=dict)

    def tracker(self, strategy_id: str) -> _StrategyPnl:
        return self.per_strategy.setdefault(strategy_id, _StrategyPnl())


@dataclass
class _StrategyPnl:
    lots: dict[str, list[_Lot]] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees: float = 0.0
    closed_quantity: float = 0.0
    closed_lots: list[ClosedLotAttribution] = field(default_factory=list)
    unmatched_sell_quantity: float = 0.0


#: Charged on both sides of a closed lot, as a percent of notional, when no explicit
#: value is supplied. The paper broker reported 0.00 commission on all 24 fills, so
#: without an assumption the live PnL is gross and the objective's *after-cost*
#: criterion is not being measured in the live path at all. This is an assumption,
#: not an observation, and it is reported separately from the observed fees.
DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT = 0.05


def _pnl_evidence(
    records: list[CycleRecord],
    evidence: BrokerEvidenceBatch | None,
    seeded_fills: Sequence[BrokerFillActivity] = (),
    assumed_round_trip_cost_pct: float = DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT,
    owner_fills: Sequence[BrokerFillActivity] = (),
) -> PnLEvidence:
    """Attribute broker fills to strategies and match closed lots FIFO.

    `seeded_fills` are earlier broker-confirmed fills taken from the journal, and
    they exist because the broker evidence batch only covers a 24h window. The
    agent's open lots were opened weeks earlier, so a SELL inside the window could
    not match anything and closed_lot_count stayed at 0 forever. Every seeded fill
    carries a price the broker reported, so cost basis stays broker-sourced; the
    evidence *status* is still gated on the fresh batch, because only that can
    verify the exit.
    """
    if evidence is None:
        return PnLEvidence(status=PNL_EVIDENCE_MISSING, missing_reasons=(PNL_EVIDENCE_MISSING,))

    order_to_strategy = _order_strategy_map(records)
    # Orders that were SHORT decisions. Only these may open a short lot: a SELL beyond the
    # agent's longs that was *not* a short is the account owner's shares, and stays unmatched.
    short_orders = {
        record.execution.client_order_id
        for record in records
        if record.decision.action == "SHORT" and record.execution.client_order_id
    }
    ledger = _LotLedger()
    missing_reasons = list(evidence.missing_reasons)
    fill_candidates, order_derived_fill_count = _fill_candidates(evidence)
    fill_attributions: list[FillAttribution] = []
    linked_buy_count = 0
    linked_sell_count = 0
    unlinked_fill_count = 0

    # One ordered stream: journal-confirmed earlier fills first, then the fresh
    # batch, de-duplicated so a fill present in both is applied once.
    #
    # De-duplication cannot key on activity_id. The journal records one entry per
    # order, keyed by its own client_order_id, while the broker reports an order
    # that filled in pieces as several activities under its own ids - a real 52
    # share SELL appears as a single journal entry and as two broker activities of
    # 16 and 36. Keying on activity_id matched none of them, the SELL was applied
    # twice, and the ledger reported 81 unmatched shares against 23 real lots.
    #
    # The order is the common identity: quantity is summed per order and the
    # broker's own prices win, so a split fill is one economic event. When the
    # broker is silent the journal's price stands.
    # For each order the broker covers, the broker's own activities are the
    # truth and the journal's one-line summary is discarded - they are two views of
    # the same fill, not two fills. Summing them instead would double the
    # quantity. Orders the broker does not cover keep their journal entry, which
    # is the whole point of seeding from the journal.
    broker_by_order: dict[str, list[BrokerFillActivity]] = {}
    for activity in fill_candidates:
        broker_by_order.setdefault(_order_key(activity), []).append(activity)

    stream: dict[str, BrokerFillActivity] = {}
    for activity in seeded_fills:
        stream.setdefault(_order_key(activity), activity)
    for key, activities in broker_by_order.items():
        quantity = sum(item.quantity for item in activities)
        # A split fill can straddle two prices, so the volume-weighted average is
        # the only honest single price for the order.
        price = sum(item.price * item.quantity for item in activities) / quantity
        first = activities[0]
        stream[key] = first.model_copy(
            update={
                "quantity": quantity,
                "price": price,
                "transaction_time": min(item.transaction_time for item in activities),
                "source": _join_sources(item.source for item in activities),
            }
        )

    # The owner's fills are interleaved in time, so an agent sale sees the owner's book as it
    # stood at that moment - not as it stands today.
    owner_queue = sorted(owner_fills, key=lambda item: item.transaction_time)
    for activity in sorted(stream.values(), key=lambda item: item.transaction_time):
        while owner_queue and owner_queue[0].transaction_time <= activity.transaction_time:
            _apply_owner_fill(ledger, owner_queue.pop(0))
        strategy_id = _activity_strategy(activity, order_to_strategy)
        status: AttributionStatus = "LINKED" if strategy_id else "UNLINKED"
        in_window = any(_order_key(activity) == _order_key(item) for item in fill_candidates)
        fill_attributions.append(
            _fill_attribution(
                activity,
                strategy_id=strategy_id,
                status=status,
                seeded=not in_window,
            )
        )
        if strategy_id is None:
            unlinked_fill_count += 1
            continue
        if activity.side == "BUY":
            linked_buy_count += 1
        else:
            linked_sell_count += 1
        _apply_activity(
            ledger, activity, strategy_id=strategy_id,
            opens_short=activity.client_order_id in short_orders,
        )

    strategy_pnl = ledger.per_strategy
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
    unmatched_sell = {
        strategy_id: round(tracker.unmatched_sell_quantity, 10)
        for strategy_id, tracker in strategy_pnl.items()
        if tracker.unmatched_sell_quantity > 1e-9
    }
    open_lot_quantity = _open_lot_quantity(ledger)
    # Marked from the latest price the loop actually observed, per symbol.
    last_prices: dict[str, float] = {}
    last_seen: datetime | None = None
    for record in records:
        snap = getattr(record, "snapshot", None)
        if snap is None or snap.last_price <= 0:
            continue
        last_prices[snap.symbol] = snap.last_price
        if last_seen is None or snap.timestamp > last_seen:
            last_seen = snap.timestamp
    open_lots = _open_lots(ledger, last_prices, last_seen)
    # Only a fully-priced book has a meaningful unrealized figure; one lot with an
    # unknown price makes the total unknowable, and reporting 0.0 would read as
    # "flat" rather than "unknown".
    priced = [p for p in (lot.unrealized_pnl for lot in open_lots) if isinstance(p, float)]
    unrealized = sum(priced) if len(priced) == len(open_lots) else (0.0 if not open_lots else None)
    realized_total = sum(strategy_realized.values()) if strategy_realized else 0.0
    fees_total = sum(strategy_fees.values()) if strategy_fees else 0.0
    # Charged on the notional actually traded, both sides, per owning strategy. On
    # paper the observed fees are 0.00, so this is the only cost that appears in the
    # live ledger at all - and omitting it is what made the headline figure gross.
    assumed_cost = _assumed_cost_for_lots(closed_lots, assumed_round_trip_cost_pct)
    assumed_total = sum(assumed_cost.values())
    net = None if unrealized is None else round(realized_total + unrealized, 6)
    net_of_fees = None if net is None else round(net - fees_total, 6)

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
            missing_reasons.append("linked SELL fills exist but no prior linked BUY lot was available to close")
        elif linked_buy_count or linked_sell_count:
            missing_reasons.append("no linked closed lots were produced by the linked fills")
        elif unlinked_fill_count:
            missing_reasons.append("broker fills were excluded from strategy PnL because they were unlinked")

    account_pnl = None
    account_return_pct = None
    if evidence.portfolio_history:
        last_point = sorted(evidence.portfolio_history, key=lambda item: item.timestamp)[-1]
        account_pnl = last_point.profit_loss
        account_return_pct = last_point.profit_loss_pct

    # The holding benchmark, from the prices this evaluation already holds rather than
    # a second source that could disagree with the lots.
    #
    # Same capital and same calendar window on both legs. The strategy leg is realized plus
    # unrealized PnL over its peak exposure; the market leg is the same instrument from the
    # strategy's first entry to its last exit, or to the last quote while a lot is open.
    # The market leg used to run over the whole journal, so a strategy whose first trade was
    # on 2026-09-28 was compared with SPY from 2026-06-09. The whole-record figure is kept
    # as `market_return_pct` for context only.
    peak_exposure = _peak_exposure_by_strategy(closed_lots, open_lots)
    unrealized_by_strategy: dict[str, float] = {}
    for open_lot in open_lots:
        if open_lot.unrealized_pnl is not None:
            unrealized_by_strategy[open_lot.strategy_id] = (
                unrealized_by_strategy.get(open_lot.strategy_id, 0.0) + open_lot.unrealized_pnl
            )
    strategy_return = {
        strategy_id: round(
            (strategy_realized.get(strategy_id, 0.0) + unrealized_by_strategy.get(strategy_id, 0.0))
            / exposure * 100.0,
            6,
        )
        for strategy_id, exposure in peak_exposure.items()
        if exposure > 0 and (strategy_id in strategy_realized or strategy_id in unrealized_by_strategy)
    }
    market_return_pct = _market_return_pct(records)
    strategy_market_return: dict[str, float] = {}
    for strategy_id, (symbol, start, end) in _strategy_windows(closed_lots, open_lots).items():
        leg = _market_return_over(records, symbol, start, end)
        if leg is not None:
            strategy_market_return[strategy_id] = leg
    if strategy_return and not strategy_market_return:
        missing_reasons.append(
            "no price series covers any strategy's holding window, so no holding benchmark can be stated"
        )
    excess_vs_market = {
        strategy_id: round(value - strategy_market_return[strategy_id], 6)
        for strategy_id, value in strategy_return.items()
        if strategy_id in strategy_market_return
    }

    # Named `evidence_status`, not `status`: this function also builds fill
    # attributions, whose `status` is a LINKED/UNLINKED flag. Two unrelated
    # meanings under one name in one 150-line function is how the wrong one gets
    # passed to the wrong constructor.
    evidence_status = PNL_EVIDENCE_MISSING
    if strategy_realized:
        evidence_status = PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
    elif evidence.portfolio_history:
        evidence_status = PNL_EVIDENCE_ACCOUNT_VERIFIED
    elif evidence.activities or evidence.orders:
        evidence_status = PNL_EVIDENCE_PARTIAL_FILLS
    else:
        missing_reasons.append("broker evidence contained no fills, orders, or portfolio history")

    return PnLEvidence(
        status=evidence_status,
        account_realized_or_reported_pnl=account_pnl,
        account_return_pct=account_return_pct,
        strategy_realized_pnl=strategy_realized,
        strategy_fees=strategy_fees,
        strategy_peak_exposure=peak_exposure,
        strategy_return_pct=strategy_return,
        market_return_pct=market_return_pct,
        strategy_market_return_pct=strategy_market_return,
        strategy_excess_vs_market_pct=excess_vs_market,
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
        unmatched_sell_quantity=unmatched_sell,
        owner_exit_quantity=round(ledger.owner_exit_quantity, 10),
        owner_exit_cost=round(ledger.owner_exit_cost, 6),
        owner_exit_proceeds=round(ledger.owner_exit_proceeds, 6),
        open_lots=open_lots,
        unrealized_pnl=None if unrealized is None else round(unrealized, 6),
        net_pnl=net,
        net_of_fees=net_of_fees,
        assumed_cost_pct=assumed_round_trip_cost_pct,
        assumed_cost=assumed_cost,
        net_after_assumed_cost=(
            None if net is None else round(net - fees_total - assumed_total, 6)
        ),
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


def _join_sources(sources) -> str:
    """Combine several fill sources into one honest label, order-stable."""
    seen: list[str] = []
    for source in sources:
        for part in str(source).split("+"):
            if part not in seen:
                seen.append(part)
    return "+".join(seen)


def _order_key(activity: BrokerFillActivity) -> str:
    """The identity of the *order* behind a fill, across broker and journal.

    An order that filled in pieces is one economic event, so lot matching and
    de-duplication both have to work at order granularity rather than per activity.
    """
    return activity.order_id or activity.client_order_id or activity.activity_id


def _activity_strategy(activity: BrokerFillActivity, order_to_strategy: dict[str, str]) -> str | None:
    if activity.order_id and activity.order_id in order_to_strategy:
        return order_to_strategy[activity.order_id]
    if activity.client_order_id and activity.client_order_id in order_to_strategy:
        return order_to_strategy[activity.client_order_id]
    # Journal-confirmed fills know their own strategy, which matters for lots
    # opened long before the cycle records in the current window.
    return activity.strategy_id


def confirmed_fill_activities(journal, records: Sequence[CycleRecord] | None = None) -> list[BrokerFillActivity]:
    """Rebuild broker-confirmed fills from the journal, with no time limit.

    The broker evidence batch only covers a short window, so lots opened weeks
    earlier were invisible to lot matching and no SELL could ever close one. Every
    field here came from the broker: price and quantity from ORDER_FILL_CONFIRMED,
    which is written only after the order is re-polled to a terminal fill state.

    Timing is the one part that cannot come from the broker retroactively. The
    fill event is written when the reconciler re-polls, which for the historical
    orders was long after the trade. So the timestamp is the contemporaneous cycle
    time (DataSnapshot.timestamp, the market-data time of the decision that placed
    the order) where the cycle record still exists, and the two sources are
    labelled differently so a reader can tell which is which.
    """
    from min_agent.fill_reconciler import FILL_EVENT

    anchor: dict[str, datetime] = {}
    for record in records or ():
        coid = record.execution.client_order_id if record.execution else None
        if coid:
            anchor.setdefault(coid, record.snapshot.timestamp)

    activities: list[BrokerFillActivity] = []
    for event in journal.read_events(FILL_EVENT):
        payload = event.payload
        coid = payload.get("client_order_id")
        price = payload.get("filled_avg_price")
        quantity = payload.get("filled_quantity")
        symbol = payload.get("symbol")
        side = payload.get("side")
        if not (
            isinstance(coid, str)
            and isinstance(price, (int, float))
            and price > 0
            and isinstance(quantity, (int, float))
            and quantity > 0
            and isinstance(symbol, str)
            and isinstance(side, str)
        ):
            continue
        strategy_id = payload.get("strategy_id")
        try:
            activities.append(
                BrokerFillActivity(
                    activity_id=coid,
                    order_id=payload.get("order_id") if isinstance(payload.get("order_id"), str) else None,
                    client_order_id=coid,
                    symbol=symbol,
                    # The journal keeps the decision's intent (SHORT, COVER); the broker side
                    # is what the fill was, and the ledger reads intent from the order id.
                    side=cast("Side", BROKER_SIDE.get(side.strip().upper(), side.strip().upper()).upper()),
                    quantity=float(quantity),
                    price=float(price),
                    transaction_time=anchor.get(coid, event.timestamp),
                    source=(
                        "alpaca_journal_confirmed_fill"
                        if coid in anchor
                        else "alpaca_journal_confirmed_fill_time_unknown"
                    ),
                    strategy_id=strategy_id if isinstance(strategy_id, str) and strategy_id else None,
                )
            )
        except Exception:
            continue
    return activities


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


def _fill_attribution(
    activity: BrokerFillActivity,
    *,
    strategy_id: str | None,
    status: AttributionStatus,
    seeded: bool = False,
) -> FillAttribution:
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
        seeded=seeded,
    )


def _open_lots(
    ledger: _LotLedger,
    prices: Mapping[str, float],
    as_of: datetime | None,
) -> tuple[OpenLotAttribution, ...]:
    """Open agent lots, marked to the last price the system actually saw.

    Only realized PnL was attributed before this, so a position the agent was
    holding contributed nothing to its own record. That made the account figure and
    the agent figure impossible to reconcile even in principle: one measured closed
    trades, the other measured everything.

    A lot whose symbol has no observed price is reported with `current_price=None`
    and `unrealized_pnl=None` rather than valued at its entry price. Marking an open
    position at its own cost reports it as worth exactly nothing, which is a
    fabricated number wearing the appearance of a measurement.
    """
    out: list[OpenLotAttribution] = []
    for symbol, short_lots in ledger.short_lots.items():
        price = prices.get(symbol)
        for lot in short_lots:
            if lot.quantity <= 1e-9:
                continue
            gain = None if price is None or not lot.price else (lot.price - price) * lot.quantity
            out.append(
                OpenLotAttribution(
                    direction="SHORT",
                    strategy_id=lot.strategy_id or "unattributed", symbol=symbol,
                    quantity=lot.quantity, entry_price=lot.price, current_price=price,
                    unrealized_pnl=None if gain is None else round(gain, 6),
                    unrealized_pnl_pct=(
                        None if price is None or not lot.price
                        else round((lot.price - price) / lot.price, 6)
                    ),
                    opened_at=lot.opened_at, as_of=as_of,
                    buy_order_id=lot.order_id, buy_fill_id=lot.fill_id,
                )
            )
    for symbol, lots in ledger.lots.items():
        price = prices.get(symbol)
        for lot in lots:
            if lot.quantity <= 1e-9:
                continue
            unrealized = None
            pct = None
            if price is not None and lot.price:
                unrealized = (price - lot.price) * lot.quantity
                pct = (price - lot.price) / lot.price
            out.append(
                OpenLotAttribution(
                    strategy_id=lot.strategy_id or "unattributed",
                    symbol=symbol,
                    quantity=lot.quantity,
                    entry_price=lot.price,
                    current_price=price,
                    unrealized_pnl=None if unrealized is None else round(unrealized, 6),
                    unrealized_pnl_pct=None if pct is None else round(pct, 6),
                    opened_at=lot.opened_at,
                    as_of=as_of,
                    buy_order_id=lot.order_id,
                    buy_fill_id=lot.fill_id,
                    buy_client_order_id=lot.client_order_id,
                )
            )
    return tuple(out)


def _strategy_windows(
    closed_lots: Sequence[ClosedLotAttribution],
    open_lots: Sequence[OpenLotAttribution],
) -> dict[str, tuple[str, datetime, datetime]]:
    """Per strategy: its symbol, its first entry, and its last exit - or, while a lot is still
    open, the time that lot was last valued."""
    windows: dict[str, tuple[str, datetime, datetime]] = {}

    def widen(strategy_id: str, symbol: str, start: datetime, end: datetime | None) -> None:
        end = end or start
        if strategy_id in windows:
            _, lo, hi = windows[strategy_id]
            start, end = min(lo, start), max(hi, end)
        windows[strategy_id] = (symbol, start, end)

    for lot in closed_lots:
        widen(lot.strategy_id, lot.symbol, lot.opened_at, lot.closed_at)
    for open_lot in open_lots:
        widen(open_lot.strategy_id, open_lot.symbol, open_lot.opened_at, open_lot.as_of)
    return windows


def _market_return_over(
    records: Sequence[CycleRecord], symbol: str, start: datetime, end: datetime
) -> float | None:
    """The instrument's return from its first quote at or after `start` to its last at or
    before `end`, from the same journal snapshots the lots are valued with. None when fewer
    than two quotes fall in the window: one quote is not a holding period."""
    prices = sorted(
        (record.snapshot.timestamp, record.snapshot.last_price)
        for record in records
        if getattr(record, "snapshot", None) is not None
        and getattr(record.snapshot, "symbol", symbol) == symbol
        and record.snapshot.last_price > 0
        and start <= record.snapshot.timestamp <= end
    )
    if len(prices) < 2 or prices[0][0] == prices[-1][0]:
        return None
    return round((prices[-1][1] / prices[0][1] - 1.0) * 100.0, 6)


def _market_return_pct(records: list[CycleRecord]) -> float | None:
    """What the traded instrument returned over the window these records cover.

    The first and last real quotes the loop observed, and nothing else. This exists so
    the benchmark and the lots rest on one price source: a comparison assembled from two
    can silently disagree, and a benchmark that disagrees with the PnL it is judging is
    worse than no benchmark. Returns None when no price series covers the window, which
    is reported rather than papered over.
    """
    prices = [
        (record.snapshot.timestamp, record.snapshot.last_price)
        for record in records
        if getattr(record, "snapshot", None) is not None and record.snapshot.last_price > 0
    ]
    if len(prices) < 2:
        return None
    prices.sort(key=lambda item: item[0])
    first, last = prices[0][1], prices[-1][1]
    if first <= 0:
        return None
    return round((last / first - 1.0) * 100.0, 6)


def _assumed_cost_for_lots(
    closed_lots: Sequence[ClosedLotAttribution],
    pct: float,
) -> dict[str, float]:
    """Configured cost charged on the notional actually traded, both sides.

    Separate from the broker's observed fees, which are 0.00 on paper. On a live venue
    both would apply, and reporting one number for "net" would hide which of them did
    the work.
    """
    out: dict[str, float] = {}
    for lot in closed_lots:
        notional = (float(lot.buy_price) + float(lot.sell_price)) * float(lot.quantity)
        key = lot.strategy_id or "unattributed"
        out[key] = round(out.get(key, 0.0) + notional * pct / 200.0, 6)
    return out


def _open_lot_quantity(ledger: _LotLedger) -> dict[str, float]:
    """Open quantity per owning strategy and symbol.

    Lots live in the account-level ledger, but each one still belongs to the
    strategy that opened it, and an open lot is what a future SELL will close -
    so this is the map that says who is expected to realize PnL next.
    """
    quantities: dict[str, float] = {}
    for symbol, lots in ledger.lots.items():
        for lot in lots:
            if lot.quantity <= 1e-9:
                continue
            key = f"{lot.strategy_id or 'unattributed'}:{symbol}"
            quantities[key] = round(quantities.get(key, 0.0) + lot.quantity, 10)
    return quantities


def _apply_activity(
    ledger: _LotLedger, activity: BrokerFillActivity, *, strategy_id: str, opens_short: bool = False
) -> None:
    """Apply one broker fill to the account-level lot ledger.

    A BUY opens a lot attributed to `strategy_id`. A SELL matches FIFO against
    whatever lots exist for the symbol and attributes each matched portion to the
    strategy that opened that lot, recording the closing strategy separately. That
    is the standard convention and it is the only one under which a lot opened by
    one strategy can be closed by another.
    """
    if activity.side == "BUY":
        # A buy covers the agent's open shorts first; only the remainder opens a long. Shorts
        # only exist when a SHORT decision opened them, so on a long-only record this is the
        # old path exactly.
        remaining_buy = _cover_shorts(ledger, activity, strategy_id=strategy_id)
        if remaining_buy <= 1e-9:
            return
        if remaining_buy < activity.quantity:
            activity = activity.model_copy(update={
                "quantity": remaining_buy,
                "fees": activity.fees * remaining_buy / activity.quantity,
            })
        ledger.tracker(strategy_id).fees += activity.fees
        ledger.lots.setdefault(activity.symbol, []).append(
            _Lot(
                quantity=activity.quantity,
                price=activity.price,
                fees=activity.fees,
                fill_id=activity.activity_id,
                order_id=activity.order_id,
                client_order_id=activity.client_order_id,
                opened_at=activity.transaction_time,
                source=activity.source,
                strategy_id=strategy_id,
            )
        )
        return

    remaining = activity.quantity
    lots = ledger.lots.setdefault(activity.symbol, [])
    sell_fee_remaining = activity.fees
    closing = ledger.tracker(strategy_id)
    while remaining > 1e-9 and lots:
        lot = lots[0]
        owner = lot.strategy_id or strategy_id
        matched = min(remaining, lot.quantity)
        buy_fee = lot.fees * (matched / lot.quantity) if lot.quantity else 0.0
        sell_fee = sell_fee_remaining * (matched / remaining) if remaining else 0.0
        realized_pnl = (activity.price - lot.price) * matched - buy_fee - sell_fee
        tracker = ledger.tracker(owner)
        tracker.realized_pnl += realized_pnl
        tracker.closed_quantity += matched
        tracker.closed_lots.append(
            ClosedLotAttribution(
                strategy_id=owner,
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
                closing_strategy_id=strategy_id if strategy_id != owner else None,
            )
        )
        lot.quantity -= matched
        lot.fees -= buy_fee
        remaining -= matched
        sell_fee_remaining -= sell_fee
        if lot.quantity <= 1e-9:
            lots.pop(0)

    # What a SHORT decision sold beyond the agent's longs is a short, opened at this price.
    if opens_short and remaining > 1e-9:
        closing.fees += sell_fee_remaining
        ledger.short_lots.setdefault(activity.symbol, []).append(
            _Lot(
                quantity=remaining, price=activity.price, fees=sell_fee_remaining,
                fill_id=activity.activity_id, order_id=activity.order_id,
                client_order_id=activity.client_order_id, opened_at=activity.transaction_time,
                source=activity.source, strategy_id=strategy_id,
            )
        )
        return

    # Not a short: the order was not a SHORT decision, so these are shares the agent never
    # owned - the 29-share case. Where the owner's recorded fills cover them, price them
    # against the owner's lots and report them as the owner's exit; never as strategy PnL.
    owner_book = ledger.owner_lots.get(activity.symbol, [])
    while remaining > 1e-9 and owner_book:
        matched = min(remaining, owner_book[0][0])
        ledger.owner_exit_quantity += matched
        ledger.owner_exit_cost += matched * owner_book[0][1]
        ledger.owner_exit_proceeds += matched * activity.price
        owner_book[0][0] -= matched
        remaining -= matched
        if owner_book[0][0] <= 1e-9:
            owner_book.pop(0)

    # A SELL that matched nothing still cost money and is still a fact. Charge the
    # remainder to the closing strategy so it is visible rather than dropped, which
    # is what the old code did with `unmatched_sell_quantity`.
    closing.unmatched_sell_quantity += remaining
    closing.fees += sell_fee_remaining


def _apply_owner_fill(ledger: _LotLedger, activity: BrokerFillActivity) -> None:
    """Keep the account owner's lot book: a buy adds a lot, a sell reduces FIFO."""
    lots = ledger.owner_lots.setdefault(activity.symbol, [])
    if activity.side == "BUY":
        lots.append([activity.quantity, activity.price])
        return
    remaining = activity.quantity
    while remaining > 1e-9 and lots:
        matched = min(remaining, lots[0][0])
        lots[0][0] -= matched
        remaining -= matched
        if lots[0][0] <= 1e-9:
            lots.pop(0)


def _cover_shorts(ledger: _LotLedger, activity: BrokerFillActivity, *, strategy_id: str) -> float:
    """Close open short lots FIFO with a buy; return the quantity left over."""
    remaining = activity.quantity
    lots = ledger.short_lots.get(activity.symbol, [])
    fee_remaining = activity.fees
    while remaining > 1e-9 and lots:
        lot = lots[0]
        owner = lot.strategy_id or strategy_id
        matched = min(remaining, lot.quantity)
        open_fee = lot.fees * (matched / lot.quantity) if lot.quantity else 0.0
        close_fee = fee_remaining * (matched / remaining) if remaining else 0.0
        realized_pnl = (lot.price - activity.price) * matched - open_fee - close_fee
        tracker = ledger.tracker(owner)
        tracker.realized_pnl += realized_pnl
        tracker.closed_quantity += matched
        tracker.closed_lots.append(
            ClosedLotAttribution(
                direction="SHORT",
                strategy_id=owner, symbol=activity.symbol,
                buy_fill_id=activity.activity_id, sell_fill_id=lot.fill_id,
                buy_order_id=activity.order_id, sell_order_id=lot.order_id,
                buy_client_order_id=activity.client_order_id,
                sell_client_order_id=lot.client_order_id,
                quantity=matched, buy_price=activity.price, sell_price=lot.price,
                buy_fees=close_fee, sell_fees=open_fee, realized_pnl=realized_pnl,
                opened_at=lot.opened_at, closed_at=activity.transaction_time,
                source=_combined_source(lot.source, activity.source),
                closing_strategy_id=strategy_id if strategy_id != owner else None,
            )
        )
        lot.quantity -= matched
        lot.fees -= open_fee
        remaining -= matched
        fee_remaining -= close_fee
        if lot.quantity <= 1e-9:
            lots.pop(0)
    return remaining


def _combined_source(open_source: str, close_source: str) -> str:
    if open_source == close_source:
        return open_source
    return f"{open_source}+{close_source}"


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _resolved_fill(record: CycleRecord, fills: Mapping[str, float] | None) -> float:
    if not fills:
        return record.execution.filled_quantity
    coid = record.execution.client_order_id
    if not coid or coid not in fills:
        return record.execution.filled_quantity
    return max(record.execution.filled_quantity, float(fills[coid]))


def _score(
    cycles: int,
    errors: int,
    rejected_orders: int,
    *,
    strategy_fault_rejections: int | None = None,
    trade_attempts: int = 0,
    realized_pnl: float | None = None,
) -> float:
    """Score a strategy as reliability gated by exploration.

    The previous version was `1 - (errors + rejections) / cycles`, so a strategy
    that never acted and was never corrected scored a perfect 1.0 while a
    strategy that placed five real orders scored 0.833. StrategySelector takes a
    deterministic argmax over this value, so the system selected the do-nothing
    strategy every cycle, it kept its 1.0, and it was selected again. In the
    recorded run that produced 800 HOLD cycles out of 851.

    The fix is structural rather than a special case: a strategy earns at most
    25% of its reliability score for pure inaction, so inaction can never be
    the argmax while any candidate actually trades. Broker-verified PnL can then
    only adjust a score that was earned by acting - it can no longer substitute
    for acting.
    """
    if cycles <= 0:
        return 0.0
    faults = strategy_fault_rejections if strategy_fault_rejections is not None else rejected_orders
    operational = max(0.0, 1.0 - ((errors + faults) / cycles))
    exploration = min(1.0, max(0, trade_attempts) / cycles)
    score = operational * (INACTION_FLOOR + (1.0 - INACTION_FLOOR) * exploration)
    if realized_pnl is None:
        # Reserve headroom. Without this, a strategy that traded reliably but
        # has no PnL evidence ties with one the broker has verified as
        # profitable, and PnL cannot discriminate at the ceiling.
        return score * (1.0 - PNL_BONUS)
    if realized_pnl > 0:
        return min(1.0, score * (1.0 + PNL_BONUS))
    return max(0.0, score * (1.0 - PNL_PENALTY))



def _peak_exposure_by_strategy(
    closed_lots: Sequence[ClosedLotAttribution],
    open_lots: Sequence[OpenLotAttribution],
) -> dict[str, float]:
    """Peak cost basis each strategy held at risk at any one moment.

    The denominator for "what did the capital I entrusted to this strategy return".
    Deliberately **not** cumulative turnover: a strategy that cycles one share fifteen
    times turns over fifteen times the capital it ever risked, so turnover as a
    denominator understates its return and then compares it against a market return
    computed on capital held once. Measured here, turnover understated the largest
    strategy by 0.5 points and turned a -0.82 excess into a -1.38 one - the same sign
    by luck, which is worse than being wrong in one direction.

    Replayed in timestamp order across closed and still-open lots, because a strategy
    that held one lot at a time and a strategy that held eight at once are different
    exposures and only the replay tells them apart.
    """
    events: dict[str, list[tuple[datetime, float]]] = {}
    for lot in closed_lots:
        # The entry is what was at risk: the buy for a long, the short sale for a short.
        entry = lot.sell_price if lot.direction == "SHORT" else lot.buy_price
        notional = entry * lot.quantity
        bucket = events.setdefault(lot.strategy_id, [])
        bucket.append((lot.opened_at, notional))
        bucket.append((lot.closed_at, -notional))
    for open_lot in open_lots:
        events.setdefault(open_lot.strategy_id, []).append(
            (open_lot.opened_at, open_lot.entry_price * open_lot.quantity)
        )
    peaks: dict[str, float] = {}
    for strategy_id, moves in events.items():
        # Sorted by timestamp; ties do not matter because a peak is a maximum over a
        # sum, and a same-instant open/close cannot raise it above either side.
        moves.sort(key=lambda item: item[0])
        held = 0.0
        peak = 0.0
        for _, delta in moves:
            held += delta
            if held > peak:
                peak = held
        if peak > 0:
            peaks[strategy_id] = round(peak, 6)
    return peaks


#: The journal event `--ingest-owner-history` writes, identified by this payload key.
OWNER_FILLS_KEY = "owner_fills"


def owner_fill_activities(journal) -> list[BrokerFillActivity]:
    """The account owner's fills from the latest owner-history ingest, or none.

    Broker-reported fills of allowlisted symbols that no agent order made. Without an ingest
    this is empty and the 29-share sale stays unmatched, which is what it was before.
    """
    latest = None
    for event in journal.read_events("BROKER_EVIDENCE_INGESTED"):
        if isinstance(event.payload.get(OWNER_FILLS_KEY), list):
            latest = event
    if latest is None:
        return []
    out = []
    for row in latest.payload[OWNER_FILLS_KEY]:
        try:
            out.append(BrokerFillActivity.model_validate(row))
        except ValueError:
            continue
    return out
