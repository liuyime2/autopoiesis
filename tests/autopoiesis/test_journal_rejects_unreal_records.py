"""A strict journal refuses a cycle record no real cycle could have produced.

On 2026-10-27 five records `probe-0`..`probe-4` (SPY exactly 600.00, rationale `x`, labelled
`alpaca`, no quote time) were appended to the production journal by a hand-run script, and a sixth,
`probe`, on 2026-10-23. All fixtures below are synthetic and labelled so; they are written only to
temporary journals.

Usage: pytest tests/autopoiesis/test_journal_rejects_unreal_records.py -q
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from autopoiesis.journal import JsonlJournal, UnrealRecordError, unreal_reason
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)

AFTER_QUOTE_TIME_ERA = datetime(2026, 10, 27, 20, 24, 29, tzinfo=timezone.utc)
BEFORE_QUOTE_TIME_ERA = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)


def _record(*, cycle_id: str | None = None, at=AFTER_QUOTE_TIME_ERA, source="alpaca", quote_time=None) -> CycleRecord:
    return CycleRecord(
        cycle_id=cycle_id or str(uuid4()),
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=at, market_open=True, last_price=600.0, source=source,
            account=AccountSnapshot(equity=100_000, cash=40_000, buying_power=40_000, portfolio_value=100_000, daily_loss=0),
            quote_time=quote_time,
        ),
        decision=TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.5, rationale="x"),
        guardian=GuardianResult(approved=True, reason="ok"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold"),
    )


def test_the_exact_probe_record_is_unreal():
    assert "not a uuid" in unreal_reason(_record(cycle_id="probe-0"))
    assert "not a uuid" in unreal_reason(_record(cycle_id="probe"))


def test_an_alpaca_snapshot_without_a_quote_time_is_unreal_only_in_the_era_that_records_one():
    assert "quote time" in unreal_reason(_record())
    assert unreal_reason(_record(at=BEFORE_QUOTE_TIME_ERA)) is None, "2,060 real older cycles have none"
    assert unreal_reason(_record(quote_time=AFTER_QUOTE_TIME_ERA)) is None


def test_other_sources_are_not_judged_by_the_alpaca_rule():
    assert unreal_reason(_record(source="alpaca_replay")) is None


def test_a_strict_journal_refuses_and_writes_nothing(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(path, strict=True)
    with pytest.raises(UnrealRecordError, match="probe-0"):
        journal.append(_record(cycle_id="probe-0"))
    assert not path.exists() or path.read_text() == ""
    journal.append(_record(quote_time=AFTER_QUOTE_TIME_ERA))
    assert len(path.read_text().splitlines()) == 1


def test_the_default_journal_stays_permissive_so_tests_and_tools_are_unchanged(tmp_path):
    JsonlJournal(tmp_path / "j.jsonl").append(_record(cycle_id="probe-0"))


def test_doctor_fails_on_an_unacknowledged_unreal_record_and_passes_once_it_is_marked(tmp_path):
    from autopoiesis.config import AgentConfig
    from autopoiesis.doctor import DoctorReport, _check_journal

    cfg = AgentConfig.from_env({
        "AUTOPOIESIS_JOURNAL": str(tmp_path / "journal.jsonl"),
        "AUTOPOIESIS_STRATEGY_DIR": str(tmp_path / "strategies"),
    })
    JsonlJournal(cfg.journal_path).append(_record(cycle_id="probe-0"))

    def verdict():
        report = DoctorReport()
        _check_journal(report, cfg)
        return {c.name: c.status.value for c in report.checks}["journal real"]

    assert verdict() == "fail"
    (tmp_path / "invalidated_cycles.json").write_text(json.dumps(
        {"invalidated": [{"cycle_id": "probe-0", "reason": "synthetic probe", "invalidated_at": "2026-10-31T00:00:00Z"}]}))
    assert verdict() == "ok"


def test_every_journal_the_cli_opens_on_the_production_path_is_strict():
    """A new call site that forgot `strict=True` would reopen the door the probe records came through."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "autopoiesis" / "cli.py").read_text(encoding="utf-8")
    sites = re.findall(r"JsonlJournal\(config\.journal_path[^)]*\)", source)
    assert sites, "the cli no longer opens the journal this way; update this test with it"
    assert all("strict=True" in site for site in sites), [s for s in sites if "strict=True" not in s]


def test_a_writer_waits_for_a_reader_for_as_long_as_the_journal_is_told_to(tmp_path):
    """2026-10-30: a cycle's record was lost after a 10 s wait behind a reader (synthetic reproduction)."""
    import threading
    import time

    from autopoiesis.atomicio import file_lock

    path = tmp_path / "journal.jsonl"
    lock_path = path.with_suffix(path.suffix + ".lock")
    held = threading.Event()

    def reader():
        with file_lock(lock_path, exclusive=False):
            held.set()
            time.sleep(0.6)

    threading.Thread(target=reader).start()
    held.wait()
    with pytest.raises(TimeoutError):
        JsonlJournal(path, lock_timeout=0.1).append(_record(quote_time=AFTER_QUOTE_TIME_ERA))

    held.clear()
    threading.Thread(target=reader).start()
    held.wait()
    JsonlJournal(path, lock_timeout=5.0).append(_record(quote_time=AFTER_QUOTE_TIME_ERA))
    assert len(path.read_text().splitlines()) == 1


def test_a_strategy_retired_by_the_screen_cannot_be_re_admitted_by_proposing_it_again(tmp_path):
    from autopoiesis.strategy_admission import NON_EXPIRING_RETIREMENT_REASONS, StrategyAdmission

    assert any(r.startswith("offline validation rejected") for r in NON_EXPIRING_RETIREMENT_REASONS)
    reason = "offline validation rejected its own recorded decisions: 23 scored decision(s), 14 correct"
    existing = type("S", (), {"readmissions": 0, "lifecycle_reason": reason})()
    gate = StrategyAdmission.__new__(StrategyAdmission)
    assert "re-admission refused" in gate._readmission_refusal(existing)
