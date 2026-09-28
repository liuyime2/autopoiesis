from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Action = Literal["BUY", "SELL", "HOLD"]
DecisionSource = Literal["llm", "fallback_policy_engine", "policy_engine", "baseline"]
HoldReason = Literal["no_signal", "risk_limit_near", "market_uncertain", "await_confirmation", "other"]
AttributionStatus = Literal["LINKED", "UNLINKED"]
StrategyKind = Literal["HOLD_BASELINE", "TREND_FOLLOW", "FIXED_SIZE"]
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
    quantity: float
    market_value: float = Field(ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class OpenOrderSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    side: Literal["BUY", "SELL"]
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

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source")
    @classmethod
    def require_real_source(cls, value: str) -> str:
        return _require_real_source(value)


class BrokerFillActivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    activity_id: str = Field(min_length=1)
    order_id: str | None = None
    client_order_id: str | None = None
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    gross_amount: float | None = None
    fees: float = Field(default=0, ge=0)
    transaction_time: datetime
    source: str = "alpaca_activities"

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
    side: Literal["BUY", "SELL"]
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
    status: Literal["SUCCESS", "PARTIAL", "FAILED"] = "SUCCESS"
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
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(default=0, ge=0)
    transaction_time: datetime
    source: str
    attribution_status: AttributionStatus

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


class ClosedLotAttribution(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    def validate_executable_parameters(self) -> "StrategySpec":
        if any(key.strip().lower() in _FORBIDDEN_EXECUTABLE_KEYS for key in self.parameters):
            raise ValueError("strategy specs must not contain executable code parameters")

        schema = STRATEGY_PARAMETER_SCHEMAS[self.kind]
        allowed = set(schema["allowed"])
        required = set(schema["required"])
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
            if action not in {"BUY", "SELL", "HOLD"}:
                raise ValueError("FIXED_SIZE action must be BUY, SELL, or HOLD")
            if not _is_integer_like(quantity) or quantity < 0:
                raise ValueError("FIXED_SIZE quantity must be a non-negative integer")
            if action in {"BUY", "SELL"} and quantity <= 0:
                raise ValueError("FIXED_SIZE BUY and SELL require positive quantity")
            if action == "HOLD" and quantity != 0:
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
            if not _is_integer_like(quantity) or quantity <= 0:
                raise ValueError("TREND_FOLLOW quantity must be a positive integer")
            if not isinstance(confidence, int | float) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
                raise ValueError("TREND_FOLLOW confidence must be in [0, 1]")
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
    def validate_knowledge_artifact(self) -> "KnowledgeArtifact":
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
    cycles: int = Field(ge=0)
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
    def require_strategy_spec_for_strategy_tasks(self) -> "CurriculumTask":
        if self.task_type == "STRATEGY_SPEC" and self.strategy_spec is None:
            raise ValueError("STRATEGY_SPEC tasks require a strategy_spec")
        return self


class DaemonState(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: DaemonStatus
    started_at: datetime
    updated_at: datetime
    cycle_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    active_strategy_id: str | None = None
    last_cycle_id: str | None = None
    message: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().upper()


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
    def require_quantity_for_orders(self) -> "TradeDecision":
        if self.action in {"BUY", "SELL"} and self.quantity <= 0:
            raise ValueError("BUY and SELL require positive quantity")
        if self.action == "HOLD" and self.quantity != 0:
            raise ValueError("HOLD requires zero quantity")
        return self


class GuardianResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    approved: bool
    reason: str

    @model_validator(mode="after")
    def require_rejection_reason(self) -> "GuardianResult":
        if not self.approved and not self.reason.strip():
            raise ValueError("rejections require a reason")
        return self


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["SKIPPED", "SUBMITTED", "REJECTED", "ERROR"]
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
