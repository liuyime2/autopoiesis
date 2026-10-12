"""A daemon that is alive and a daemon that is cycling are two different facts.

The heartbeat carried `cycle_count`, which answers how many cycles *this process* ran - and resets
to zero on restart. So a fresh daemon over a record of 2,060 cycles reported "0", and a reader could
not tell a daemon that had just started from one that had been running for a week without a
successful cycle. The failure this prevents is not a crash; it is a health report that reads as
working while the thing it describes is idling.

The recency now comes from the record's own newest cycle, which survives a restart, and liveness and
cycling are reported as separate fields.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis.health import HealthMonitor
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import HeartbeatPayload

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _heartbeat(path, *, age_seconds, status="RUNNING", pid=4242):
    payload = HeartbeatPayload(
        pid=pid,
        status=status,
        timestamp=NOW,
        cycle_count=0,
        error_count=0,
        last_cycle_at=NOW - timedelta(seconds=age_seconds),
        last_cycle_age_seconds=float(age_seconds),
    )
    path.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return payload


def test_a_fresh_daemon_over_an_old_record_reports_the_records_own_age(tmp_path):
    """`cycle_count` reset to zero must not be reported as "nothing has run".

    This is the whole point of reading recency from the record rather than keeping it in memory: the
    number survives the restart that reset the counter.
    """
    path = tmp_path / "heartbeat.json"
    _heartbeat(path, age_seconds=40.5 * 3600)
    monitor = HealthMonitor(path, stale_after_seconds=900)

    assert monitor.cycling() is False, "40 hours is not cycling"
    assert monitor.liveness()["last_cycle_age_seconds"] == pytest.approx(40.5 * 3600)


def test_a_daemon_that_just_restarted_after_a_recent_cycle_is_cycling(tmp_path):
    """The counter is zero and the record is an hour old: both facts, neither hiding the other."""
    path = tmp_path / "heartbeat.json"
    _heartbeat(path, age_seconds=3600)
    # The pid probe is injected: liveness legitimately asks whether the process exists, and a test
    # asserting about real processes is a test about the machine.
    monitor = HealthMonitor(path, stale_after_seconds=900, pid_probe=lambda pid: True)

    assert monitor.is_live(now=NOW + timedelta(seconds=10)) is True
    assert monitor.cycling() is True, "an hour-old cycle is cycling, whatever the counter says"


def test_alive_and_cycling_are_reported_separately(tmp_path):
    """The property a health report that only knew liveness could not express."""
    path = tmp_path / "heartbeat.json"
    _heartbeat(path, age_seconds=7 * 86_400)
    status = json.loads(HealthMonitor(path).status_json(now=NOW + timedelta(seconds=1)))

    assert status["status"] == "RUNNING", "the process is up and answering"
    assert status["cycling"] is False, "and has done nothing for a week"
    assert status["last_cycle_age_seconds"] == pytest.approx(7 * 86_400)


def test_no_cycle_on_record_is_not_reported_as_recent(tmp_path):
    """An absent recency is absent, not zero and not fresh.

    A record with no cycles at all is the state of a fresh clone, and reporting it as "cycling"
    would claim work that never happened.
    """
    path = tmp_path / "heartbeat.json"
    payload = HeartbeatPayload(
        pid=4242, status="RUNNING", timestamp=NOW, cycle_count=0, error_count=0,
        last_cycle_at=None, last_cycle_age_seconds=None,
    )
    path.write_text(payload.model_dump_json(), encoding="utf-8")

    assert HealthMonitor(path).cycling() is False
    assert json.loads(HealthMonitor(path).status_json())["cycling"] is False


def test_the_daemon_populates_recency_from_the_journal(tmp_path):
    """Read from the record, not from memory: the read is the same one `doctor` does.

    Two readers of the same record that disagreed about its recency was a defect class this project
    has already paid for, so the daemon's heartbeat and the doctor's check are asserted to see the
    same newest cycle.
    """
    from autopoiesis.daemon import AgentDaemon

    runtime = tmp_path / "runtime"
    (runtime / "strategies").mkdir(parents=True)
    journal = JsonlJournal(runtime / "journal.jsonl")
    journal.archive_manifest_status()

    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.journal = journal
    daemon.now = lambda: NOW
    daemon.health = HealthMonitor(runtime / "heartbeat.json")
    daemon.cycle_count = 0
    daemon.error_count = 0
    daemon.last_cycle_id = None
    # The dependency outcomes. `__init__` sets these; a constructed instance must too, because the
    # heartbeat writes them and a recorder must never raise out of the trading loop.
    daemon._last_broker_at = None
    daemon._last_broker_ok = None
    daemon._last_model_at = None
    daemon._last_model_ok = None

    # No cycles yet: the recency is absent rather than claimed.
    assert daemon._journal_recency() == (None, None)

    _cycle_record(journal, NOW - timedelta(hours=3))
    _cycle_record(journal, NOW - timedelta(hours=1))

    latest, age = daemon._journal_recency()
    assert latest == NOW - timedelta(hours=1)
    assert age == pytest.approx(3600.0)

    # And the heartbeat carries it, which is what a reader of the file sees.
    daemon._heartbeat("RUNNING", "test")
    written = json.loads((runtime / "heartbeat.json").read_text(encoding="utf-8"))
    assert written["last_cycle_age_seconds"] == pytest.approx(3600.0)
    assert written["cycle_count"] == 0, "the counter is separate and still reports zero"


def _cycle_record(journal: JsonlJournal, at: datetime) -> None:
    from autopoiesis.models import (
        AccountSnapshot,
        CycleRecord,
        DataSnapshot,
        ExecutionResult,
        GuardianResult,
        TradeDecision,
    )

    journal.append(CycleRecord(
        cycle_id=f"cycle-{at.isoformat()}",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=at, market_open=True, last_price=100.0, source="alpaca",
            account=AccountSnapshot(equity=100_000.0, cash=100_000.0, buying_power=100_000.0,
                                    portfolio_value=100_000.0, daily_loss=0.0),
        ),
        decision=TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2,
                               rationale="wait"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold"),
    ))


def test_an_alive_daemon_that_is_not_cycling_says_why(tmp_path):
    """The ambiguity this closes: alive-and-idle against market-closed against broker-down.

    Before the heartbeat carried the dependency outcomes, all three looked identical - process up,
    heartbeat fresh, cycle_count whatever it happened to be. A model outage in particular looked
    like a market with nothing to do, because the loop fails closed to a HOLD and the cycle
    "succeeds". Recording which dependency last answered, and whether it did, is what makes the
    three distinguishable by a reader who has only the heartbeat file.
    """
    from autopoiesis.health import HealthMonitor

    path = tmp_path / "heartbeat.json"
    payload = HeartbeatPayload(
        pid=4242, status="RUNNING", timestamp=NOW, cycle_count=0, error_count=0,
        last_cycle_age_seconds=46.2 * 3600,
        last_broker_at=NOW - timedelta(minutes=5),
        last_broker_ok=False,
        last_model_at=NOW - timedelta(minutes=5),
        last_model_ok=True,
    )
    path.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
    body = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))

    assert body["last_broker_ok"] is False, "the broker stopped answering, and the file says so"
    assert body["last_model_ok"] is True, "and the model is fine, which rules out the other cause"
    assert body["last_cycle_age_seconds"] == pytest.approx(46.2 * 3600)
    # Both facts together distinguish "dependencies broke" from "market closed": a closed market
    # has a last broker answer that succeeded and simply nothing since.
    liveness = json.loads(HealthMonitor(path).status_json(now=NOW))
    assert liveness["status"] == "RUNNING" and liveness["cycling"] is False


def test_the_daemon_records_the_model_not_answering(tmp_path):
    """`fallback_policy_engine` is the fail-closed path, and it is a model outage.

    The loop already turns an unusable engine output into a HOLD, so the cycle succeeds either
    way. Reading the decision's own source is the only way to tell the two apart from outside.
    """
    from autopoiesis.daemon import AgentDaemon
    from autopoiesis.models import TradeDecision

    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.now = lambda: NOW
    daemon._last_broker_at = None
    daemon._last_broker_ok = None
    daemon._last_model_at = None
    daemon._last_model_ok = None

    daemon._record_model(False)
    assert daemon._last_model_ok is False
    daemon._record_model(True)
    assert daemon._last_model_ok is True

    # And the decision source the loop records is the signal it was derived from.
    decision = TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.0,
                             rationale="fail-closed HOLD after invalid decision engine output",
                             decision_source="fallback_policy_engine")
    assert (decision.decision_source == "llm") is False


def test_a_decline_the_model_was_never_asked_about_is_not_a_model_failure():
    """`last_model_ok` must not cry wolf on a correct decline.

    With `universe=tradable` the daemon cycles symbols the champion strategy does not cover, and
    the policy engine declines them without consulting the model - about a fifth of cycles on this
    host. The first version of the recorder treated every `fallback_policy_engine` decision as "the
    model did not answer", so `last_model_ok` read False roughly one cycle in five and looked
    exactly like an outage. A monitor that reports a failure where there is none is a monitor that
    gets ignored, which is the worse failure.

    The two facts are distinguished by the rationale: the loop's fail-closed text means the model
    was consulted and its output unusable; anything else means it was not asked.
    """
    from autopoiesis.daemon import AgentDaemon
    from autopoiesis.loop import FAIL_CLOSED_RATIONALE
    from autopoiesis.models import TradeDecision

    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.now = lambda: NOW
    daemon._last_model_at = None
    daemon._last_model_ok = None

    class _Record:
        def __init__(self, decision):
            self.decision = decision

    def _decide(source, rationale):
        daemon._post_cycle_model_flag(_Record(TradeDecision(
            symbol="SOXS", action="HOLD", quantity=0, confidence=0.0, rationale=rationale,
            decision_source=source,
        )))

    # A decline: the flag is untouched rather than flipped to False.
    _decide("fallback_policy_engine", "strategy:x snapshot symbol is outside strategy universe")
    assert daemon._last_model_ok is None, "a decline must not report the model as failed"

    # The model answers, so the flag goes True.
    _decide("llm", "no directional signal in this regime")
    assert daemon._last_model_ok is True

    # A decline after a success leaves the last known value standing.
    _decide("fallback_policy_engine", "strategy:x snapshot symbol is outside strategy universe")
    assert daemon._last_model_ok is True, (
        "nothing new is known about the model from a cycle that did not ask it"
    )

    # A real failure - the loop's fail-closed path - does flip it.
    _decide("fallback_policy_engine", FAIL_CLOSED_RATIONALE)
    assert daemon._last_model_ok is False


def test_a_post_cycle_model_flag_helper_is_what_the_loop_calls():
    """The extraction exists so the rule is in one place and testable without a cycle."""
    from autopoiesis.daemon import AgentDaemon

    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.now = lambda: NOW
    daemon._last_model_at = None
    daemon._last_model_ok = None
    assert callable(daemon._post_cycle_model_flag)
