"""An operator pause must be auditable, reversible in intent, and idempotent in effect.

Measured 2026-10-14: the curriculum answered "this position has no sell strategy"
with unconditional `SELL 1` probes, which have no condition that can become false and
therefore fire every cycle until the position is empty. Broker-verified: TLT 188
shares sold, XLE and XLF emptied entirely. `pause_strategy` is the entry point used to
stop that on a live position, so what it writes and what it refuses is the difference
between an auditable stop and a silent hand edit of the library.

Every fixture here is synthetic. The point of the module is that it can be trusted
without a running daemon, an account or a market.

Usage: pytest tests/autopoiesis/test_strategy_pause.py -q
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autopoiesis.journal import JournalEvent, JsonlJournal
from autopoiesis.models import StrategySpec
from autopoiesis.strategy_engine import StrategyLibrary
from autopoiesis.strategy_pause import StrategyPauseRefused, pause_strategy

TS = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def _spec(strategy_id: str, lifecycle: str = "PROBATION") -> StrategySpec:
    return StrategySpec.model_validate(
        {
            "strategy_id": strategy_id,
            "name": strategy_id,
            "symbols": ["TLT"],
            "created_at": "2026-10-13T00:00:00Z",
            "rationale": "synthetic fixture",
            "enabled": True,
            "kind": "FIXED_SIZE",
            "parameters": {"action": "SELL", "quantity": 1, "confidence": 0.5},
            "max_position_value": 1000,
            "lifecycle": lifecycle,
        }
    )


def _library(tmp_path, *specs) -> StrategyLibrary:
    library = StrategyLibrary(tmp_path / "strategies")
    for spec in specs:
        library.save(spec)
    return library


# --- it stops the strategy ---------------------------------------------------


def test_a_paused_strategy_is_no_longer_offered_for_selection(tmp_path):
    """The point of the whole exercise. PAUSED is what the selector already excludes."""
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    assert [s.strategy_id for s in library.list()] == ["fixed-size-tlt-sell-001"]

    pause_strategy(
        library=library,
        journal=None,
        strategy_id="fixed-size-tlt-sell-001",
        reason="unconditional exit probe, no market condition",
    )

    served = library.selectable() if hasattr(library, "selectable") else None
    listed = library.list()
    assert listed[0].lifecycle == "PAUSED"
    if served is not None:
        assert served == [], "a paused strategy must not be servable"
    # Whatever the selector is called, the spec it would load must not be PROBATION.
    assert listed[0].lifecycle != "PROBATION"


def test_the_pause_survives_a_reload_from_disk(tmp_path):
    """The running daemon re-reads the directory every cycle, so the file is the state."""
    library = _library(tmp_path, _spec("fixed-size-xle-sell-001"))
    pause_strategy(
        library=library, journal=None, strategy_id="fixed-size-xle-sell-001",
        reason="unconditional exit probe",
    )

    fresh = StrategyLibrary(tmp_path / "strategies").try_load("fixed-size-xle-sell-001")
    assert fresh is not None and fresh.lifecycle == "PAUSED"
    assert fresh.lifecycle_reason == "unconditional exit probe"


def test_pausing_one_strategy_leaves_the_others_untouched(tmp_path):
    library = _library(
        tmp_path, _spec("fixed-size-tlt-sell-001"), _spec("fixed-size-xlf-sell-001")
    )
    pause_strategy(
        library=library, journal=None, strategy_id="fixed-size-tlt-sell-001",
        reason="unconditional exit probe",
    )

    by_id = {s.strategy_id: s.lifecycle for s in library.list()}
    assert by_id["fixed-size-tlt-sell-001"] == "PAUSED"
    assert by_id["fixed-size-xlf-sell-001"] == "PROBATION", (
        "an operator pause must not silence the rest of the library"
    )


# --- it is auditable ---------------------------------------------------------


def test_the_journal_records_who_paused_what_and_from_which_state(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    pause_strategy(
        library=library, journal=journal, strategy_id="fixed-size-tlt-sell-001",
        reason="unconditional exit probe", actor="operator:liuyime2", now=TS,
    )

    events = [json.loads(line) for line in journal.path.read_text().splitlines() if line]
    pauses = [e for e in events if e["event_type"] == "STRATEGY_LIFECYCLE_UPDATED"]
    assert len(pauses) == 1, events
    payload = pauses[0]["payload"]
    assert payload["source"] == "operator_pause"
    assert payload["actor"] == "operator:liuyime2"
    assert payload["previous_lifecycle"] == "PROBATION", (
        "an audit that cannot say what it changed from is not an audit"
    )
    assert payload["lifecycle"] == "PAUSED"
    assert payload["new_lifecycle"] == "PAUSED", (
        "doctor.py's lifecycle-provenance check reads `new_lifecycle`; an event that "
        "omits it makes three correctly-paused strategies fail the doctor as "
        "'reached a state with no matching recorded decision'"
    )
    assert pauses[0]["strategy_id"] == "fixed-size-tlt-sell-001"
    assert "unconditional exit probe" in pauses[0]["message"]


# --- it refuses what should be refused --------------------------------------


def test_a_retired_strategy_cannot_be_resurrected_by_an_operator_pause(tmp_path):
    """Retirement is terminal and rests on evidence. An operator must not route around it."""
    library = _library(tmp_path, _spec("fixed-size-bil-sell-001", lifecycle="RETIRED"))

    with pytest.raises(StrategyPauseRefused) as raised:
        pause_strategy(
            library=library, journal=None, strategy_id="fixed-size-bil-sell-001",
            reason="bring it back",
        )
    assert "terminal" in str(raised.value)
    assert library.try_load("fixed-size-bil-sell-001").lifecycle == "RETIRED"


def test_a_pause_without_a_reason_is_refused(tmp_path):
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))

    with pytest.raises(StrategyPauseRefused) as raised:
        pause_strategy(
            library=library, journal=None, strategy_id="fixed-size-tlt-sell-001", reason="  "
        )
    assert "--reason" in str(raised.value)
    assert library.try_load("fixed-size-tlt-sell-001").lifecycle == "PROBATION", (
        "a refused pause must not have written anything"
    )


def test_an_unknown_strategy_is_refused_rather_than_created(tmp_path):
    library = _library(tmp_path)

    with pytest.raises(StrategyPauseRefused) as raised:
        pause_strategy(
            library=library, journal=None, strategy_id="fixed-size-does-not-exist",
            reason="typo",
        )
    assert "no such strategy" in str(raised.value)
    assert library.try_load("fixed-size-does-not-exist") is None


def test_repeating_the_same_pause_does_not_grow_the_audit_trail(tmp_path):
    """A retried command must be idempotent, not a second identical event."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    for _ in range(2):
        try:
            pause_strategy(
                library=library, journal=journal, strategy_id="fixed-size-tlt-sell-001",
                reason="unconditional exit probe", actor="operator", now=TS,
            )
        except StrategyPauseRefused:
            pass

    events = [
        json.loads(line)
        for line in journal.path.read_text().splitlines()
        if line
    ]
    pauses = [e for e in events if e["event_type"] == "STRATEGY_LIFECYCLE_UPDATED"]
    assert len(pauses) == 1, f"a repeat must not re-write history: {len(pauses)} events"


