from __future__ import annotations

from datetime import datetime, timezone

from min_agent.models import DataSnapshot, GuardianResult, StrategySpec, TradeDecision


class Guardian:
    def __init__(
        self,
        *,
        allowlist: set[str] | frozenset[str],
        max_position_value: float,
        max_daily_loss: float,
        max_trades_per_day: int = 10,
        max_total_exposure: float | None = None,
        # A separate, absolute backstop on the whole account, including positions
        # outside the allowlist. Off by default: the agent cannot increase those,
        # so bounding the allowlist already bounds it. It exists for the case
        # where the account owner wants the total itself fenced, and it must be
        # set deliberately - turning it on is a real risk decision.
        max_account_value: float | None = None,
        min_confidence: float = 0.5,
        max_snapshot_age_seconds: int = 900,
    ):
        self.allowlist = frozenset(symbol.upper() for symbol in allowlist)
        self.max_position_value = max_position_value
        self.max_daily_loss = max_daily_loss
        self.max_trades_per_day = max_trades_per_day
        self.max_total_exposure = max_total_exposure
        self.max_account_value = max_account_value
        self.min_confidence = min_confidence
        self.max_snapshot_age_seconds = max_snapshot_age_seconds

    def review(
        self,
        decision: TradeDecision,
        snapshot: DataSnapshot,
        mode: str = "paper",
        trades_today: int = 0,
        now: datetime | None = None,
        agent_position_quantity: float | None = None,
    ) -> GuardianResult:
        if mode.strip().lower() != "paper":
            return GuardianResult(approved=False, reason="only paper mode is allowed")

        if decision.symbol != snapshot.symbol:
            return GuardianResult(approved=False, reason="decision symbol does not match data snapshot")

        if decision.action != "SELL" and decision.symbol not in self.allowlist:
            return GuardianResult(approved=False, reason="symbol is outside the allowlist")

        if decision.action == "SELL" and decision.symbol not in self.allowlist:
            if not any(pos.symbol == decision.symbol for pos in snapshot.positions):
                return GuardianResult(approved=False, reason="symbol is outside the allowlist")

        if self._is_stale(snapshot, now=now):
            return GuardianResult(approved=False, reason="data snapshot is stale")

        if decision.action == "HOLD":
            return GuardianResult(approved=True, reason="approved")

        # Every remaining check gates an actual order. The daily-loss limit used
        # to sit above the HOLD branch, so a deliberate no-op was journalled as
        # a risk REJECTION rather than a hold - misleading in the audit trail,
        # even though the reason is in SYSTEM_REJECTION_REASONS and so was never
        # charged against the strategy.
        if snapshot.account.daily_loss >= self.max_daily_loss:
            return GuardianResult(approved=False, reason="daily loss limit reached")

        if not snapshot.account.day_start_equity_known:
            return GuardianResult(approved=False, reason="account day-start equity unavailable")

        if not snapshot.market_open:
            return GuardianResult(approved=False, reason="market is closed")

        if trades_today >= self.max_trades_per_day:
            return GuardianResult(approved=False, reason="max trades per day reached")

        if decision.confidence < self.min_confidence:
            return GuardianResult(approved=False, reason="decision confidence below minimum")

        position_value = decision.quantity * snapshot.last_price
        if decision.action == "BUY" and position_value > self.max_position_value:
            return GuardianResult(approved=False, reason="position value exceeds hard limit")

        if decision.action == "BUY" and snapshot.account.buying_power < position_value:
            return GuardianResult(approved=False, reason="insufficient buying power")

        if decision.action == "SELL" and not self._has_position(snapshot, decision.symbol, decision.quantity):
            return GuardianResult(approved=False, reason="cannot sell more than known holdings")

        # A SELL must also be bounded by what *the agent* holds, not only by what the
        # account holds. The account check above is necessary and not sufficient: the
        # agent bought 23 shares, the account held 52, and a single SELL of 52 was
        # approved and filled - liquidating 29 shares of a pre-existing position the
        # agent never bought, which is 56% of the owner's holding.
        #
        # This is an asymmetry in the earlier exposure-scoping work: bounding the
        # allowlist book was justified because "the agent cannot increase" exposure
        # outside it. It plainly can *decrease* it, without limit, by selling the
        # owner's shares.
        #
        # When the agent's own holding is unknown the answer is to refuse, not to
        # allow. Refusing a SELL is recoverable - the next cycle retries, and the
        # position is unchanged - whereas liquidating someone's position is not. The
        # cost of this rule being wrong is a missed exit, which is why the reason
        # states it plainly.
        if decision.action == "SELL":
            if agent_position_quantity is None:
                return GuardianResult(
                    approved=False,
                    reason=(
                        "the agent's own holding of "
                        f"{decision.symbol} is unknown, so a SELL cannot be "
                        "bounded; refusing rather than risk selling the account "
                        "owner's shares"
                    ),
                )
            if decision.quantity > agent_position_quantity:
                return GuardianResult(
                    approved=False,
                    reason=(
                        f"cannot sell {decision.quantity} {decision.symbol}; the "
                        f"agent holds {agent_position_quantity:g}. The account may "
                        "hold more, but those shares are not the agent's to sell"
                    ),
                )

        if self._has_conflicting_open_order(snapshot, decision):
            return GuardianResult(approved=False, reason="conflicting open order exists")

        # The exposure cap bounds the agent's own book, not the account's.
        #
        # It used to sum every position, which counted a $37k bond allocation the
        # agent has no mandate over and cannot change: BIL and TLT are not in the
        # allowlist, so it can neither buy them nor is it permitted to sell them.
        # Against a $20k cap that left the agent zero usable exposure, and a limit
        # that forbids every trade is not risk management - it is a brick. The
        # agent can only *increase* exposure by buying allowlist symbols, so
        # bounding allowlist exposure bounds everything the agent is able to do.
        #
        # The cap value is unchanged, and both numbers are reported so the
        # accounting stays auditable rather than merely convenient.
        mandate_value = sum(
            pos.market_value for pos in snapshot.positions if pos.symbol in self.allowlist
        )
        account_value = sum(pos.market_value for pos in snapshot.positions)
        if self.max_total_exposure is not None and decision.action == "BUY":
            if mandate_value + position_value > self.max_total_exposure:
                return GuardianResult(
                    approved=False,
                    reason=(
                        f"agent exposure {mandate_value:.2f} + {position_value:.2f} "
                        f"exceeds the {self.max_total_exposure:.2f} limit "
                        f"(allowlist symbols only; account total is {account_value:.2f})"
                    ),
                )
        if self.max_account_value is not None and decision.action == "BUY":
            if account_value + position_value > self.max_account_value:
                return GuardianResult(
                    approved=False,
                    reason=(
                        f"account total {account_value:.2f} + {position_value:.2f} "
                        f"exceeds the {self.max_account_value:.2f} account limit"
                    ),
                )

        return GuardianResult(approved=True, reason="approved")

    def review_strategy(self, strategy: StrategySpec) -> GuardianResult:
        if not set(strategy.symbols).issubset(self.allowlist):
            return GuardianResult(approved=False, reason="strategy contains symbols outside the allowlist")
        if strategy.max_position_value > self.max_position_value:
            return GuardianResult(approved=False, reason="strategy position value exceeds hard limit")
        # A FIXED_SIZE strategy submits its confidence verbatim, and the decision
        # path rejects anything under min_confidence. Admission accepted confidence
        # anywhere in [0, 1], so a strategy could be admitted that was incapable of
        # ever placing an order. One SELL was rejected 100% of the time on exactly
        # this and nothing in the system said why. Reject it at the door instead.
        confidence = strategy.parameters.get("confidence")
        if isinstance(confidence, (int, float)) and float(confidence) < self.min_confidence:
            return GuardianResult(
                approved=False,
                reason=(
                    f"strategy confidence {float(confidence):.2f} is below the "
                    f"guardian minimum {self.min_confidence:.2f}, so it could never execute"
                ),
            )
        return GuardianResult(approved=True, reason="approved")

    def _is_stale(self, snapshot: DataSnapshot, *, now: datetime | None) -> bool:
        current = now or datetime.now(timezone.utc)
        timestamp = snapshot.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return (current - timestamp).total_seconds() > self.max_snapshot_age_seconds

    def _has_position(self, snapshot: DataSnapshot, symbol: str, quantity: int) -> bool:
        positions = [pos for pos in snapshot.positions if pos.symbol == symbol]
        if not positions:
            return False
        return sum(pos.quantity for pos in positions) >= quantity

    def _has_conflicting_open_order(self, snapshot: DataSnapshot, decision: TradeDecision) -> bool:
        return any(order.symbol == decision.symbol and order.side == decision.action for order in snapshot.open_orders)
