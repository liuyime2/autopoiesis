"""A file in the strategy library is not the same thing as an approved strategy.

The engine selects straight from the library directory, so once a JSON file lands
there it is indistinguishable from a strategy the admission gate approved. The
doctor had a check for this, but it reported its findings as a WARN hedged with
"legacy from before admission was journalled, or written outside the gate" - a
hedge the journal can resolve by itself, by comparing each file against the first
admission event ever recorded. Resolving it changed the answer: all five files it
names were written after the gate was journalled, and three of them placed 33
broker-confirmed paper fills. A warning that cannot fail is not a control.

These tests pin the resolved behaviour: post-gate entries fail, genuine
pre-gate legacy stays a warning, and a real bypass names the trading it caused.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from min_agent.doctor import DoctorReport, _check_admission_provenance
from min_agent.journal import JsonlJournal
from min_agent.models import JournalEvent


class _Cfg:
    def __init__(self, strategy_dir, journal_path):
        self.strategy_dir = strategy_dir
        self.journal_path = journal_path


def _spec(strategy_id: str, lifecycle: str = "ACTIVE") -> dict:
    return {
        "strategy_id": strategy_id, "name": strategy_id, "kind": "FIXED_SIZE",
        "symbols": ["SPY"], "parameters": {"action": "BUY", "quantity": 1, "confidence": 0.6},
        "max_position_value": 5000.0, "created_at": "2026-06-18T03:21:34+00:00",
        "rationale": "r", "enabled": True, "lifecycle": lifecycle,
    }


def _build(tmp_path, *, files, reviews, fills=()):
    sdir = tmp_path / "strategies"
    sdir.mkdir(exist_ok=True)
    for spec in files:
        (sdir / f"{spec['strategy_id']}.json").write_text(json.dumps(spec))
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for i, (sid, status) in enumerate(reviews):
        journal.append_event(JournalEvent(
            event_id=f"r{i}", event_type="STRATEGY_ADMISSION_REVIEWED",
            timestamp=datetime(2026, 6, 10, 23, 34, tzinfo=timezone.utc),
            status=status, message="reviewed", strategy_id=sid,
            payload={"accepted": status == "ACCEPTED", "reason": "r"},
        ))
    for i, sid in enumerate(fills):
        journal.append_event(JournalEvent(
            event_id=f"f{i}", event_type="ORDER_FILL_CONFIRMED",
            timestamp=datetime(2026, 6, 18, 15, 0, tzinfo=timezone.utc),
            status="SUCCESS", message="fill", strategy_id=sid, payload={},
        ))
    report = DoctorReport()
    _check_admission_provenance(report, _Cfg(sdir, journal.path), journal)
    return report


def _check(report, name="admission provenance"):
    return next(c for c in report.checks if c.name == name)


def test_post_gate_file_with_no_accepted_admission_fails(tmp_path):
    """The bypass must be able to turn the gate red, not merely warn."""
    report = _build(
        tmp_path,
        files=[_spec("approved-001"), _spec("sneaky-001")],
        reviews=[("approved-001", "ACCEPTED")],
    )
    check = _check(report)
    assert check.status.value == "fail", (
        f"a file written after the gate with no accepted admission must fail, got "
        f"{check.status.value}: {check.detail}"
    )
    assert "sneaky-001" in check.detail
    assert "approved-001" not in check.detail, "an admitted strategy is not a finding"


def test_genuine_legacy_file_stays_a_warning(tmp_path):
    """A file older than the first admission event really is legacy."""
    sdir = tmp_path / "strategies"
    sdir.mkdir()
    (sdir / "ancient-001.json").write_text(json.dumps(_spec("ancient-001")))
    old = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(sdir / "ancient-001.json", (old, old))
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append_event(JournalEvent(
        event_id="r0", event_type="STRATEGY_ADMISSION_REVIEWED",
        timestamp=datetime(2026, 6, 10, 23, 34, tzinfo=timezone.utc),
        status="ACCEPTED", message="ok", strategy_id="approved-001",
        payload={"accepted": True, "reason": "r"},
    ))
    (sdir / "approved-001.json").write_text(json.dumps(_spec("approved-001")))
    report = DoctorReport()
    _check_admission_provenance(report, _Cfg(sdir, journal.path), journal)
    check = _check(report)
    assert check.status.value == "warn", f"legacy should warn, got {check.status.value}"
    assert "ancient-001" in check.detail


def test_a_bypass_names_the_trading_it_caused(tmp_path):
    """Severity has to be visible: these files placed real broker-confirmed fills."""
    report = _build(
        tmp_path,
        files=[_spec("sneaky-001"), _spec("approved-001")],
        reviews=[("approved-001", "ACCEPTED")],
        fills=["sneaky-001", "sneaky-001", "sneaky-001"],
    )
    detail = _check(report).detail
    assert "3fill(s)" in detail, f"the fill count must be reported: {detail}"


def test_fully_traceable_library_passes(tmp_path):
    report = _build(
        tmp_path,
        files=[_spec("approved-001"), _spec("approved-002")],
        reviews=[("approved-001", "ACCEPTED"), ("approved-002", "ACCEPTED")],
    )
    assert _check(report).status.value == "ok"


def test_no_reviews_at_all_is_not_a_pass(tmp_path):
    """An empty gate and a satisfied gate must not look the same."""
    report = _build(tmp_path, files=[_spec("orphan-001")], reviews=[])
    assert _check(report).status.value == "warn"
    assert "no admission reviews" in _check(report).detail


def test_rejections_do_not_count_as_admissions(tmp_path):
    """The bug this class of check exists to catch: refused, then traded anyway."""
    report = _build(
        tmp_path,
        files=[_spec("refused-001")],
        reviews=[("refused-001", "REJECTED")],
    )
    check = _check(report)
    assert check.status.value == "fail"
    assert "refused-001" in check.detail


def test_refused_and_not_selectable_is_accounted_for(tmp_path):
    """A gate that says no is a gate working, not a gate being bypassed.

    The first version of the repaired check demanded an ACCEPTED event for every
    file, so a correctly-refused spec sitting at RETIRED was reported as a
    bypass - and re-adjudicating the five unaccounted files could be applied and
    still leave the gate red for having done the right thing. Refused plus
    non-selectable is a complete disposition.
    """
    report = _build(
        tmp_path,
        files=[_spec("refused-001", lifecycle="RETIRED")],
        reviews=[("refused-001", "REJECTED")],
    )
    check = _check(report)
    assert check.status.value == "ok", (
        f"a refused spec that is retired is accounted for, got {check.status.value}: "
        f"{check.detail}"
    )


def test_refused_but_still_selectable_is_the_worse_failure(tmp_path):
    """Refused and ACTIVE means the gate's verdict was ignored."""
    report = _build(
        tmp_path,
        files=[_spec("refused-001", lifecycle="ACTIVE")],
        reviews=[("refused-001", "REJECTED")],
    )
    check = _check(report)
    assert check.status.value == "fail"
    assert "still selectable" in check.detail, check.detail
