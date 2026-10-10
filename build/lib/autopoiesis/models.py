from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autopoiesis import coerce

Action = Literal["BUY", "SELL", "HOLD", "SHORT", "COVER"]
#: Actions that place an order. SELL reduces a long; SHORT opens or adds to a short; COVER
#: buys a short back. See docs/history/plans/2026-10-07-bounded-short-and-rule-dsl.md.
ORDER_ACTIONS = frozenset({"BUY", "SELL", "SHORT", "COVER"})
#: The broker side each order action is submitted as.
BROKER_SIDE = {"BUY": "buy", "SELL": "sell", "SHORT": "sell", "COVER": "buy"}
#: Spelled inline in five model fields before this alias existed, so "which way"
#: had five independent spellings to keep in step.
Side = Literal["BUY", "SELL"]
#: Whether a broker evidence batch could be assembled at all.
BrokerEvidenceStatus = Literal["SUCCESS", "PARTIAL", "FAILED"]
DecisionSource = Literal["llm", "fallback_policy_engine", "policy_engine", "baseline"]
HoldReason = Literal["no_signal", "risk_limit_near", "market_uncertain", "await_confirmation", "other"]
AttributionStatus = Literal["LINKED", "UNLINKED"]
StrategyKind = Literal["HOLD_BASELINE", "TREND_FOLLOW", "FIXED_SIZE", "RULE"]
#: The signals a RULE strategy may read. Each is a percent figure over `lookback` bars.
RULE_SIGNALS = ("return_over_n", "price_vs_sma")
StrategyLifecycle = Literal["PROBATION", "ACTIVE", "PAUSED", "RETIRED", "BASELINE"]
CurriculumTaskType = Literal["STRATEGY_SPEC", "OPTIMIZE", "EVALUATE"]
CurriculumTaskSource = Literal["llm", "fallback"]
DaemonStatus = Literal["STARTING", "RUNNING", "BACKING_OFF", "STOPPING", "STOPPED", "ERROR"]
JournalEventType = Literal[
    "REFLECTION_GENERATED",
    "REFLECTION_FAILED",
    "STRATEGY_EVALUATION_RECORDED",
    "CURRICULUM_PROPOSED",
    "CURRICULUM_FAILED",
    "STRATEGY_ADMISSION_REVIEWED",
    "ORDER_RECONCILIATION_REVIEWED",
    "BROKER_EVIDENCE_INGESTED",
    "PNL_EVIDENCE_RECORDED",
    "PNL_EVIDENCE_FAILED",
    "COUNTERFACTUAL_EVALUATED",
    "SHADOW_ORDER_INTENT",
    "OFFLINE_VALIDATION_COMPLETED",
    "PROFIT_TARGET_CHECKED",
    "STRATEGY_LIFECYCLE_UPDATED",
    "KNOWLEDGE_ARTIFACT_PROPOSED",
    "KNOWLEDGE_ARTIFACT_ADMISSION_REVIEWED",
    "CYCLE_FAILED",
    "ORDER_FILL_CONFIRMED",
]
JournalEventStatus = Literal["SUCCESS", "FAILED", "ACCEPTED", "REJECTED", "SKIPPED"]
StrategyParameter = str | int | float | bool
KnowledgeArtifactType = Literal["QA", "OBSERVATION", "LESSON", "SOURCE_NOTE"]
KnowledgeArtifactStatus = Literal["PROPOSED", "ACCEPTED", "REJECTED", "RETIRED"]
KnowledgeSourceKind = Literal["journal", "broker_evidence", "operator_note", "external_stub"]

_FORBIDDEN_EVIDENCE_SOURCES = {"mock", "fake", "fabricated", "synthetic"}
_FORBIDDEN_EXECUTABLE_KEYS = {"code", "python", "script", "exec", "shell", "command"}
_FORBIDDEN_EXECUTABLE_TEXT = ("ex" "ec(", "ev" "al(", "subprocess", "shell" "=True", "os.system", "python -", "bash ")
STRATEGY_PARAMETER_SCHEMAS: dict[str, dict[str, object]] = {
    "HOLD_BASELINE": {
        "required": (),
        "allowed": (),
        "description": "No parameters; always emits HOLD.",
    },
    "FIXED_SIZE": {
        "required": ("action", "quantity", "confidence"),
        "allowed": ("action", "quantity", "confidence"),
        "description": "action BUY|SELL|HOLD; integer quantity >= 0; confidence in [0, 1].",
    },
    "TREND_FOLLOW": {
        "required": ("reference_price", "threshold_pct", "quantity", "confidence"),
        "allowed": ("reference_price", "threshold_pct", "quantity", "confidence"),
        "description": "reference_price > 0; 0 < threshold_pct <= 0.20; positive integer quantity; confidence in [0, 1].",
    },
    # The rule DSL. Parameters only - no code - so a proposed rule can be validated here,
    # executed by `StrategyExecutor`, and backtested, and nothing the model writes is run.
    "RULE": {
        "required": (
            "signal", "lookback", "threshold_pct", "when_above", "when_below", "quantity",
            "confidence",
        ),
        "allowed": (
            "signal", "lookback", "threshold_pct", "when_above", "when_below", "quantity",
            "confidence",
        ),
        "description": (
            "signal return_over_n|price_vs_sma, the percent change over `lookback` bars or the "
            "percent distance from their mean; integer lookback 2-78; threshold_pct in "
            "(0, 5]; when_above (signal > threshold) and when_below (signal < -threshold) each "
            "BUY|SELL|SHORT|COVER|HOLD, inside the band it holds; positive integer quantity; "
            "confidence in [0, 1]."
        ),
    },
}


