"""The safety envelope has to be machine-checked, not assumed.

Everything here holds today only because no code does the violating thing. That is
not an invariant - it is an absence, and one commit can remove it. Each test below
provokes the violation and requires the corresponding doctor check to fail, so the
envelope is enforced by the health check rather than by memory.

The live session these protect: 24 broker-confirmed orders and +564.39 of
broker-verified per-strategy PnL, produced without the agent ever being able to
touch a risk limit, the allowlist, or Guardian.
"""

import json
from datetime import datetime, timezone

import pytest

from autopoiesis.atomicio import write_json_atomic
from autopoiesis.config import AgentConfig
from autopoiesis.doctor import RISK_BASELINE_PATH, run_doctor


def _config(tmp_path, **overrides):
    base = dict(
        mode="paper",
        alpaca_base_url="https://paper-api.alpaca.markets",
        allowlist={"SPY", "QQQ"},
        symbols=("SPY",),
        alpaca_api_key="k",
        alpaca_secret_key="s",
        max_position_value=5000.0,
        max_daily_loss=500.0,
        max_total_exposure=20000.0,
        max_account_value=0.0,
        max_trades_per_day=10,
        min_confidence=0.5,
        ollama_base_url="http://127.0.0.1:11434",
        model="qwen3.8:27b",
        gpu_devices="0",
        journal_path=tmp_path / "journal.jsonl",
        strategy_dir=tmp_path / "strategies",
        knowledge_dir=tmp_path / "knowledge",
        heartbeat_path=tmp_path / "heartbeat.json",
        pidfile_path=tmp_path / "daemon.pid",
    )
    base.update(overrides)
    return AgentConfig(**base)


def _status(report, name):
    for check in report.checks:
        if check.name == name:
            return check.status.value
    raise AssertionError(f"no check named {name!r}; got {[c.name for c in report.checks]}")


def _detail(report, name):
    for check in report.checks:
        if check.name == name:
            return check.detail
    raise AssertionError(f"no check named {name!r}")


def test_an_order_submitted_without_guardian_approval_fails_the_check(tmp_path):
    """`executor.execute` refuses to submit an unapproved decision, but nothing ever
    checked that it did - and a reflection layer reads these same records."""
    from autopoiesis.journal import JsonlJournal
    from autopoiesis.models import (
        AccountSnapshot,
        CycleRecord,
        DataSnapshot,
        ExecutionResult,
        GuardianResult,
        StrategySpec,
        TradeDecision,
    )

    config = _config(tmp_path)
    journal = JsonlJournal(config.journal_path)
    record = CycleRecord(
        cycle_id="c1",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=datetime.now(tz=timezone.utc), market_open=True,
            last_price=766.0, source="alpaca",
            account=AccountSnapshot(equity=100_000, cash=50_000, buying_power=50_000,
                                    portfolio_value=100_000, daily_loss=0),
        ),
        decision=TradeDecision(symbol="SPY", action="BUY", quantity=1,
                               confidence=0.9, rationale="r"),
        guardian=GuardianResult(approved=False, reason="total exposure exceeds hard limit"),
        execution=ExecutionResult(status="SUBMITTED", order_id="oid-1",
                                  client_order_id="cid-1", filled_quantity=0.0,
                                  message="pending_new"),
        strategy_id="s1",
    )
    journal.append(record)

    report = run_doctor(config, skip_broker=True)

    assert _status(report, "guardian bypass") == "fail"
    assert "1 order(s) submitted without Guardian approval" in _detail(report, "guardian bypass")


def test_a_real_session_reports_no_bypass(tmp_path):
    """The other direction: the check must not cry wolf, or it gets ignored."""
    from autopoiesis.journal import JsonlJournal
    from autopoiesis.models import (
        AccountSnapshot,
        CycleRecord,
        DataSnapshot,
        ExecutionResult,
        GuardianResult,
        TradeDecision,
    )

    config = _config(tmp_path)
    journal = JsonlJournal(config.journal_path)
    record = CycleRecord(
        cycle_id="c1",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=datetime.now(tz=timezone.utc), market_open=True,
            last_price=766.0, source="alpaca",
            account=AccountSnapshot(equity=100_000, cash=50_000, buying_power=50_000,
                                    portfolio_value=100_000, daily_loss=0),
        ),
        decision=TradeDecision(symbol="SPY", action="BUY", quantity=1,
                               confidence=0.9, rationale="r"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status="SUBMITTED", order_id="oid-1",
                                  client_order_id="cid-1", filled_quantity=0.0,
                                  message="pending_new"),
        strategy_id="s1",
    )
    journal.append(record)

    report = run_doctor(config, skip_broker=True)

    assert _status(report, "guardian bypass") == "ok"