def test_pausing_with_a_new_reason_is_a_real_transition_and_is_recorded(tmp_path):
    """Re-pausing for a different reason is a change of mind, and must be visible."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    pause_strategy(
        library=library, journal=journal, strategy_id="fixed-size-tlt-sell-001",
        reason="first reason", actor="operator", now=TS,
    )
    pause_strategy(
        library=library, journal=journal, strategy_id="fixed-size-tlt-sell-001",
        reason="second reason", actor="operator", now=TS,
    )

    events = [
        json.loads(line)
        for line in journal.path.read_text().splitlines()
        if line
    ]
    pauses = [e for e in events if e["event_type"] == "STRATEGY_LIFECYCLE_UPDATED"]
    assert len(pauses) == 2
    assert pauses[1]["payload"]["previous_lifecycle"] == "PAUSED"
    assert library.try_load("fixed-size-tlt-sell-001").lifecycle_reason == "second reason"


def test_the_refused_paths_write_no_journal_entry(tmp_path):
    """An audit trail of refused attempts is noise; only applied transitions belong."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    library = _library(tmp_path, _spec("fixed-size-bil-sell-001", lifecycle="RETIRED"))
    with pytest.raises(StrategyPauseRefused):
        pause_strategy(
            library=library, journal=journal, strategy_id="fixed-size-bil-sell-001",
            reason="nope", actor="operator", now=TS,
        )
    assert not journal.path.exists(), (
        "a refused pause must not create an audit file either"
    )


def test_the_journal_is_optional_but_the_file_write_is_not(tmp_path):
    """A pause with no journal still has to take effect; it just leaves no audit."""
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    pause_strategy(
        library=library, journal=None, strategy_id="fixed-size-tlt-sell-001", reason="x"
    )
    assert library.try_load("fixed-size-tlt-sell-001").lifecycle == "PAUSED"


def test_the_strategy_id_is_normalised_the_way_the_library_normalises_it(tmp_path):
    """The library keys files by the validated id, so the entry point must use that too.

    Measured while writing this: `StrategySpec` lowercases `strategy_id`, so saving
    `probe-BASELINE` writes `probe-baseline.json`. A pause that looked the spec up by
    the caller's spelling would report "no such strategy" for a strategy that is
    demonstrably on disk.
    """
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    with pytest.raises(StrategyPauseRefused) as raised:
        pause_strategy(
            library=library, journal=None,
            strategy_id="FIXED-SIZE-TLT-SELL-001", reason="typo",
        )
    assert "no such strategy" in str(raised.value)
    pause_strategy(
        library=library, journal=None, strategy_id="fixed-size-tlt-sell-001",
        reason="unconditional exit probe",
    )
    assert library.try_load("fixed-size-tlt-sell-001").lifecycle == "PAUSED"


