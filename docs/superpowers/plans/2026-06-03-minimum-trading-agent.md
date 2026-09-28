# Minimum Trading Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the smallest autonomous paper-trading agent that can analyze real data, make an LLM decision, pass Guardian review, execute paper trades, journal outcomes, and reflect on results.

**Architecture:** Create a new focused package rather than modifying historical systems in place. The package has explicit mode separation, real-data provenance checks, structured LLM decisions, Guardian approval, Alpaca paper execution, append-only journaling, and reflection memory.

**Tech Stack:** Python 3.10+, pydantic, pytest, loguru, Alpaca paper API, JSONL journals, Superpowers planning workflow.

---

## File Structure

Create a new implementation under `src/min_agent/`.

```text
src/min_agent/
  __init__.py
  modes.py
  models.py
  data_gateway.py
  llm_decision.py
  guardian.py
  executor.py
  journal.py
  reflector.py
  loop.py
  config.py

tests/min_agent/
  test_modes.py
  test_models.py
  test_guardian.py
  test_journal.py
  test_loop.py
```

Responsibilities:

- `modes.py`: defines `demo`, `test`, `paper`, and `live` mode rules.
- `models.py`: defines typed data, decision, approval, execution, and journal records.
- `data_gateway.py`: fetches real market/account data and tags provenance.
- `llm_decision.py`: parses structured LLM output.
- `guardian.py`: blocks unsafe or malformed trades.
- `executor.py`: submits approved paper orders.
- `journal.py`: appends cycle records to JSONL.
- `reflector.py`: writes compact reflection memory from journal outcomes.
- `loop.py`: orchestrates one complete trading cycle.
- `config.py`: loads allowlist, risk limits, and mode.

## Task 1: Mode Contract

**Files:**
- Create: `src/min_agent/__init__.py`
- Create: `src/min_agent/modes.py`
- Test: `tests/min_agent/test_modes.py`

- [ ] **Step 1: Write failing mode tests**

Create `tests/min_agent/test_modes.py`:

```python
import pytest

from src.min_agent.modes import TradingMode, validate_data_allowed


def test_paper_rejects_non_real_data():
    with pytest.raises(ValueError, match="non-real data"):
        validate_data_allowed(TradingMode.PAPER, is_real=False)


def test_live_rejects_non_real_data():
    with pytest.raises(ValueError, match="non-real data"):
        validate_data_allowed(TradingMode.LIVE, is_real=False)


def test_test_mode_allows_fixture_data():
    validate_data_allowed(TradingMode.TEST, is_real=False)


def test_demo_mode_allows_fixture_data():
    validate_data_allowed(TradingMode.DEMO, is_real=False)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/min_agent/test_modes.py -q
```

Expected: import failure because `src.min_agent.modes` does not exist.

- [ ] **Step 3: Implement modes**

Create `src/min_agent/__init__.py`:

```python
"""Minimum autonomous paper-trading agent."""
```

Create `src/min_agent/modes.py`:

```python
from enum import StrEnum


class TradingMode(StrEnum):
    DEMO = "demo"
    TEST = "test"
    PAPER = "paper"
    LIVE = "live"


def validate_data_allowed(mode: TradingMode, is_real: bool) -> None:
    if mode in {TradingMode.PAPER, TradingMode.LIVE} and not is_real:
        raise ValueError(f"{mode.value} mode rejects non-real data")
```

- [ ] **Step 4: Run mode tests**

Run:

```bash
python -m pytest tests/min_agent/test_modes.py -q
```

Expected: all tests pass.

## Task 2: Core Typed Models

**Files:**
- Create: `src/min_agent/models.py`
- Test: `tests/min_agent/test_models.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/min_agent/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from src.min_agent.models import (
    DataSnapshot,
    LLMDecision,
    GuardianResult,
)


def test_data_snapshot_requires_real_flag():
    snap = DataSnapshot(
        source="alpaca",
        fetched_at="2026-06-03T00:00:00Z",
        mode="paper",
        is_real=True,
        payload={"symbol": "SPY"},
    )
    assert snap.is_real is True


def test_decision_rejects_invalid_action():
    with pytest.raises(ValidationError):
        LLMDecision(
            action="WAIT",
            symbol="SPY",
            confidence=0.5,
            target_position_pct=0.1,
            reasoning="invalid action",
            risk_notes=[],
            evidence=[],
        )


def test_guardian_rejection_requires_reason():
    with pytest.raises(ValidationError):
        GuardianResult(approved=False, reason="")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/min_agent/test_models.py -q
```

Expected: import failure because `src.min_agent.models` does not exist.

- [ ] **Step 3: Implement models**

Create `src/min_agent/models.py`:

```python
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


class DataSnapshot(BaseModel):
    source: str
    fetched_at: str
    mode: Literal["demo", "test", "paper", "live"]
    is_real: bool
    payload: dict[str, Any]


class LLMDecision(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str
    confidence: float = Field(ge=0.0, le=1.0)
    target_position_pct: float = Field(ge=0.0, le=1.0)
    reasoning: str
    risk_notes: list[str]
    evidence: list[str]

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        return symbol


class GuardianResult(BaseModel):
    approved: bool
    reason: str = ""
    approved_action: dict[str, Any] | None = None

    @field_validator("reason")
    @classmethod
    def rejection_needs_reason(cls, value: str, info):
        approved = info.data.get("approved")
        if approved is False and not value.strip():
            raise ValueError("rejection reason is required")
        return value
```

- [ ] **Step 4: Run model tests**

Run:

```bash
python -m pytest tests/min_agent/test_models.py -q
```

Expected: all tests pass.

## Task 3: Guardian Risk Gate

**Files:**
- Create: `src/min_agent/guardian.py`
- Test: `tests/min_agent/test_guardian.py`

- [ ] **Step 1: Write failing Guardian tests**

Create `tests/min_agent/test_guardian.py`:

```python
from src.min_agent.guardian import Guardian, RiskLimits
from src.min_agent.models import DataSnapshot, LLMDecision


def decision(action="BUY", symbol="SPY", pct=0.1):
    return LLMDecision(
        action=action,
        symbol=symbol,
        confidence=0.7,
        target_position_pct=pct,
        reasoning="test decision",
        risk_notes=[],
        evidence=["test evidence"],
    )


def real_snapshot(payload):
    return DataSnapshot(
        source="alpaca",
        fetched_at="2026-06-03T00:00:00Z",
        mode="paper",
        is_real=True,
        payload=payload,
    )


def test_guardian_rejects_symbol_outside_allowlist():
    guardian = Guardian(RiskLimits(allowlist={"SPY"}))
    result = guardian.review(
        decision("BUY", "TSLA", 0.1),
        market=real_snapshot({"market_open": True}),
        account=real_snapshot({"equity": 100000, "daily_loss_pct": 0.0}),
    )
    assert result.approved is False
    assert "allowlist" in result.reason


def test_guardian_rejects_market_closed():
    guardian = Guardian(RiskLimits(allowlist={"SPY"}))
    result = guardian.review(
        decision("BUY", "SPY", 0.1),
        market=real_snapshot({"market_open": False}),
        account=real_snapshot({"equity": 100000, "daily_loss_pct": 0.0}),
    )
    assert result.approved is False
    assert "market closed" in result.reason


def test_guardian_rejects_position_above_limit():
    guardian = Guardian(RiskLimits(allowlist={"SPY"}, max_single_position_pct=0.2))
    result = guardian.review(
        decision("BUY", "SPY", 0.5),
        market=real_snapshot({"market_open": True}),
        account=real_snapshot({"equity": 100000, "daily_loss_pct": 0.0}),
    )
    assert result.approved is False
    assert "position" in result.reason


def test_guardian_approves_hold():
    guardian = Guardian(RiskLimits(allowlist={"SPY"}))
    result = guardian.review(
        decision("HOLD", "SPY", 0.0),
        market=real_snapshot({"market_open": True}),
        account=real_snapshot({"equity": 100000, "daily_loss_pct": 0.0}),
    )
    assert result.approved is True
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/min_agent/test_guardian.py -q
```

Expected: import failure because `src.min_agent.guardian` does not exist.

- [ ] **Step 3: Implement Guardian**

Create `src/min_agent/guardian.py`:

```python
from dataclasses import dataclass, field

from src.min_agent.models import DataSnapshot, GuardianResult, LLMDecision


@dataclass(frozen=True)
class RiskLimits:
    allowlist: set[str] = field(default_factory=lambda: {"SPY", "QQQ", "AAPL", "MSFT", "NVDA"})
    max_single_position_pct: float = 0.2
    max_daily_loss_pct: float = 0.03


class Guardian:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def review(
        self,
        decision: LLMDecision,
        market: DataSnapshot,
        account: DataSnapshot,
    ) -> GuardianResult:
        if not market.is_real or not account.is_real:
            return GuardianResult(approved=False, reason="non-real data rejected")

        if decision.symbol not in self.limits.allowlist:
            return GuardianResult(approved=False, reason=f"{decision.symbol} outside allowlist")

        if not market.payload.get("market_open", False):
            return GuardianResult(approved=False, reason="market closed")

        daily_loss_pct = float(account.payload.get("daily_loss_pct", 0.0))
        if daily_loss_pct >= self.limits.max_daily_loss_pct:
            return GuardianResult(approved=False, reason="daily loss limit reached")

        if decision.target_position_pct > self.limits.max_single_position_pct:
            return GuardianResult(approved=False, reason="position size exceeds limit")

        return GuardianResult(approved=True, approved_action=decision.model_dump())
```

- [ ] **Step 4: Run Guardian tests**

Run:

```bash
python -m pytest tests/min_agent/test_guardian.py -q
```

Expected: all tests pass.

## Task 4: Append-Only Decision Journal

**Files:**
- Create: `src/min_agent/journal.py`
- Test: `tests/min_agent/test_journal.py`

- [ ] **Step 1: Write failing journal tests**

Create `tests/min_agent/test_journal.py`:

```python
import json

from src.min_agent.journal import DecisionJournal


def test_journal_appends_jsonl_record(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = DecisionJournal(path)
    journal.append({"cycle_id": "C1", "mode": "paper"})
    journal.append({"cycle_id": "C2", "mode": "paper"})

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["cycle_id"] == "C1"
    assert json.loads(lines[1])["cycle_id"] == "C2"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/min_agent/test_journal.py -q
```

Expected: import failure because `src.min_agent.journal` does not exist.

- [ ] **Step 3: Implement journal**

Create `src/min_agent/journal.py`:

```python
import json
from pathlib import Path
from typing import Any


class DecisionJournal:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run journal tests**

Run:

```bash
python -m pytest tests/min_agent/test_journal.py -q
```

Expected: all tests pass.

## Task 5: One-Cycle Orchestrator

**Files:**
- Create: `src/min_agent/loop.py`
- Test: `tests/min_agent/test_loop.py`

- [ ] **Step 1: Write failing loop test**

Create `tests/min_agent/test_loop.py`:

```python
import json

from src.min_agent.guardian import Guardian, RiskLimits
from src.min_agent.journal import DecisionJournal
from src.min_agent.loop import TradingCycle
from src.min_agent.models import DataSnapshot, LLMDecision


class FakeDataGateway:
    def market(self):
        return DataSnapshot(
            source="alpaca",
            fetched_at="2026-06-03T00:00:00Z",
            mode="paper",
            is_real=True,
            payload={"market_open": True},
        )

    def account(self):
        return DataSnapshot(
            source="alpaca",
            fetched_at="2026-06-03T00:00:00Z",
            mode="paper",
            is_real=True,
            payload={"equity": 100000, "daily_loss_pct": 0.0},
        )


class FakeDecisionEngine:
    def decide(self, market, account):
        return LLMDecision(
            action="HOLD",
            symbol="SPY",
            confidence=0.6,
            target_position_pct=0.0,
            reasoning="no edge",
            risk_notes=[],
            evidence=["market open"],
        )


class FakeExecutor:
    def execute(self, guardian_result):
        return {"status": "NOOP"}


def test_cycle_journals_decision(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    cycle = TradingCycle(
        data_gateway=FakeDataGateway(),
        decision_engine=FakeDecisionEngine(),
        guardian=Guardian(RiskLimits(allowlist={"SPY"})),
        executor=FakeExecutor(),
        journal=DecisionJournal(journal_path),
    )

    result = cycle.run_once("C1")

    assert result["cycle_id"] == "C1"
    lines = journal_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["guardian"]["approved"] is True
    assert record["execution"]["status"] == "NOOP"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/min_agent/test_loop.py -q
```

Expected: import failure because `src.min_agent.loop` does not exist.

- [ ] **Step 3: Implement cycle orchestrator**

Create `src/min_agent/loop.py`:

```python
from datetime import datetime, timezone
from typing import Any


class TradingCycle:
    def __init__(self, data_gateway, decision_engine, guardian, executor, journal):
        self.data_gateway = data_gateway
        self.decision_engine = decision_engine
        self.guardian = guardian
        self.executor = executor
        self.journal = journal

    def run_once(self, cycle_id: str) -> dict[str, Any]:
        market = self.data_gateway.market()
        account = self.data_gateway.account()
        decision = self.decision_engine.decide(market, account)
        guardian_result = self.guardian.review(decision, market, account)

        if guardian_result.approved:
            execution = self.executor.execute(guardian_result)
        else:
            execution = {"status": "BLOCKED", "reason": guardian_result.reason}

        record = {
            "cycle_id": cycle_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market": market.model_dump(),
            "account": account.model_dump(),
            "decision": decision.model_dump(),
            "guardian": guardian_result.model_dump(),
            "execution": execution,
        }
        self.journal.append(record)
        return record
```

- [ ] **Step 4: Run loop tests**

Run:

```bash
python -m pytest tests/min_agent/test_loop.py -q
```

Expected: all tests pass.

## Task 6: Verification

**Files:**
- No new files.

- [ ] **Step 1: Run full min-agent tests**

Run:

```bash
python -m pytest tests/min_agent -q
```

Expected: all tests pass.

- [ ] **Step 2: Scan for unsafe live-path shortcuts**

Run:

```bash
grep -R "return True\\|mock\\|shell=True\\|live" -n src/min_agent tests/min_agent
```

Expected: no `return True` market shortcut, no `shell=True`, and no live execution path.

- [ ] **Step 3: Document verification output**

Add the exact test command and result to the session progress notes before claiming implementation complete.

## Completion Criteria

- `src/min_agent` exists with focused modules.
- `tests/min_agent` covers mode separation, schemas, Guardian, journal, and one-cycle orchestration.
- Paper/live paths reject non-real data.
- The minimal cycle journals every decision.
- No broker live execution exists in Phase 1.
- No claims about profitability are made until real paper-trading records exist.

## Execution Options

1. Subagent-Driven: implement each task in a fresh worker and review after every task.
2. Inline Execution: implement tasks sequentially in this session with test checkpoints.