def _require_real_source(value: str) -> str:
    source = value.strip().lower()
    if source in _FORBIDDEN_EVIDENCE_SOURCES:
        raise ValueError("data source must be real")
    return source


def _normalize_artifact_id(value: str) -> str:
    artifact_id = value.strip().lower().replace(" ", "-")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in artifact_id):
        raise ValueError("artifact_id may only contain lowercase letters, numbers, hyphens, and underscores")
    return artifact_id


def _is_integer_like(value: StrategyParameter) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def _require_no_executable_text(value: str, *, field_name: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in _FORBIDDEN_EXECUTABLE_TEXT):
        raise ValueError(f"{field_name} must not contain executable instructions")
    return value


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    equity: float = Field(ge=0)
    cash: float
    buying_power: float
    portfolio_value: float = Field(ge=0)
    daily_loss: float = Field(ge=0)
    day_start_equity_known: bool = True
    """False when the broker did not report a day-start equity.

    The gateway sets this explicitly. It defaults to True only so that
    hand-built snapshots stay valid; the daily-loss kill switch must never be
    silently disabled by a missing field, so Guardian fails closed on BUY
    when this is False instead of trusting a fabricated loss of 0.0.
    """


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    #: Negative for a short, as Alpaca reports it.
    quantity: float
    #: Signed like `quantity`. It was constrained to >= 0, so the first short position the
    #: broker reported would have failed validation and taken every later snapshot - and so
    #: every trading cycle - down with it.
    market_value: float

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def value_has_the_sign_of_the_quantity(self) -> PositionSnapshot:
        if self.quantity * self.market_value < 0:
            raise ValueError("market_value must have the sign of quantity")
        return self


class OpenOrderSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    status: str = Field(min_length=1)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("side", mode="before")
    @classmethod
    def normalize_side(cls, value: str) -> str:
        return value.strip().upper()


class DataSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime
    market_open: bool
    last_price: float = Field(gt=0)
    source: str
    account: AccountSnapshot
    day_start_equity: float | None = None
    positions: tuple[PositionSnapshot, ...] = Field(default_factory=tuple)
    open_orders: tuple[OpenOrderSnapshot, ...] = Field(default_factory=tuple)
    #: When the price itself was observed, as the broker reported it. `timestamp` above is the
    #: broker's *clock*, which is a different fact: a cycle taken while the market is open against a
    #: quote that printed an hour ago has a fresh clock and a stale price, and measuring only the
    #: clock passes it. Measured 2026-10-09 - `_latest_price` kept the price and discarded the trade's
    #: own timestamp, so this did not exist and nothing downstream could check it.
    #:
    #: Optional, because not every source reports one. An absent value is "nothing to check", not
    #: "stale": a broker without trade timestamps must not stop trading.
    quote_time: datetime | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class NewsItem(BaseModel):
    """One headline the broker carries, as context and nothing more.

    Kept short on purpose. A headline is not a validated signal: every reader tried in
    `docs/evidence/signal-research-2026-10-08/` read overnight headlines correctly (rho +0.11 to
    +0.15 on the gap) and then found nothing left to trade by the next close, which is what the
    record says, so the block it lands in says so too.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    headline: str = Field(min_length=1)
    source: str = "alpaca_news"
    created_at: datetime | None = None
    #: Truncated at a stated length when the headline is not enough; empty when the broker sent none.
    summary: str = ""

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class InstrumentProfile(BaseModel):
    """What a symbol actually is, and what its own price history says it behaves like.

    The first half is the broker's asset record, verbatim: `name` is the answer to "what is this",
    and it is not parsed or guessed. Under `AUTOPOIESIS_UNIVERSE=tradable` a round can reach a 3x
    inverse fund and a commodity ETF (`SOXS`, `BITO`), and before this existed the model was told a
    ticker and a price and nothing else.

    The second half is measured from the symbol's own bars, because a name can be wrong, stale, or
    silent, and because what an instrument *does* is what the decision is about. `flags` therefore
    always carries the number it was derived from, so a reader can disagree with the derivation.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str = ""
    asset_class: str = ""
    exchange: str = ""
    status: str = ""
    tradable: bool | None = None
    marginable: bool | None = None
    shortable: bool | None = None
    easy_to_borrow: bool | None = None
    fractionable: bool | None = None

    last_price: float | None = None
    #: Annualised volatility of daily returns, percent, over `bars_used` days. None when there
    #: are too few bars to measure.
    annualized_vol_pct: float | None = None
    avg_dollar_volume: float | None = None
    bars_used: int = 0

    #: Behaviour against four references over the same window. A 3x inverse fund shows up here
    #: without anyone parsing its name.
    beta_spy: float | None = None
    corr_spy: float | None = None
    corr_qqq: float | None = None
    corr_tlt: float | None = None
    corr_gld: float | None = None
    #: This instrument's daily volatility over the index's over the same window. The test that
    #: catches a leveraged fund whose underlying is not the index, where a correlation to SPY alone
    #: measures near zero.
    vol_ratio_spy: float | None = None

    #: Derived labels, each carrying the measurement behind it. Empty when nothing could be
    #: measured, which is reported rather than filled in.
    flags: tuple[str, ...] = Field(default_factory=tuple)
    #: Why no profile could be built, when that happens. A blank instrument is not the same as an
    #: ordinary one.
    unavailable_reason: str = ""

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class BrokerFillActivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    activity_id: str = Field(min_length=1)
    order_id: str | None = None
    client_order_id: str | None = None
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    gross_amount: float | None = None
    fees: float = Field(default=0, ge=0)
    transaction_time: datetime
    source: str = "alpaca_activities"
    strategy_id: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("side", mode="before")
    @classmethod
    def normalize_side(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class BrokerOrderSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str = Field(min_length=1)
    client_order_id: str | None = None
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    filled_quantity: float = Field(default=0, ge=0)
    filled_avg_price: float | None = Field(default=None, gt=0)
    status: str = Field(min_length=1)
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    source: str = "alpaca_orders"

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("side", mode="before")
    @classmethod
    def normalize_side(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class PortfolioHistoryPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    equity: float = Field(ge=0)
    profit_loss: float | None = None
    profit_loss_pct: float | None = None
    source: str = "alpaca_portfolio_history"

    @field_validator("source")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class BrokerEvidenceBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    orders: tuple[BrokerOrderSnapshot, ...] = Field(default_factory=tuple)
    activities: tuple[BrokerFillActivity, ...] = Field(default_factory=tuple)
    portfolio_history: tuple[PortfolioHistoryPoint, ...] = Field(default_factory=tuple)
    source_account: str = "alpaca_paper"
    status: BrokerEvidenceStatus = "SUCCESS"
    missing_reasons: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("source_account")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class FillAttribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    fill_id: str = Field(min_length=1)
    order_id: str | None = None
    client_order_id: str | None = None
    strategy_id: str | None = None
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(default=0, ge=0)
    transaction_time: datetime
    source: str
    attribution_status: AttributionStatus
    seeded: bool = False

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("side", "attribution_status", mode="before")
    @classmethod
    def normalize_upper_literal_fields(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class OpenLotAttribution(BaseModel):
    """A lot the agent still holds, and what it is worth right now.

    Priced from the latest observed price for the symbol, not from the broker's
    portfolio history, because the portfolio history is a whole-account series and
    this is per-lot. `current_price` is therefore the price the system last saw, and
    `as_of` says when - an open lot marked at a price from three days ago is a
    historical fact, not a valuation, and the timestamp is what tells the two apart.
    """
    model_config = ConfigDict(frozen=True)

    #: LONG: bought then sold. SHORT: sold short then covered, so for a closed SHORT lot
    #: `sell_price` is the entry and `buy_price` the exit, and for an open one `entry_price`
    #: is the short price.
    direction: Literal["LONG", "SHORT"] = "LONG"
    strategy_id: str = Field(min_length=1)
    symbol: str
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    current_price: float | None = Field(default=None, gt=0)
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    opened_at: datetime
    as_of: datetime | None = None
    #: Which order opened this lot. Without it an open position cannot be traced
    #: to the decision that created it, which is the same gap that let 29 shares be
    #: sold with no BUY to account for - an order nobody could follow back.
    buy_order_id: str | None = None
    buy_fill_id: str | None = None
    buy_client_order_id: str | None = None


class ClosedLotAttribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: LONG: bought then sold. SHORT: sold short then covered, so for a closed SHORT lot
    #: `sell_price` is the entry and `buy_price` the exit, and for an open one `entry_price`
    #: is the short price.
    direction: Literal["LONG", "SHORT"] = "LONG"
    strategy_id: str = Field(min_length=1)
    symbol: str
    buy_fill_id: str = Field(min_length=1)
    sell_fill_id: str = Field(min_length=1)
    buy_order_id: str | None = None
    sell_order_id: str | None = None
    buy_client_order_id: str | None = None
    sell_client_order_id: str | None = None
    quantity: float = Field(gt=0)
    buy_price: float = Field(gt=0)
    sell_price: float = Field(gt=0)
    buy_fees: float = Field(default=0, ge=0)
    sell_fees: float = Field(default=0, ge=0)
    realized_pnl: float
    opened_at: datetime
    closed_at: datetime
    source: str
    closing_strategy_id: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class PnLEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    account_realized_or_reported_pnl: float | None = None
    account_return_pct: float | None = None
    strategy_realized_pnl: dict[str, float] = Field(default_factory=dict)
    strategy_fees: dict[str, float] = Field(default_factory=dict)
    #: Peak capital each strategy held at risk at one time: the cost basis of its open
    #: position at its largest, across closed and still-open lots.
    #:
    #: Named for what it is rather than "deployed capital", because that phrase was
    #: read as cumulative turnover and produced a benchmark that was wrong by
    #: construction. A strategy that cycles one share fifteen times turns over 15x the
    #: capital it ever risked, so dividing realized PnL by turnover understates its
    #: return — here by 0.5 points on the largest strategy — and then compares it
    #: against a market return computed on capital held once. Two different
    #: denominators, so the "excess" was not an excess of anything.
    strategy_peak_exposure: dict[str, float] = Field(default_factory=dict)
    #: Realized plus unrealized PnL over that peak exposure, per cent. A strategy that never
    #: held a lot is absent rather than zero: "did not trade" is not "returned nothing".
    strategy_return_pct: dict[str, float] = Field(default_factory=dict)
    #: The instrument's return over the whole journal, for context only - no strategy held
    #: capital for all of it. From the prices this evaluation already holds. Computed here rather than fetched so the benchmark
    #: and the lots cannot rest on two price sources that disagree.
    market_return_pct: float | None = None
    #: Per strategy, the same instrument over that strategy's own window - first entry to
    #: last exit, or to the last valuation while a lot is open. This, not the whole-record
    #: `market_return_pct` above, is what `strategy_excess_vs_market_pct` subtracts.
    strategy_market_return_pct: dict[str, float] = Field(default_factory=dict)
    #: Per strategy, `strategy_return_pct` minus `strategy_market_return_pct`. This is the
    #: project's success metric and nothing else reported it: the promotion gate
    #: screens on decision quality, which the counterfactual ledger measures, and
    #: that ledger cannot see this number by construction - a trade's return there
    #: is holding from the same instant less cost, so every decision shows an excess
    #: of exactly the assumed cost over holding. See
    #: `docs/history/plans/2026-10-04-report-the-holding-benchmark.md`.
    strategy_excess_vs_market_pct: dict[str, float] = Field(default_factory=dict)
    unattributed_pnl: float | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    evidence_source: str = "missing"
    missing_reasons: tuple[str, ...] = Field(default_factory=tuple)
    fill_attributions: tuple[FillAttribution, ...] = Field(default_factory=tuple)
    closed_lots: tuple[ClosedLotAttribution, ...] = Field(default_factory=tuple)
    linked_fill_count: int = Field(default=0, ge=0)
    unlinked_fill_count: int = Field(default=0, ge=0)
    closed_lot_count: int = Field(default=0, ge=0)
    order_derived_fill_count: int = Field(default=0, ge=0)
    open_lot_quantity: dict[str, float] = Field(default_factory=dict)
    unmatched_sell_quantity: dict[str, float] = Field(default_factory=dict)
    #: Shares the agent sold beyond its own lots that the account owner's recorded fills do
    #: cover, priced FIFO against the owner's lots as of that sale. The owner's gain or loss,
    #: realised by an agent order - reported so the 29-share sale has a price, and never part
    #: of any strategy's PnL. Zero without an owner-history ingest (`--ingest-owner-history`).
    owner_exit_quantity: float = 0.0
    owner_exit_cost: float = 0.0
    owner_exit_proceeds: float = 0.0
    #: Open agent lots and what they are worth right now. Only realized PnL was
    #: attributed before this, so a position the agent was holding contributed
    #: nothing to its own record - and the account figure and the agent figure could
    #: never be reconciled even in principle, only explained away.
    open_lots: tuple[OpenLotAttribution, ...] = ()
    #: Realized plus unrealized, when both are known. `None` rather than a guess
    #: when the open side cannot be priced.
    unrealized_pnl: float | None = None
    net_pnl: float | None = None
    #: Realized PnL after the *observed* broker fees only. Kept separate from
    #: `net_after_assumed_cost` because on paper the observed fee is 0.00, and a
    #: number called "net" that silently means "gross, because the broker is free"
    #: is the kind of label that outlives its assumption.
    net_of_fees: float | None = None
    #: Cost charged on top of the observed fees, from an explicit configured
    #: assumption rather than from the broker. Percent of notional, both sides.
    assumed_cost_pct: float = 0.0
    assumed_cost: dict[str, float] = Field(default_factory=dict)
    #: Realized + unrealized - observed fees - assumed cost. This is the after-cost
    #: figure the objective's success criterion is written against, so it is
    #: computed here rather than left to a reader to reconstruct.
    net_after_assumed_cost: float | None = None

    @field_validator("evidence_source")
    @classmethod
    def require_real_or_missing_source(cls, value: str) -> str:
        source = value.strip().lower()
        if source == "missing":
            return source
        return _require_real_source(source)


class StrategySpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: StrategyKind
    symbols: tuple[str, ...]
    parameters: dict[str, StrategyParameter] = Field(default_factory=dict)
    max_position_value: float = Field(gt=0)
    enabled: bool = True
    lifecycle: StrategyLifecycle = "ACTIVE"
    #: Why this strategy carries the lifecycle it does.
    #:
    #: It used to live only in the journal, so the authoritative strategy file said
    #: `PAUSED` and nothing else. On reload there was no way to tell a strategy that
    #: had been rejected on evidence from one that had merely never been testable,
    #: which is why the doctor spent 17 passes reconciling files that "carried a gated
    #: life" it could not explain. `experiment_registry` already kept its own
    #: `lifecycle_reason`, so the reason survived in the derived view and not in the
    #: single source of truth - which is backwards.
    #:
    #: Without it no re-adjudication is possible: a rule cannot ask whether a reason
    #: still holds when the reason was not kept. Defaults to empty so every strategy
    #: written before this still loads.
    lifecycle_reason: str = ""
    #: How many times this strategy has been sent back to probation for want of evidence.
    #:
    #: `cumulative_cycles` is derived from the journal and is therefore monotone: it cannot
    #: be reset by a lifecycle transition, so returning a strategy to PROBATION on its own
    #: does not make it servable again. Measured 2026-10-06: `fixed-size-sell-20260724-001`
    #: was returned to probation with 15 cumulative cycles against a 13 budget, and
    #: `_needs_probation` still read false - it had been "fixed" into a state the selector
    #: still would not serve.
    #:
    #: Each restart grants one further budget, counted against this field. It is a count of
    #: retries, not an attempt limit: nothing here decides that a strategy has had enough
    #: chances. That stays with the screen and the promotion gate, which judge outcomes.
    probation_restarts: int = Field(default=0, ge=0)
    created_at: datetime
    rationale: str = Field(min_length=1)

    @field_validator("strategy_id")
    @classmethod
    def normalize_strategy_id(cls, value: str) -> str:
        strategy_id = value.strip().lower().replace(" ", "-")
        if not strategy_id:
            raise ValueError("strategy_id is required")
        if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in strategy_id):
            raise ValueError("strategy_id may only contain lowercase letters, numbers, hyphens, and underscores")
        return strategy_id

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("lifecycle", mode="before")
    @classmethod
    def normalize_lifecycle(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        symbols = tuple(str(item).strip().upper() for item in value if str(item).strip())
        if not symbols:
            raise ValueError("at least one symbol is required")
        return symbols

    @model_validator(mode="after")
    def validate_executable_parameters(self) -> StrategySpec:
        if any(key.strip().lower() in _FORBIDDEN_EXECUTABLE_KEYS for key in self.parameters):
            raise ValueError("strategy specs must not contain executable code parameters")

        schema = STRATEGY_PARAMETER_SCHEMAS[self.kind]
        allowed = set(coerce.field_str_tuple(schema.get("allowed"), f"{self.kind}.allowed"))
        required = set(coerce.field_str_tuple(schema.get("required"), f"{self.kind}.required"))
        keys = set(self.parameters)
        unknown = keys - allowed
        missing = required - keys
        if unknown:
            raise ValueError(f"unsupported parameters for {self.kind}: {sorted(unknown)}")
        if missing:
            raise ValueError(f"missing parameters for {self.kind}: {sorted(missing)}")

        if self.kind == "FIXED_SIZE":
            action = str(self.parameters["action"]).strip().upper()
            quantity = self.parameters["quantity"]
            confidence = self.parameters["confidence"]
            if action not in ORDER_ACTIONS | {"HOLD"}:
                raise ValueError("FIXED_SIZE action must be BUY, SELL, SHORT, COVER or HOLD")
            if not _is_integer_like(quantity) or coerce.field_int(quantity, "quantity") < 0:
                raise ValueError("FIXED_SIZE quantity must be a non-negative integer")
            quantity_value = coerce.field_int(quantity, "quantity")
            if action in ORDER_ACTIONS and quantity_value <= 0:
                raise ValueError("FIXED_SIZE BUY and SELL require positive quantity")
            if action == "HOLD" and quantity_value != 0:
                raise ValueError("FIXED_SIZE HOLD requires zero quantity")
            if not isinstance(confidence, int | float) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
                raise ValueError("FIXED_SIZE confidence must be in [0, 1]")

        if self.kind == "TREND_FOLLOW":
            reference_price = self.parameters["reference_price"]
            threshold_pct = self.parameters["threshold_pct"]
            quantity = self.parameters["quantity"]
            confidence = self.parameters["confidence"]
            if not isinstance(reference_price, int | float) or isinstance(reference_price, bool) or float(reference_price) <= 0:
                raise ValueError("TREND_FOLLOW reference_price must be positive")
            if not isinstance(threshold_pct, int | float) or isinstance(threshold_pct, bool) or not 0 < float(threshold_pct) <= 0.20:
                raise ValueError("TREND_FOLLOW threshold_pct must be in (0, 0.20]")
            if not _is_integer_like(quantity) or coerce.field_int(quantity, "quantity") <= 0:
                raise ValueError("TREND_FOLLOW quantity must be a positive integer")
            if not isinstance(confidence, int | float) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
                raise ValueError("TREND_FOLLOW confidence must be in [0, 1]")

        if self.kind == "RULE":
            p = self.parameters
            if p["signal"] not in RULE_SIGNALS:
                raise ValueError(f"RULE signal must be one of {', '.join(RULE_SIGNALS)}")
            if not _is_integer_like(p["lookback"]) or not 2 <= coerce.field_int(p["lookback"], "lookback") <= 78:
                raise ValueError("RULE lookback must be an integer in [2, 78]")
            t = p["threshold_pct"]
            if not isinstance(t, int | float) or isinstance(t, bool) or not 0 < float(t) <= 5:
                raise ValueError("RULE threshold_pct must be in (0, 5]")
            for key in ("when_above", "when_below"):
                if str(p[key]).strip().upper() not in ORDER_ACTIONS | {"HOLD"}:
                    raise ValueError(f"RULE {key} must be BUY, SELL, SHORT, COVER or HOLD")
            if not _is_integer_like(p["quantity"]) or coerce.field_int(p["quantity"], "quantity") <= 0:
                raise ValueError("RULE quantity must be a positive integer")
            c = p["confidence"]
            if not isinstance(c, int | float) or isinstance(c, bool) or not 0 <= float(c) <= 1:
                raise ValueError("RULE confidence must be in [0, 1]")
        return self


class KnowledgeArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(min_length=1)
    artifact_type: KnowledgeArtifactType
    question: str | None = None
    answer: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    tags: tuple[str, ...] = Field(default_factory=tuple)
    source_kind: KnowledgeSourceKind
    source_refs: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime
    status: KnowledgeArtifactStatus = "PROPOSED"
    rationale: str = Field(min_length=1)

    @field_validator("artifact_id")
    @classmethod
    def normalize_artifact_id(cls, value: str) -> str:
        return _normalize_artifact_id(value)

    @field_validator("artifact_type", "status", mode="before")
    @classmethod
    def normalize_upper_literal_fields(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source_kind", mode="before")
    @classmethod
    def normalize_source_kind(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        return tuple(str(item).strip().lower() for item in value if str(item).strip())

    @field_validator("source_refs", mode="before")
    @classmethod
    def normalize_source_refs(cls, value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        return tuple(str(item).strip() for item in value if str(item).strip())

    @model_validator(mode="after")
    def validate_knowledge_artifact(self) -> KnowledgeArtifact:
        _require_no_executable_text(self.answer, field_name="answer")
        _require_no_executable_text(self.summary, field_name="summary")
        _require_no_executable_text(self.rationale, field_name="rationale")
        if self.question is not None:
            _require_no_executable_text(self.question, field_name="question")
        if any(tag in _FORBIDDEN_EVIDENCE_SOURCES for tag in self.tags):
            raise ValueError("knowledge tags must not use forbidden evidence labels")
        if any(ref.strip().lower() in _FORBIDDEN_EVIDENCE_SOURCES for ref in self.source_refs):
            raise ValueError("knowledge source refs must not use forbidden evidence labels")
        if self.source_kind != "operator_note" and not self.source_refs:
            raise ValueError("knowledge artifacts require source_refs unless source_kind is operator_note")
        return self


class StrategyEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(min_length=1)
    cycles: int = Field(ge=0)
    #: Cycles this strategy was selected for across the whole journal, as opposed to
    #: `cycles`, which counts only the reflection window. See `StrategyResult` for why
    #: the probation rules cannot use the windowed number.
    cumulative_cycles: int = Field(default=0, ge=0)
    cumulative_trade_attempts: int = Field(default=0, ge=0)
    submitted_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)
    skipped_orders: int = Field(ge=0)
    errors: int = Field(ge=0)
    action_counts: dict[str, int] = Field(default_factory=dict)
    guardian_rejections: dict[str, int] = Field(default_factory=dict)
    intended_notional: float = Field(ge=0)
    submitted_intended_notional: float = Field(ge=0)
    filled_quantity: float = Field(ge=0)
    fill_quantity_ratio: float | None = None
    pnl_evidence: str = "missing_fill_price_and_broker_activity"
    realized_pnl: float | None = None
    fees: float | None = None
    score: float
    trade_attempts: int = Field(default=0, ge=0)
    """Cycles in which the strategy actually tried to trade.

    Distinct from submitted_orders: a BUY that Guardian stopped because the
    market was closed is still an attempt. Without this, inaction was
    indistinguishable from success and scored perfectly.
    """
    strategy_fault_rejections: int | None = None
    """Rejections attributable to the strategy, excluding the risk system's own
    correct refusals (market closed, daily trade limit reached, ...).

    None means "not split" and is read as all of rejected_orders. Defaulting to
    0 would silently under-charge any result built without the split, which
    would reward a strategy for rejections the system issued against it."""


class EvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    window_cycles: int = Field(ge=0)
    submitted_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)
    skipped_orders: int = Field(ge=0)
    #: Orders the loop would have submitted had shadow trading been off.
    #: A count, never a PnL contribution: a shadow order proves the path ran,
    #: not that money moved. Kept here so the stage is visibly exercised.
    shadowed_orders: int = Field(default=0, ge=0)
    execution_errors: int = Field(ge=0)
    error_count: int = Field(ge=0)
    guardian_approved: int = Field(ge=0)
    guardian_rejected: int = Field(ge=0)
    guardian_rejections: dict[str, int] = Field(default_factory=dict)
    action_counts: dict[str, int] = Field(default_factory=dict)
    hold_reasons: dict[str, int] = Field(default_factory=dict)
    """Why HOLDs happened, as stated by the decision maker.

    A risk-driven abstention (`risk_limit_near`) means the decision maker is acting
    as a second risk authority. It has to be countable, otherwise a trading halt reads
    as an ordinary quiet day in the journal.
    """
    data_sources: dict[str, int] = Field(default_factory=dict)
    market_open_cycles: int = Field(ge=0)
    market_closed_cycles: int = Field(ge=0)
    intended_notional: float = Field(ge=0)
    submitted_intended_notional: float = Field(ge=0)
    filled_quantity: float = Field(ge=0)
    fill_quantity_ratio: float | None = None
    first_observed_equity: float | None = None
    last_observed_equity: float | None = None
    observed_equity_delta: float | None = None
    pnl_evidence: str = "missing_fill_price_and_broker_activity"
    pnl: PnLEvidence | None = None
    strategy_metrics: dict[str, StrategyEvaluation] = Field(default_factory=dict)


class StrategyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(min_length=1)
    #: Cycles this strategy was selected for, **within the reflection window**.
    #: This is a freshness measure: error and rejection rates, the score and the
    #: exploration floor are all recent-behaviour judgements and belong here.
    cycles: int = Field(ge=0)
    #: Cycles this strategy has been selected for across the whole journal.
    #:
    #: A different quantity from `cycles`, and the only correct one for "has this
    #: candidate had its probation budget". The window is 50 cycle records shared by
    #: every strategy that traded in it, so it currently tops out at 7 cycles for any
    #: one strategy - which made a 13-cycle probation budget, and the verdict that
    #: checks it, unreachable. Probation then never ended, the queue never emptied,
    #: and because the queue has absolute priority the one ACTIVE strategy was
    #: selected 0 times in the 146 cycles after its promotion.
    #:
    #: Defaults to 0 so a result built without it behaves exactly as before.
    cumulative_cycles: int = Field(default=0, ge=0)
    #: Lifetime BUY/SELL decisions this strategy produced. Paired with
    #: `cumulative_cycles` because both answer the same question - how much has this
    #: candidate been given, and what did it do with it - so they are counted together
    #: and carried together. The windowed `trade_attempts` cannot answer it:
    #: `trend-follow-buy-010` was paused for "no exploration evidence" on 2026-10-04
    #: with a windowed count of 0 and a lifetime count of 1.
    cumulative_trade_attempts: int = Field(default=0, ge=0)
    submitted_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)
    errors: int = Field(ge=0)
    realized_pnl: float | None = None
    score: float
    evaluated_at: datetime
    skipped_orders: int = Field(default=0, ge=0)
    action_counts: dict[str, int] = Field(default_factory=dict)
    guardian_rejections: dict[str, int] = Field(default_factory=dict)
    intended_notional: float = Field(default=0, ge=0)
    filled_quantity: float = Field(default=0, ge=0)
    fees: float | None = None
    pnl_evidence: str = "missing_fill_price_and_broker_activity"
    trade_attempts: int = Field(default=0, ge=0)
    #: Orders the loop would have submitted had shadow trading been off. Visible so
    #: the stage can be seen working, and impossible to mistake for PnL: it is a
    #: count, and it never reaches `realized_pnl`.
    shadowed_orders: int = Field(default=0, ge=0)
    strategy_fault_rejections: int | None = None


class CurriculumTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    task_type: CurriculumTaskType
    summary: str = Field(min_length=1)
    strategy_spec: StrategySpec | None = None
    parameters: dict[str, StrategyParameter] = Field(default_factory=dict)
    rationale: str = Field(min_length=1)
    source: CurriculumTaskSource = "llm"
    """Who produced this task.

    The curriculum caught every LLM failure and returned a hard-coded
    `_fallback_task`, which the daemon then journaled as status="SUCCESS". 30 of
    the 168 recorded CURRICULUM_PROPOSED events were such constants, and the
    journal could not tell them apart from real model output. Fallbacks now
    declare themselves.
    """

    @field_validator("task_type", mode="before")
    @classmethod
    def normalize_task_type(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def require_strategy_spec_for_strategy_tasks(self) -> CurriculumTask:
        if self.task_type == "STRATEGY_SPEC" and self.strategy_spec is None:
            raise ValueError("STRATEGY_SPEC tasks require a strategy_spec")
        return self


class ReflectionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    window_cycles: int = Field(ge=0)
    submitted_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)
    error_count: int = Field(ge=0)
    guardian_rejections: dict[str, int] = Field(default_factory=dict)
    strategy_scores: dict[str, float] = Field(default_factory=dict)
    summary: str
    evaluation: dict[str, object] = Field(default_factory=dict)
    strategy_metrics: dict[str, dict[str, object]] = Field(default_factory=dict)


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    pid: int = Field(gt=0)
    status: DaemonStatus
    timestamp: datetime
    cycle_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    active_strategy_id: str | None = None
    last_cycle_id: str | None = None
    message: str = ""
    #: Fingerprint of the `src/autopoiesis` tree this process imported, so a running
    #: daemon can be compared against the worktree it claims to be running.
    #:
    #: Liveness and currency are different properties and a health check that only
    #: knows about the first misses the second completely. A daemon kept running across
    #: a fix to `evaluator.is_system_rejection` stayed perfectly healthy - fresh
    #: heartbeat, journal events landing, `doctor` green - while continuing to retire
    #: `fixed-size-sell-005` under the old rules, twice, hours after the rule had been
    #: corrected and tested. Nothing in the system asked whether the process running the
    #: code was the code on disk.
    source_fingerprint: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().upper()


class TradeDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    action: Action
    quantity: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    strategy_id: str | None = None
    #: Which model produced this decision, e.g. "qwen3.8:27b".
    #:
    #: This is provenance, and it was missing. Every decision recorded
    #: `decision_source` - "llm", "fallback_policy_engine", "baseline" - but not
    #: *which* model, so the 67 LLM decisions could not be separated by prompt or by
    #: model version, and no outcome could ever be attributed to a model change. The
    #: model was upgraded during this project; without this field that upgrade is
    #: invisible in the record and its effect is unmeasurable forever.
    #:
    #: Optional and defaulting to None so the 920 cycles already on the journal still
    #: parse. They predate the field, and back-filling them with the currently
    #: configured model would assert a fact about the past that is not known.
    model: str | None = None
    hold_reason: HoldReason | None = None
    """Why a HOLD was chosen, stated by the model.

    Without this a risk-driven abstention is indistinguishable from an ordinary
    one in the audit trail. Guardian logs "approved" for a HOLD, so when the model
    declines to act because of a risk scalar it has de-facto become a second risk
    authority - one that can halt trading with none of Guardian's
    auditability, and which an operator cannot see or attribute. Recording the
    reason makes the halt visible as a halt.

    It is a reason, not a permission: Guardian still reviews every decision, and
    this never widens or narrows what is allowed.
    """
    rule_action: Action | None = None
    """What the selected strategy's own rule decided on the same snapshot.

    Recorded on every LLM-path decision, so each cycle carries both the rule's action and the
    action taken, and the model's contribution is a paired comparison on identical inputs
    rather than an attribution over whichever lots it happened to open. None on cycles
    journalled before this field existed - they cannot be paired, and back-filling would
    claim a fact about the past that was never recorded.
    """
    lesson_ids: tuple[str, ...] | None = None
    """The admitted lessons this decision was shown, and those withheld from it this cycle.

    Every decision used to see the same lessons, so no lesson's effect could be told apart
    from the model's own behaviour. Each lesson is now shown on a deterministic half of
    cycles, and these two fields make the ablation readable from the journal. None on cycles
    before the split existed."""
    lessons_withheld: tuple[str, ...] | None = None
    override_reason: str | None = None
    """Why the model departed from `rule_action`, in its own words.

    The rule is the default; the model may override it, but only by saying why. An override
    with no reason is not taken."""
    decision_source: DecisionSource = "baseline"
    """Who produced this decision.

    The daemon used to run PolicyEngine only, so every trade decision was a
    static JSON lookup while the LLM was invoked solely for curriculum. And when
    the curriculum LLM failed, a hard-coded `_fallback_task` was journaled as
    status="SUCCESS", so the journal could not tell model output from a
    constant. This field makes provenance auditable per decision.
    """

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def require_quantity_for_orders(self) -> TradeDecision:
        if self.action in ORDER_ACTIONS and self.quantity <= 0:
            raise ValueError("BUY, SELL, SHORT and COVER require positive quantity")
        if self.action == "HOLD" and self.quantity != 0:
            raise ValueError("HOLD requires zero quantity")
        return self


class GuardianResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    approved: bool
    reason: str

    @model_validator(mode="after")
    def require_rejection_reason(self) -> GuardianResult:
        if not self.approved and not self.reason.strip():
            raise ValueError("rejections require a reason")
        return self


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    # SHADOWED is a real outcome of a real cycle, and it is deliberately its own
    # value rather than a reuse of SKIPPED. Anything that branches on this field and
    # does not know SHADOWED will not count it as executed, which is the safe
    # default; tests/autopoiesis/test_shadow.py proves the PnL ledger reports zero
    # from shadow-only cycles, so a shadow order can never be mistaken for a fill.
    # REPLAYED is a real outcome of a real decision made over real bars, and it is a
    # distinct value for the same reason SHADOWED is: anything branching on this field
    # that does not know REPLAYED will not count it as executed, which is the safe
    # default. A replayed fill is not a broker fill and must never enter the PnL ledger.
    status: Literal["SKIPPED", "SUBMITTED", "REJECTED", "ERROR", "SHADOWED", "REPLAYED"]
    order_id: str | None
    client_order_id: str | None = None
    filled_quantity: float = Field(ge=0)
    filled_avg_price: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0)
    filled_at: datetime | None = None
    submitted_at: datetime | None = None
    broker_status: str | None = None
    message: str


class CycleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    cycle_id: str
    snapshot: DataSnapshot
    decision: TradeDecision
    guardian: GuardianResult
    execution: ExecutionResult
    strategy_id: str | None = None
    error: str | None = None

    @property
    def symbol(self) -> str:
        return self.snapshot.symbol


class JournalEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1)
    event_type: JournalEventType
    timestamp: datetime
    status: JournalEventStatus
    message: str = ""
    cycle_id: str | None = None
    strategy_id: str | None = None
    task_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("status", mode="before")
    @classmethod
    def normalize_event_status(cls, value: str) -> str:
        return value.strip().upper()