def test_raising_a_risk_limit_after_the_baseline_fails(tmp_path):
    """The `risk limits` check prints the values and compares them to nothing, so
    5000 and 500000 reported identically. That is the check the old auto-fix.sh
    would have defeated, and it is exactly what a self-modifying agent must not be
    able to do quietly."""
    baseline = tmp_path / RISK_BASELINE_PATH
    write_json_atomic(baseline, {
        "recorded_at": "2026-09-28T00:00:00+00:00",
        "limits": {
            "max_position_value": 5000.0,
            "max_daily_loss": 500.0,
            "max_total_exposure": 20000.0,
            "max_account_value": 0.0,
            "max_trades_per_day": 10,
            "min_confidence": 0.5,
            "allowlist": ["QQQ", "SPY"],
        },
    })

    # The limit is raised *after* the baseline was taken - the direction that
    # matters, and the one the old auto-fix.sh exploited.
    raised = _config(tmp_path, max_position_value=50000.0)
    report = run_doctor(raised, skip_broker=True)

    assert _status(report, "risk limits vs baseline") == "fail"
    assert "max_position_value 5000.0 -> 50000.0" in _detail(report, "risk limits vs baseline")


def test_widening_the_allowlist_after_the_baseline_fails(tmp_path):
    """`MIN_AGENT_ALLOWLIST` was never read by doctor at all, so a symbol the agent
    had no mandate for could be added invisibly."""

    config = _config(tmp_path)
    write_json_atomic(tmp_path / RISK_BASELINE_PATH, {
        "recorded_at": "2026-09-28T00:00:00+00:00",
        "limits": {
            "max_position_value": 5000.0, "max_daily_loss": 500.0,
            "max_total_exposure": 20000.0, "max_account_value": 0.0,
            "max_trades_per_day": 10, "min_confidence": 0.5,
            "allowlist": ["QQQ", "SPY"],
        },
    })
    widened = _config(tmp_path, allowlist={"SPY", "QQQ", "BIL"})

    report = run_doctor(widened, skip_broker=True)

    assert _status(report, "risk limits vs baseline") == "fail"
    detail = _detail(report, "risk limits vs baseline")
    assert "allowlist" in detail and "BIL" in detail


def test_lowering_a_risk_limit_is_not_flagged_as_loosened(tmp_path):
    """Tightening is always safe, so the check must only fire in one direction -
    otherwise a legitimate de-risking would be reported as a breach."""

    config = _config(tmp_path)
    write_json_atomic(tmp_path / RISK_BASELINE_PATH, {
        "recorded_at": "2026-09-28T00:00:00+00:00",
        "limits": {
            "max_position_value": 50000.0, "max_daily_loss": 500.0,
            "max_total_exposure": 200000.0, "max_account_value": 0.0,
            "max_trades_per_day": 10, "min_confidence": 0.5,
            "allowlist": ["QQQ", "SPY"],
        },
    })

    report = run_doctor(config, skip_broker=True)

    assert _status(report, "risk limits vs baseline") == "ok"


def test_a_duplicate_retirement_unjournalled_by_one_legitimate_decision_fails(tmp_path):
    """The check this was written for, made able to fail.

    The first version asked whether *any* decision had produced a given lifecycle,
    so one journalled retirement laundered every other unjournalled one. It passed
    against eight strategies that had been retired with no recorded decision at
    all - the exact bypass it exists to catch, and a check that cannot fail is
    worse than no check.
    """
    from datetime import datetime, timezone

    from autopoiesis.atomicio import write_json_atomic
    from autopoiesis.journal import JsonlJournal
    from autopoiesis.models import JournalEvent, StrategySpec
    from autopoiesis.strategy_engine import StrategyLibrary

    config = _config(tmp_path)
    write_json_atomic(tmp_path / RISK_BASELINE_PATH, {
        "recorded_at": "2026-09-28T00:00:00+00:00",
        "limits": {
            "max_position_value": 5000.0, "max_daily_loss": 500.0,
            "max_total_exposure": 20000.0, "max_account_value": 0.0,
            "max_trades_per_day": 10, "min_confidence": 0.5,
            "allowlist": ["QQQ", "SPY"],
        },
    })
    library = StrategyLibrary(config.strategy_dir)
    for sid in ("legitimate", "sneaky-1", "sneaky-2"):
        library.save(StrategySpec(
            strategy_id=sid, name=sid, kind="FIXED_SIZE", symbols=("SPY",),
            parameters={"action": "BUY", "quantity": 1, "confidence": 0.7},
            max_position_value=1000.0, enabled=True, lifecycle="RETIRED",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc), rationale="t",
        ))

    journal = JsonlJournal(config.journal_path)
    journal.append_event(JournalEvent(
        event_id="e1", event_type="STRATEGY_LIFECYCLE_UPDATED",
        timestamp=datetime.now(tz=timezone.utc), status="SUCCESS", message="x",
        strategy_id="legitimate",
        payload={"old_lifecycle": "PROBATION", "new_lifecycle": "RETIRED", "reason": "r"},
    ))

    report = run_doctor(config, skip_broker=True)

    assert _status(report, "lifecycle provenance") == "fail"
    detail = _detail(report, "lifecycle provenance")
    assert "sneaky-1" in detail and "sneaky-2" in detail
    assert "legitimate" not in detail, "the journalled one must not be implicated"