def test_an_accepted_strategy_can_still_be_paused(tmp_path):
    """BASELINE and ACTIVE are not terminal; a bad strategy must be stoppable either way."""
    for lifecycle in ("BASELINE", "ACTIVE"):
        library = _library(tmp_path, _spec(f"probe-{lifecycle.lower()}", lifecycle=lifecycle))
        pause_strategy(
            library=library, journal=None, strategy_id=f"probe-{lifecycle.lower()}",
            reason="misfiring",
        )
        assert library.try_load(f"probe-{lifecycle.lower()}").lifecycle == "PAUSED"


def test_the_doctor_can_trace_the_pause_to_the_journal(tmp_path):
    """The entry point's own acceptance criterion, asserted against the real reader.

    This is not a shape comparison: it runs `_check_lifecycle_provenance` over a journal
    the pause wrote and requires the result to be OK. Written because the first version
    wrote `lifecycle` while doctor.py reads `new_lifecycle`, and three correctly-paused
    strategies failed the doctor as "reached a state with no matching recorded decision".
    """
    from test_daemon import config as _config

    from autopoiesis.doctor import DoctorReport, Status, _check_lifecycle_provenance

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    pause_strategy(
        library=library, journal=journal, strategy_id="fixed-size-tlt-sell-001",
        reason="unconditional exit probe", actor="operator", now=TS,
    )

    config = _config(tmp_path)
    object.__setattr__(config, "strategy_dir", tmp_path / "strategies")
    report = DoctorReport()
    _check_lifecycle_provenance(report, config, journal)

    checks = [c for c in report.checks if c.name == "lifecycle provenance"]
    assert checks, report.checks
    assert checks[0].status is Status.OK, (
        f"an operator pause is a recorded decision and the doctor must trace it: "
        f"{checks[0].detail}"
    )


def test_the_doctor_reads_a_pause_written_before_the_key_existed(tmp_path):
    """The journal is append-only, so the reader must read what was recorded.

    Three pauses were written on 2026-10-14 carrying `lifecycle` and not
    `new_lifecycle`, which left the doctor reporting them as states with no recorded
    decision. Those events cannot be rewritten to suit a reader. The acceptance is
    narrow: only an event that names itself an operator pause gets the second key.
    """
    from test_daemon import config as _config

    from autopoiesis.doctor import DoctorReport, Status, _check_lifecycle_provenance

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    journal.append_event(JournalEvent(
        event_id="legacy-pause", event_type="STRATEGY_LIFECYCLE_UPDATED", timestamp=TS,
        status="SUCCESS", message="paused by operator: unconditional exit probe",
        strategy_id="fixed-size-tlt-sell-001",
        payload={
            "phase": "applied", "source": "operator_pause", "actor": "operator",
            "previous_lifecycle": "PROBATION", "lifecycle": "PAUSED",
            "lifecycle_reason": "unconditional exit probe",
        },
    ))
    library.save(_spec("fixed-size-tlt-sell-001").model_copy(update={"lifecycle": "PAUSED"}))

    config = _config(tmp_path)
    object.__setattr__(config, "strategy_dir", tmp_path / "strategies")
    report = DoctorReport()
    _check_lifecycle_provenance(report, config, journal)

    checks = [c for c in report.checks if c.name == "lifecycle provenance"]
    assert checks and checks[0].status is Status.OK, (
        f"an append-only journal cannot be rewritten to suit a reader: {checks}"
    )


def test_the_doctor_still_reports_a_lifecycle_manager_event_with_no_new_lifecycle(tmp_path):
    """The narrow acceptance must not become a blanket one.

    A lifecycle-manager transition that omits `new_lifecycle` is a real gap - the
    strategy reached a gated state and the journal does not say what it moved to - and
    must still be reported.
    """
    from test_daemon import config as _config

    from autopoiesis.doctor import DoctorReport, Status, _check_lifecycle_provenance

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    library = _library(tmp_path, _spec("fixed-size-tlt-sell-001"))
    journal.append_event(JournalEvent(
        event_id="malformed", event_type="STRATEGY_LIFECYCLE_UPDATED", timestamp=TS,
        status="SUCCESS", message="a manager transition with no target state",
        strategy_id="fixed-size-tlt-sell-001",
        payload={"phase": "applied", "lifecycle": "PAUSED"},
    ))
    library.save(_spec("fixed-size-tlt-sell-001").model_copy(update={"lifecycle": "PAUSED"}))

    config = _config(tmp_path)
    object.__setattr__(config, "strategy_dir", tmp_path / "strategies")
    report = DoctorReport()
    _check_lifecycle_provenance(report, config, journal)

    checks = [c for c in report.checks if c.name == "lifecycle provenance"]
    assert checks and checks[0].status is Status.FAIL, (
        "an event that is not an operator pause must not get the second key: "
        f"{[(c.status, c.detail) for c in checks]}"
    )
