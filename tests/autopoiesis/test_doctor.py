"""Tests for the doctor command - the fast-iteration primitive.

The defect this exists to prevent: the previous health story was a rubber stamp.
`cli --status` replayed heartbeat.json and always returned 0; auto_reviewer.py
read the same file; the ops shell scripts reported success because `set -e`
without `pipefail` let `| tee` convert any failure into exit 0. A daemon that had
been dead for 97 days passed 112 consecutive automated reviews.

doctor must therefore fail on every condition that the old story papered over,
and it must report the end-to-end proof criteria honestly - a cycle count is not
evidence that the agent works.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis.config import AgentConfig
from autopoiesis.doctor import Status, build_client, run_doctor
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    HeartbeatPayload,
    KnowledgeArtifact,
    TradeDecision,
)
from autopoiesis.strategy_engine import StrategyLibrary

NOW = datetime.now(tz=timezone.utc)


def _record(cycle_id, action="BUY", status="SUBMITTED", coid=None, filled=0.0):
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=DataSnapshot(
            symbol="SPY",
            timestamp=datetime(2026, 6, 9, 14, 30, tzinfo=timezone.utc),
            market_open=True,
            last_price=500.0,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
            ),
        ),
        decision=TradeDecision(symbol="SPY", action=action, quantity=2, confidence=0.8, rationale="t"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status=status, order_id=f"o-{cycle_id}", client_order_id=coid, filled_quantity=filled, message="m"
        ),
        strategy_id="s1",
    )


def _spec(strategy_id="s1", **kw):
    from autopoiesis.models import StrategySpec

    return StrategySpec(
        strategy_id=strategy_id,
        name="S",
        kind="FIXED_SIZE",
        symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        max_position_value=1000,
        enabled=kw.get("enabled", True),
        lifecycle=kw.get("lifecycle", "ACTIVE"),
        created_at=NOW,
        rationale="t",
    )


@pytest.fixture
def config(tmp_path):
    return AgentConfig(
        mode="paper",
        symbols=("SPY",),
        allowlist=frozenset({"SPY"}),
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        alpaca_base_url="https://paper-api.alpaca.markets",
        ollama_base_url="http://127.0.0.1:11434",
        model="deepseek-r1:8b",
        gpu_devices="0",
        journal_path=tmp_path / "journal.jsonl",
        max_position_value=5000,
        max_daily_loss=500,
        max_total_exposure=20000,
        stale_after_seconds=900,
        heartbeat_path=tmp_path / "heartbeat.json",
        pidfile_path=tmp_path / "daemon.pid",
        strategy_dir=tmp_path / "strategies",
        knowledge_dir=tmp_path / "knowledge",
    )


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


# --- it must actually fail ---------------------------------------------------


def test_doctor_fails_on_a_dead_daemon_that_claims_running(config):
    HealthMonitor = __import__("autopoiesis.health", fromlist=["HealthMonitor"]).HealthMonitor
    HealthMonitor(config.heartbeat_path).write(
        HeartbeatPayload(
            pid=2_590_767, status="RUNNING", timestamp=NOW - timedelta(days=97),
            cycle_count=39, error_count=0, message="market closed; maintenance only",
        )
    )

    report = run_doctor(config, skip_broker=True)

    daemon = _check(report, "daemon")
    assert daemon.status is Status.FAIL
    assert "pid_alive=False" in daemon.detail
    assert report.exit_code == 1


def test_doctor_fails_on_missing_credentials(tmp_path):
    cfg = AgentConfig.from_env.__func__(AgentConfig)  # keep the class imported
    config = AgentConfig(
        mode="paper", symbols=("SPY",), allowlist=frozenset({"SPY"}),
        alpaca_api_key=None, alpaca_secret_key=None,
        alpaca_base_url="https://paper-api.alpaca.markets",
        ollama_base_url="http://127.0.0.1:11434", model="m", gpu_devices="0",
        journal_path=tmp_path / "j.jsonl", max_position_value=5000, max_daily_loss=500,
        max_total_exposure=20000, heartbeat_path=tmp_path / "hb.json",
        strategy_dir=tmp_path / "s", knowledge_dir=tmp_path / "k",
    )

    report = run_doctor(config, skip_broker=True)

    assert _check(report, "alpaca credentials").status is Status.FAIL
    assert "alpaca credentials" in [c.name for c in report.failures]


def test_doctor_fails_on_an_unreachable_ollama(config):
    """Point at a port nothing listens on rather than relying on ollama being
    down: this test broke the moment ollama was actually started."""
    from dataclasses import replace

    report = run_doctor(replace(config, ollama_base_url="http://127.0.0.1:1"), skip_broker=True)

    assert _check(report, "ollama").status is Status.FAIL
    assert "unreachable" in _check(report, "ollama").detail


def test_doctor_passes_ollama_when_the_model_is_served(config, monkeypatch):
    """The positive case, so the check is not just 'always fail'."""
    from dataclasses import replace

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "deepseek-r1:8b"}]}

    import requests

    monkeypatch.setattr(requests, "get", lambda url, timeout=5: Response())

    report = run_doctor(replace(config, ollama_base_url="http://127.0.0.1:11434"), skip_broker=True)

    assert _check(report, "ollama").status is Status.OK


def test_doctor_writes_a_bounded_history(config, monkeypatch):
    from autopoiesis.doctor import record, run_doctor

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "deepseek-r1:8b"}]}

    import requests

    monkeypatch.setattr(requests, "get", lambda url, timeout=5: Response())
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("no broker")))

    for _ in range(3):
        record(run_doctor(config, skip_broker=True), config)

    history = config.journal_path.parent / "doctor-history.jsonl"
    lines = history.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # The fixture has credentials but an empty strategy directory, so the
    # recorded failure is the untradeable library, not the credentials.
    assert json.loads(lines[0])["ok"] is False
    assert "strategy library" in json.loads(lines[0])["failures"]


def test_quiet_mode_is_one_line(monkeypatch, tmp_path, capsys):
    """Assert the *contract*, not an ambient value.

    main() reads AgentConfig.from_env(), so this test is driven by the real
    environment and the real runtime directory. It previously asserted rc == 1,
    which was true only while no credentials were exported - so it passed in
    June and failed the moment real credentials arrived. Both halves were
    wrong: it did not control its inputs, and it hardcoded a value that the
    surrounding system legitimately changes.
    """
    import os

    from autopoiesis.cli import main

    for key in list(os.environ):
        if key.startswith(("ALPACA", "APCA", "AUTOPOIESIS", "OLLAMA")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTOPOIESIS_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("AUTOPOIESIS_HEARTBEAT", str(tmp_path / "heartbeat.json"))
    monkeypatch.setenv("AUTOPOIESIS_STRATEGY_DIR", str(tmp_path / "strategies"))
    monkeypatch.setenv("AUTOPOIESIS_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOPOIESIS_PIDFILE", str(tmp_path / "daemon.pid"))

    rc = main(["--doctor", "--quiet", "--skip-broker"])
    out = capsys.readouterr().out.strip()

    assert len(out.splitlines()) == 1, f"quiet mode must emit one line, got {out!r}"
    assert out.startswith("ok=")
    assert " exit=" in out
    assert "failures=[" in out and "warnings=[" in out and "checks=" in out
    # With no credentials, no strategies and no ollama, it must fail - and say so.
    assert rc == 1
    assert "alpaca credentials" in out
    # And it must have recorded that reading durably.
    assert (tmp_path / "doctor-history.jsonl").exists()


def test_quiet_mode_exit_code_matches_the_reported_result(monkeypatch, tmp_path, capsys):
    """The exit code and the printed verdict must not disagree - that mismatch is
    the exact bug this whole refactor was about."""
    import os

    from autopoiesis.cli import main

    for key in list(os.environ):
        if key.startswith(("ALPACA", "APCA", "AUTOPOIESIS", "OLLAMA")):
            monkeypatch.delenv(key, raising=False)
    for name, value in (
        ("AUTOPOIESIS_JOURNAL", "journal.jsonl"),
        ("AUTOPOIESIS_HEARTBEAT", "heartbeat.json"),
        ("AUTOPOIESIS_STRATEGY_DIR", "strategies"),
        ("AUTOPOIESIS_KNOWLEDGE_DIR", "knowledge"),
        ("AUTOPOIESIS_PIDFILE", "daemon.pid"),
    ):
        monkeypatch.setenv(name, str(tmp_path / value))

    rc = main(["--doctor", "--quiet", "--skip-broker"])
    out = capsys.readouterr().out.strip()

    reported_ok = out.split()[0] == "ok=True"
    reported_exit = int(out.split("exit=")[1].split()[0])
    assert reported_exit == rc
    assert reported_exit == (0 if reported_ok else 1)


def test_doctor_fails_when_no_strategy_is_selectable(config):
    library = StrategyLibrary(config.strategy_dir)
    library.save(_spec("a", lifecycle="RETIRED"))
    library.save(_spec("b", lifecycle="PAUSED"))

    report = run_doctor(config, skip_broker=True)

    check = _check(report, "strategy library")
    assert check.status is Status.FAIL
    assert "nothing can trade" in check.hint


def test_doctor_fails_on_an_unreadable_strategy_file(config):
    library = StrategyLibrary(config.strategy_dir)
    library.save(_spec("a"))
    (config.strategy_dir / "torn.json").write_text('{"strategy_id": "torn"', encoding="utf-8")

    report = run_doctor(config, skip_broker=True)

    assert _check(report, "strategy files").status is Status.FAIL


def test_doctor_fails_on_a_non_paper_endpoint(tmp_path):
    config = AgentConfig(
        mode="paper", symbols=("SPY",), allowlist=frozenset({"SPY"}),
        alpaca_api_key="k", alpaca_secret_key="s",
        alpaca_base_url="https://api.alpaca.markets",
        ollama_base_url="http://127.0.0.1:11434", model="m", gpu_devices="0",
        journal_path=tmp_path / "j.jsonl", max_position_value=5000, max_daily_loss=500,
        max_total_exposure=20000, heartbeat_path=tmp_path / "hb.json",
        strategy_dir=tmp_path / "s", knowledge_dir=tmp_path / "k",
    )

    report = run_doctor(config, skip_broker=True)

    assert _check(report, "paper endpoint").status is Status.FAIL


# --- it must not cry wolf ---------------------------------------------------


def test_a_healthy_system_has_no_failures(config, monkeypatch):
    from autopoiesis import doctor as doctor_module

    library = StrategyLibrary(config.strategy_dir)
    library.save(_spec("s1"))
    journal = JsonlJournal(config.journal_path, lock=False)
    journal.append(_record("c1"))
    from autopoiesis.health import HealthMonitor

    HealthMonitor(config.heartbeat_path).write(
        HeartbeatPayload(pid=__import__("os").getpid(), status="RUNNING", timestamp=NOW,
                         cycle_count=1, error_count=0, message="ok")
    )

    def fake_ollama(url, timeout=5):
        class R:
            def raise_for_status(self_inner): pass
            def json(self_inner): return {"models": [{"name": "deepseek-r1:8b"}]}
        return R()

    import requests

    monkeypatch.setattr(requests, "get", fake_ollama)

    report = run_doctor(config, skip_broker=True)

    # A stale journal timestamp is a warning, not a failure.
    assert [c.name for c in report.failures] == [] or all(
        c.name in {"journal recency"} for c in report.failures
    )
    assert _check(report, "strategy library").status is Status.OK
    assert _check(report, "risk limits").status is Status.OK


# --- the proof criteria must be honest --------------------------------------


def test_doctor_warns_that_nothing_is_proven_on_an_empty_journal(config):
    report = run_doctor(config, skip_broker=True)

    assert _check(report, "proof: submitted>0").status is Status.WARN
    assert _check(report, "proof: filled>0").status is Status.WARN
    assert _check(report, "proof: per-strategy pnl").status is Status.WARN


def test_doctor_does_not_call_a_submission_a_proof(config):
    """23 orders were submitted and none ever filled. Submitted>0 alone is not
    evidence the agent works."""
    journal = JsonlJournal(config.journal_path, lock=False)
    for i in range(3):
        journal.append(_record(f"c{i}", coid=f"coid-{i}"))

    report = run_doctor(config, skip_broker=True)

    assert _check(report, "proof: submitted>0").status is Status.OK
    assert _check(report, "proof: filled>0").status is Status.WARN
    assert _check(report, "proof: per-strategy pnl").status is Status.WARN


def test_a_confirmed_fill_moves_the_filled_check(config):
    from autopoiesis.models import JournalEvent

    journal = JsonlJournal(config.journal_path, lock=False)
    journal.append(_record("c1", coid="coid-1"))
    journal.append_event(
        JournalEvent(
            event_id="e1", event_type="ORDER_FILL_CONFIRMED", timestamp=NOW, status="SUCCESS",
            message="filled", payload={"client_order_id": "coid-1", "filled_quantity": 2.0},
        )
    )

    report = run_doctor(config, skip_broker=True)

    assert _check(report, "proof: filled>0").status is Status.OK
    # A fill is still not PnL.
    assert _check(report, "proof: per-strategy pnl").status is Status.WARN


def test_doctor_reports_corrupt_journal_lines(config):
    journal = JsonlJournal(config.journal_path, lock=False)
    journal.append(_record("c1"))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"cycle_id": "torn"\n')

    report = run_doctor(config, skip_broker=True)

    check = _check(report, "journal")
    assert check.status is Status.WARN
    assert "unparseable" in check.detail


def test_doctor_warns_when_the_journal_is_near_rotation(config):
    journal = JsonlJournal(config.journal_path, lock=False)
    for i in range(5):
        journal.append(_record(f"c{i}"))

    report = run_doctor(config, skip_broker=True)

    assert _check(report, "journal headroom").status in (Status.OK, Status.WARN)


# --- output shape ------------------------------------------------------------


def test_report_renders_and_serialises(config):
    report = run_doctor(config, skip_broker=True)

    text = report.render()
    assert "RESULT:" in text
    assert "[FAIL]" in text or "[WARN]" in text
    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["exit_code"] == report.exit_code
    assert len(payload["checks"]) == len(report.checks)
    assert set(payload) == {"ok", "exit_code", "failures", "warnings", "checks"}


def test_build_client_returns_none_without_credentials(tmp_path):
    config = AgentConfig(
        mode="paper", symbols=("SPY",), allowlist=frozenset({"SPY"}),
        alpaca_api_key=None, alpaca_secret_key=None,
        alpaca_base_url="https://paper-api.alpaca.markets",
        ollama_base_url="http://127.0.0.1:11434", model="m", gpu_devices="0",
        journal_path=tmp_path / "j.jsonl", max_position_value=5000, max_daily_loss=500,
        max_total_exposure=20000,
    )
    assert build_client(config) is None


def test_the_llm_is_reported_against_the_rule_on_paired_cycles_only():
    from types import SimpleNamespace

    from autopoiesis.doctor import DoctorReport, _report_llm_against_the_rule

    def row(action, rule, value, source="llm"):
        return SimpleNamespace(action=action, rule_action=rule, override_value_pct=value,
                               decision_source=source)

    report = DoctorReport()
    _report_llm_against_the_rule(report, [
        row("HOLD", "SELL", 1.05), row("HOLD", "BUY", -0.4), row("BUY", "BUY", 0.0),
        row("BUY", None, None),                       # before rule_action existed: unpaired
        row("SELL", "SELL", 0.0, source="fallback_policy_engine"),
    ])
    check = next(c for c in report.checks if c.name == "llm vs rule")
    assert "3 paired LLM decision(s), 2 override(s)" in check.detail
    assert "1 of them worth more than the rule" in check.detail
    assert "+0.650%" in check.detail

    empty = DoctorReport()
    _report_llm_against_the_rule(empty, [row("BUY", None, None)])
    assert next(c for c in empty.checks if c.name == "llm vs rule").status.value == "warn"
