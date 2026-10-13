import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autopoiesis.cli import _evidence_report, _stop_daemon, _verify_profit_target
from autopoiesis.config import AgentConfig
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import BrokerEvidenceBatch, JournalEvent, PortfolioHistoryPoint


def config(tmp_path):
    return AgentConfig(
        mode="paper",
        symbols=("SPY",),
        allowlist=frozenset({"SPY"}),
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        alpaca_base_url="https://paper-api.alpaca.markets",
        ollama_base_url="http://localhost:11434",
        model="deepseek-r1:8b",
        gpu_devices="0",
        journal_path=tmp_path / "journal.jsonl",
        max_position_value=1000,
        max_daily_loss=500,
        heartbeat_path=tmp_path / "heartbeat.json",
        pidfile_path=tmp_path / "daemon.pid",
        strategy_dir=tmp_path / "strategies",
    )


def test_stop_daemon_cleans_empty_pidfile(tmp_path):
    cfg = config(tmp_path)
    cfg.pidfile_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.pidfile_path.write_text("", encoding="utf-8")

    exit_code = _stop_daemon(cfg)

    assert exit_code == 0
    assert not cfg.pidfile_path.exists()


def test_stop_daemon_cleans_stale_pidfile(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.pidfile_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.pidfile_path.write_text("999999", encoding="utf-8")

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr("autopoiesis.cli.os.kill", fake_kill)

    exit_code = _stop_daemon(cfg)

    assert exit_code == 0
    assert not cfg.pidfile_path.exists()


def test_stop_daemon_sends_sigterm_and_cleans_after_exit(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.pidfile_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.pidfile_path.write_text("123", encoding="utf-8")
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if len(calls) > 1:
            raise ProcessLookupError

    monkeypatch.setattr("autopoiesis.cli.os.kill", fake_kill)

    exit_code = _stop_daemon(cfg)

    assert exit_code == 0
    assert calls[0][0] == 123
    assert not cfg.pidfile_path.exists()


def append_evidence(journal, *, return_pct=0.11):
    """Synthetic account-only history; no agent allocation or aligned equity."""
    batch = BrokerEvidenceBatch(
        generated_at=datetime.now(tz=timezone.utc),
        window_start=datetime(2026, 6, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        portfolio_history=(
            PortfolioHistoryPoint(
                timestamp=datetime(2026, 6, 10, 20, 0, tzinfo=timezone.utc),
                equity=111_000,
                profit_loss=11_000,
                profit_loss_pct=return_pct,
            ),
        ),
    )
    journal.append_event(
        JournalEvent(
            event_id="evidence-1",
            event_type="BROKER_EVIDENCE_INGESTED",
            timestamp=datetime.now(tz=timezone.utc),
            status="SUCCESS",
            message="success",
            payload=batch.model_dump(mode="json"),
        )
    )


def test_evidence_report_without_broker_evidence_is_unproven(tmp_path, capsys):
    cfg = config(tmp_path)

    exit_code = _evidence_report(cfg)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "missing_fill_price_and_broker_activity" in output


@pytest.mark.parametrize("return_pct", [None, 0.11, 0.02, -0.11, 2.0])
def test_synthetic_account_history_cannot_prove_agent_profit_target(tmp_path, capsys, return_pct):
    cfg = replace(config(tmp_path), profit_target_return_pct=1.25)
    if return_pct is not None:
        append_evidence(JsonlJournal(cfg.journal_path), return_pct=return_pct)
    assert JsonlJournal(cfg.journal_path).read_all() == []

    exit_code = _verify_profit_target(cfg)

    output = json.loads(capsys.readouterr().out)
    assert exit_code == output["exit_code"] == 2
    assert output["status"] == "unproven"
    assert output["comparison_status"] == "UNKNOWN"
    assert "agent allocation" in output["comparison_reason"]
    assert "aligned rolling equity" in output["comparison_reason"]
    assert output["satisfied"] is False
    assert output["target_daily_return_pct"] == 1.25
    assert output["account_return_pct"] == return_pct
    assert output["account_return_scope"] == "account_only_diagnostic"
    assert output["paper_only"] is True


@pytest.mark.parametrize(("returncode", "report", "collected"), [
    (2, {"exit_code": 2, "status": "unproven", "comparison_status": "UNKNOWN", "satisfied": False}, True),
    (1, "Traceback: synthetic exception", False),
    (1, "{malformed", False),
    (2, "{malformed", False),
    (2, {"exit_code": 1, "status": "unproven", "comparison_status": "UNKNOWN", "satisfied": False}, False),
    (0, {"exit_code": 2, "status": "unproven", "comparison_status": "UNKNOWN", "satisfied": False}, False),
    (2, {"exit_code": 2, "status": "satisfied", "comparison_status": "UNKNOWN", "satisfied": False}, False),
    (2, {"exit_code": 2, "status": "unproven", "comparison_status": "UNKNOWN", "satisfied": True}, False),
])
def test_make_evaluate_collects_only_matching_measurements(tmp_path, returncode, report, collected):
    """Synthetic subprocess reports exercise the real Make recipe without broker access."""
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "Makefile").write_text((root / "Makefile").read_text())
    runtime = tmp_path / "runtime" / "autopoiesis"
    runtime.mkdir(parents=True)
    (runtime / "journal.jsonl").touch()
    fake = tmp_path / "fake-command"
    output = json.dumps(report) if isinstance(report, dict) else report
    fake.write_text(
        f"#!{sys.executable}\nimport subprocess, sys\n"
        "if sys.argv[1:] == ['python', '-m', 'autopoiesis.cli', '--verify-profit-target']:\n"
        f"    print({output!r})\n    raise SystemExit({returncode})\n"
        "raise SystemExit(subprocess.call([sys.executable, *sys.argv[2:]]))\n"
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["make", "--no-print-directory", "evaluate", "ENVFILE=/dev/null", f"CONDA_RUN={fake}"],
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert (result.returncode == 0) is collected, result.stdout + result.stderr
    if collected:
        assert "UNKNOWN" in result.stdout
        assert "target met" not in result.stdout


def test_make_evaluate_uses_workspace_source_over_foreign_pythonpath(tmp_path):
    """Synthetic empty journal and foreign package exercise the actual CLI entry point."""
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "Makefile").write_text((root / "Makefile").read_text())
    (tmp_path / "src").symlink_to(root / "src", target_is_directory=True)
    runtime = tmp_path / "runtime" / "autopoiesis"
    runtime.mkdir(parents=True)
    journal = runtime / "journal.jsonl"
    journal.touch()
    foreign = tmp_path / "foreign" / "autopoiesis"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").touch()
    marker = tmp_path / "foreign-cli-ran"
    (foreign / "cli.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "raise RuntimeError('synthetic foreign CLI must not be selected')\n"
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AUTOPOIESIS_", "ALPACA_", "APCA_"))}
    env.update({
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONPATH": str(foreign.parent),
        "AUTOPOIESIS_JOURNAL": str(journal),
        "AUTOPOIESIS_MODE": "paper",
    })
    result = subprocess.run(
        ["make", "--no-print-directory", "evaluate", "ENVFILE=/dev/null", "CONDA_BIN=", "CONDA_RUN="],
        cwd=tmp_path, env=env, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), "the entry point must bypass the foreign package"
    report = json.loads((runtime / "evaluate.txt").read_text())
    assert report["exit_code"] == 2
    assert report["comparison_status"] == "UNKNOWN"
    assert report["status"] == "unproven"
    assert report["satisfied"] is False
    assert report["account_return_scope"] == "account_only_diagnostic"
    assert journal.read_text() == "", "the read-only CLI must leave the synthetic journal empty"


def test_help_renders_without_crashing(capsys):
    """`--help` must work, and argparse makes that easy to break.

    argparse applies `%`-formatting to any help string containing a percent sign, so
    `"the 10% daily target"` parsed as a `% d` conversion and raised
    `TypeError: %d format: a real number is required, not dict` on every invocation
    that printed usage. Nothing in the suite invoked `--help`, so it stayed broken
    for as long as the string existed.

    The parser is constructed inside `main()`, so this drives the real entry point and
    asserts on its output rather than reaching for an internal builder that does not
    exist.
    """
    from autopoiesis import cli

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0

    out = capsys.readouterr().out
    assert "usage:" in out
    # The flags an operator needs to discover the system.
    for flag in ("--daemon", "--status", "--doctor", "--once"):
        assert flag in out, f"{flag} missing from --help output"
    assert "configured" in out
    assert "10 percent" not in out


def test_archive_baseline_attestation_requires_and_records_operator_evidence(tmp_path, monkeypatch, capsys):
    from autopoiesis import cli

    cfg = config(tmp_path)
    cfg.journal_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.journal_path.write_text('{"malformed":\n', encoding="utf-8")
    # Existing malformed history cannot be attested as a trusted baseline.
    monkeypatch.setattr(cli.AgentConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setenv("AUTOPOIESIS_OPERATOR_ID", "operator-1")
    monkeypatch.setenv("AUTOPOIESIS_EVIDENCE_REFERENCE", "ticket-123")
    monkeypatch.setenv("AUTOPOIESIS_ATTESTATION_REASON", "reviewed archive evidence")

    assert cli.main(["--attest-archive-baseline"]) == 1
    assert "refused" in capsys.readouterr().out
    assert not JsonlJournal(cfg.journal_path).archive_manifest_path.exists()


def test_archive_baseline_attestation_accepts_complete_existing_history(tmp_path, monkeypatch, capsys):
    from autopoiesis import cli

    cfg = config(tmp_path)
    journal = JsonlJournal(cfg.journal_path)
    journal.append_event(JournalEvent(
        event_id="baseline-event",
        event_type="REFLECTION_FAILED",
        timestamp=datetime.now(tz=timezone.utc),
        status="FAILED",
        message="baseline evidence",
    ))
    monkeypatch.setattr(cli.AgentConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setenv("AUTOPOIESIS_OPERATOR_ID", "operator-1")
    monkeypatch.setenv("AUTOPOIESIS_EVIDENCE_REFERENCE", "ticket-123")
    monkeypatch.setenv("AUTOPOIESIS_ATTESTATION_REASON", "reviewed archive evidence")

    assert cli.main(["--attest-archive-baseline"]) == 0
    assert JsonlJournal(cfg.journal_path).archive_manifest_status()["trusted"] is True
    assert "trusted only from this recorded baseline forward" in capsys.readouterr().out


def test_ambiguous_order_runbook_contains_fail_closed_operator_steps():
    from pathlib import Path

    runbook = Path(__file__).resolve().parents[2] / "docs/runbooks/ambiguous-order-recovery.md"
    content = runbook.read_text(encoding="utf-8")
    for required in (
        "not proof that the POST was rejected",
        "ORDER_SUBMISSION_INTENT.timestamp",
        "ORDER_SUBMISSION_RESOLVED",
        "ORDER_FILL_SNAPSHOT",
        "Do not manually clear the intent",
        "operator identity, timestamp, evidence reference",
    ):
        assert required in content


def _synthetic_entry_config(tmp_path, **overrides):
    """Synthetic paper configuration; every writable path is isolated from deployment."""
    return replace(
        config(tmp_path), knowledge_dir=tmp_path / "knowledge",
        curriculum_enabled=False, **overrides,
    )


@pytest.fixture
def entry_no_network(monkeypatch):
    """Synthetic entry tests must fail if any real broker or model HTTP call is attempted."""
    def forbidden_request(*args, **kwargs):
        pytest.fail("synthetic entry fixture attempted external HTTP")

    monkeypatch.setattr("requests.Session.request", forbidden_request)


class _SyntheticClosedBroker:
    """Synthetic closed clock and empty order list; no network or submissions."""

    def __init__(self):
        self.calls = []

    def get_clock(self):
        from types import SimpleNamespace

        self.calls.append("clock")
        return SimpleNamespace(is_open=False)

    def get_orders(self, **kwargs):
        self.calls.append("orders")
        return []

    def submit_order(self, **kwargs):
        self.calls.append("submit")
        pytest.fail("a closed or competing entry must never submit an order")


def test_once_delegates_to_bounded_daemon_and_preserves_exit_code(tmp_path, monkeypatch, entry_no_network):
    """Synthetic delegate isolates the public one-cycle lifecycle contract."""
    from autopoiesis import cli

    cfg = _synthetic_entry_config(tmp_path)
    calls = []

    def run_daemon(received, *, max_cycles):
        calls.append((received, max_cycles))
        return 7

    monkeypatch.setattr(cli, "_run_daemon", run_daemon)
    monkeypatch.setattr(
        "autopoiesis.broker_client.create_alpaca_client",
        lambda **kwargs: _SyntheticClosedBroker(),
    )

    assert cli._run_once(cfg) == 7
    assert calls == [(cfg, 1)]


@pytest.mark.parametrize(("confidence", "trades_today", "exposure_cap", "reason"), [
    (0.7, 0, 2000, "decision confidence below minimum"),
    (0.95, 3, 2000, "max trades per day reached"),
    (0.95, 0, 50, "exposure"),
])
def test_shared_builder_enforces_configured_risk_limits(
    tmp_path, entry_no_network, confidence, trades_today, exposure_cap, reason,
):
    """Synthetic fresh snapshot tests effective Guardian refusals, without execution."""
    from autopoiesis import cli
    from autopoiesis.llm_decision import HybridDecisionEngine
    from autopoiesis.models import AccountSnapshot, DataSnapshot, TradeDecision

    cfg = _synthetic_entry_config(
        tmp_path, min_confidence=0.9, max_trades_per_day=3,
        max_total_exposure=exposure_cap, max_account_value=100_000,
        shorts="shadow", universe="account", manage_account=True,
    )
    assert callable(getattr(cli, "_build_agent_daemon", None)), "shared builder is missing"
    client = _SyntheticClosedBroker()
    daemon = cli._build_agent_daemon(cfg, client)
    snapshot = DataSnapshot(
        symbol="SPY", timestamp=datetime.now(timezone.utc), market_open=True,
        # The schema accepts production source names; these fixture values are synthetic.
        last_price=100, source="alpaca",
        account=AccountSnapshot(equity=10_000, cash=10_000, buying_power=10_000,
                                portfolio_value=10_000, daily_loss=0),
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1,
                             confidence=confidence, rationale="synthetic risk regression")

    result = daemon.loop.guardian.review(decision, snapshot, trades_today=trades_today)

    assert not result.approved
    assert reason in result.reason
    assert daemon.loop.guardian.shorts == "shadow"
    assert daemon.loop.guardian.universe == "account"
    assert daemon.loop.manage_account is True
    assert isinstance(daemon.loop.decision_engine, HybridDecisionEngine)
    assert daemon.loop.decision_engine.risk_limits["manage_account"] is True
    assert daemon.loop.decision_engine.risk_limits["max_total_exposure"] == exposure_cap
    assert daemon.startup_reconciler.client is client
    assert daemon.scheduler.clock_provider == client.get_clock
    assert client.calls == [], "construction must not contact the broker"
    assert daemon.journal.read_all() == []
    assert daemon.journal.read_events() == []
    assert not cfg.pidfile_path.exists()
    assert not cfg.heartbeat_path.exists()
    assert not cfg.strategy_dir.exists()
    assert not cfg.knowledge_dir.exists()


def test_once_rejects_active_daemon_pid_before_broker_or_loop(tmp_path, monkeypatch, entry_no_network):
    """Synthetic PID collision exercises the real exclusive-writer entry boundary."""
    from autopoiesis import cli
    from autopoiesis.daemon import DaemonAlreadyRunningError

    cfg = _synthetic_entry_config(tmp_path)
    owner = str(os.getpid())
    cfg.pidfile_path.write_text(owner)
    client = _SyntheticClosedBroker()
    monkeypatch.setattr("autopoiesis.broker_client.create_alpaca_client", lambda **kwargs: client)

    with pytest.raises(DaemonAlreadyRunningError):
        cli._run_once(cfg)

    assert cfg.pidfile_path.read_text() == owner
    assert client.calls == []
    assert not cfg.heartbeat_path.exists()
    assert JsonlJournal(cfg.journal_path).read_all() == []
    assert JsonlJournal(cfg.journal_path).read_events() == []


def test_once_closed_scheduler_reconciles_then_exits_without_cycle_or_order(tmp_path, monkeypatch, entry_no_network):
    """Synthetic closed broker clock drives the real scheduler and daemon entry path."""
    from autopoiesis import cli
    from autopoiesis.health import HealthMonitor

    cfg = _synthetic_entry_config(tmp_path)
    client = _SyntheticClosedBroker()
    monkeypatch.setattr("autopoiesis.broker_client.create_alpaca_client", lambda **kwargs: client)
    # Maintenance is covered by daemon tests. Isolate it here from broker evidence/model calls.
    monkeypatch.setattr(cli.AgentDaemon, "_maintenance", lambda self: None)

    assert cli._run_once(cfg) == 0

    assert client.calls == ["orders", "clock"]
    journal = JsonlJournal(cfg.journal_path)
    assert journal.read_all() == []
    assert [event.event_type for event in journal.read_events()] == ["ORDER_RECONCILIATION_REVIEWED"]
    heartbeat = HealthMonitor(cfg.heartbeat_path).read()
    assert heartbeat.status == "STOPPED"
    assert heartbeat.cycle_count == 0
    assert not cfg.pidfile_path.exists()


def test_make_operator_help_matches_checked_commands(tmp_path):
    """Copied Makefile dry runs exercise operator entry points without service changes."""
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "Makefile").write_text((root / "Makefile").read_text())
    result = subprocess.run(
        ["make", "--no-print-directory", "help", "ENVFILE=/dev/null"],
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "reports without gating" not in result.stdout
    assert "lint + type + test" in result.stdout
    assert "read-only broker" in result.stdout
    assert "make install-service" in result.stdout
    dry_run = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", "install-service", "ENVFILE=/dev/null"],
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert dry_run.stdout.strip() == "./autopoiesis install-service"


def readiness_tool():
    """Load the operator tool without installing tools as a production package."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "tools" / "market_readiness.py"
    assert path.exists(), "read-only market collector is missing"
    spec = importlib.util.spec_from_file_location("market_readiness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_readiness(tmp_path, monkeypatch, *, opened=False):
    """Synthetic broker/process/model only; never evidence about live market behavior."""
    from datetime import time
    from types import SimpleNamespace

    from autopoiesis.health import HealthMonitor
    from autopoiesis.models import HeartbeatPayload, StrategySpec

    def no_http(*args, **kwargs):
        pytest.fail("synthetic readiness fixture attempted external HTTP")

    monkeypatch.setattr("requests.sessions.Session.request", no_http)
    tool = readiness_tool()
    now = datetime(2026, 10, 13, 14 if opened else 12, 45, tzinfo=timezone.utc)
    cfg = replace(config(tmp_path), knowledge_dir=tmp_path / "knowledge")
    cfg.journal_path.touch()
    cfg.pidfile_path.write_text("123")
    JsonlJournal(cfg.journal_path).attest_archive_baseline(
        operator_id="synthetic-test", evidence_reference="synthetic-fixture", reason="synthetic empty history",
    )
    heartbeat = HeartbeatPayload(
        pid=123, status="RUNNING", timestamp=now, cycle_count=7, error_count=0,
        source_fingerprint="a" * 16,
    )
    cfg.heartbeat_path.write_text(heartbeat.model_dump_json())
    cfg.strategy_dir.mkdir()
    strategy = StrategySpec(
        strategy_id="synthetic-hold", name="Synthetic hold", kind="FIXED_SIZE",
        symbols=("SPY",), parameters={"action": "HOLD", "quantity": 0, "confidence": 0.9},
        max_position_value=1000, created_at=now, rationale="synthetic fixture",
    )
    (cfg.strategy_dir / "synthetic.json").write_text(strategy.model_dump_json())
    environment = {
        "AUTOPOIESIS_MODE": "paper", "AUTOPOIESIS_SYMBOLS": "SPY", "AUTOPOIESIS_ALLOWLIST": "SPY",
        "ALPACA_API_KEY": "synthetic-key", "ALPACA_SECRET_KEY": "synthetic-secret",
        "AUTOPOIESIS_JOURNAL": str(cfg.journal_path), "AUTOPOIESIS_HEARTBEAT": str(cfg.heartbeat_path),
        "AUTOPOIESIS_PIDFILE": str(cfg.pidfile_path), "AUTOPOIESIS_STRATEGY_DIR": str(cfg.strategy_dir),
        "AUTOPOIESIS_KNOWLEDGE_DIR": str(cfg.knowledge_dir), "AUTOPOIESIS_MAX_POSITION_VALUE": "1000",
    }
    clock = SimpleNamespace(timestamp=now, is_open=opened)
    account = SimpleNamespace(
        status="ACTIVE", trading_blocked=False, account_blocked=False, trade_suspended_by_user=False,
        equity="10000", portfolio_value="10000", last_equity="10000", cash="10000", buying_power="10000",
    )
    client = SimpleNamespace(
        get_clock=lambda: clock,
        get_calendar=lambda **kwargs: [SimpleNamespace(date=now.date(), open=time(9, 30), close=time(16))],
        get_account=lambda: account, list_positions=list, list_orders=lambda **kwargs: [],
        get_latest_trade=lambda symbol: SimpleNamespace(price=100, timestamp=now),
    )
    calls = []
    original_builder = tool._build_agent_daemon

    def forbidden(*args, **kwargs):
        raise AssertionError("readiness attempted forbidden mutation")

    def build(actual, broker, *, journal=None):
        daemon = original_builder(actual, broker, journal=journal)
        calls.append(daemon)
        for name in ("run", "_maintenance", "_round_symbols", "_startup_reconcile", "_post_cycle"):
            setattr(daemon, name, forbidden)
        daemon.loop.run_once = forbidden
        daemon.loop.executor.execute = forbidden
        daemon.journal.append = forbidden
        daemon.journal.append_event = forbidden
        daemon.health.write = forbidden
        engine = daemon.loop.decision_engine
        engine.risk_provider = lambda symbol: None
        engine.instrument_provider = lambda symbol: None
        engine.news_provider = lambda symbol: ()
        engine.llm.transport = lambda *args: {"response": json.dumps({
            "symbol": "SPY", "action": "HOLD", "quantity": 0, "confidence": 0.9,
            "rationale": "synthetic rationale must never be published", "hold_reason": "no_signal",
        })}
        return daemon

    monkeypatch.setattr(tool, "_now", lambda: now)
    monkeypatch.setattr(tool, "source_fingerprint", lambda: "a" * 16)
    monkeypatch.setattr(tool, "HealthMonitor", lambda path, **kwargs: HealthMonitor(path, pid_probe=lambda pid: True, **kwargs))
    monkeypatch.setattr(tool, "_process_environment", lambda pid: (environment, tmp_path))
    monkeypatch.setattr(tool, "create_alpaca_client", lambda **kwargs: client)
    monkeypatch.setattr(tool, "_build_agent_daemon", build)
    return SimpleNamespace(
        tool=tool, cfg=cfg, now=now, heartbeat=heartbeat, environment=environment,
        clock=clock, account=account, client=client, calls=calls, strategy=strategy,
    )


def collect_synthetic(fixture, *, opened=False):
    return fixture.tool.collect(
        phase="open" if opened else "preopen", expected_fingerprint="a" * 16,
        session_date=fixture.now.date(), after=fixture.now.replace(hour=13, minute=30) if opened else None,
        bootstrap=fixture.cfg,
    )


@pytest.mark.parametrize("refusal", [
    "nonpaper", "endpoint", "credentials", "source", "imported-source", "stale", "dead",
    "pidfile", "manifest-missing", "lock-missing", "untrusted", "corrupt", "unresolved",
])
def test_readiness_synthetic_refusals_precede_builder(tmp_path, monkeypatch, refusal):
    fixture = synthetic_readiness(tmp_path, monkeypatch)
    tool = fixture.tool
    if refusal == "nonpaper":
        fixture.environment["AUTOPOIESIS_MODE"] = "replay"
    elif refusal == "endpoint":
        fixture.environment["ALPACA_BASE_URL"] = "https://api.alpaca.markets"
    elif refusal == "credentials":
        fixture.environment.pop("ALPACA_SECRET_KEY")
    elif refusal in {"source", "stale"}:
        update = {"source_fingerprint": "b" * 16} if refusal == "source" else {"timestamp": fixture.now.replace(day=12)}
        fixture.cfg.heartbeat_path.write_text(fixture.heartbeat.model_copy(update=update).model_dump_json())
    elif refusal == "imported-source":
        monkeypatch.setattr(tool, "source_fingerprint", lambda: "b" * 16)
    elif refusal == "dead":
        from autopoiesis.health import HealthMonitor
        monkeypatch.setattr(tool, "HealthMonitor", lambda path, **kwargs: HealthMonitor(path, pid_probe=lambda pid: False, **kwargs))
    elif refusal == "pidfile":
        fixture.cfg.pidfile_path.write_text("124")
    elif refusal in {"manifest-missing", "untrusted"}:
        manifest = JsonlJournal(fixture.cfg.journal_path).archive_manifest_path
        if refusal == "manifest-missing":
            manifest.unlink()
        else:
            payload = json.loads(manifest.read_text())
            payload["baseline_attestation"] = None
            manifest.write_text(json.dumps(payload))
    elif refusal == "lock-missing":
        fixture.cfg.journal_path.with_suffix(".jsonl.lock").unlink()
    elif refusal == "corrupt":
        fixture.cfg.journal_path.write_text('{"execution": garbage}\n')
    elif refusal == "unresolved":
        JsonlJournal(fixture.cfg.journal_path).append_event(JournalEvent(
            event_id="synthetic-intent", event_type="ORDER_SUBMISSION_INTENT", timestamp=fixture.now,
            status="SUCCESS", payload={"client_order_id": "synthetic-client"},
        ))
    report = collect_synthetic(fixture)
    assert report["exit_code"] == 1, report
    assert report["status"] == "FAIL"
    assert not fixture.calls
    assert "synthetic-secret" not in json.dumps(report)


def test_readiness_synthetic_preopen_uses_real_hybrid_and_writes_only_private_report(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = collect_synthetic(fixture)
    assert report["status"] == "READY", report
    assert report["exit_code"] == 0
    assert report["preview"]["model_response_observed"] is True
    assert report["preview"]["symbol"] == "SPY"
    assert report["scheduler_trade_now"] is False
    assert "synthetic rationale" not in json.dumps(report)
    output = tmp_path / "readiness" / "synthetic-report.json"
    fixture.tool.write_report(output, report)
    assert output.stat().st_mode & 0o777 == 0o600
    assert {path: path.read_bytes() for path in before} == before
    assert {path for path in tmp_path.rglob("*") if path.is_file()} == set(before) | {output}


def synthetic_session_record(fixture, *, provenance=True, symbol="SPY"):
    from autopoiesis.learning_evidence import capture
    from autopoiesis.models import (
        AccountSnapshot,
        CycleRecord,
        DataSnapshot,
        ExecutionResult,
        GuardianResult,
        TradeDecision,
    )

    return CycleRecord(
        cycle_id="synthetic-session", strategy_id="synthetic-hold",
        snapshot=DataSnapshot(symbol=symbol, timestamp=fixture.now, quote_time=fixture.now,
            market_open=True, last_price=100, source="alpaca", account=AccountSnapshot(
                equity=10000, cash=10000, buying_power=10000, portfolio_value=10000, daily_loss=0,
            )),
        decision=TradeDecision(symbol=symbol, action="BUY" if not provenance else "HOLD",
            quantity=1 if not provenance else 0, confidence=0.9, rationale="synthetic",
            strategy_id="synthetic-hold", strategy_provenance=capture(fixture.strategy) if provenance else None),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="synthetic"),
    )


def test_readiness_synthetic_open_accepts_attributed_hold_without_fills(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    JsonlJournal(fixture.cfg.journal_path).append(synthetic_session_record(fixture))
    report = collect_synthetic(fixture, opened=True)
    assert report["status"] == "VERIFIED", report
    assert report["session"]["filled_quantity"] == 0
    assert report["profitability"] == "UNPROVEN"


@pytest.mark.parametrize("failure", ["invalid-actionable-provenance", "missing-coverage", "stale-quote", "cycle-error"])
def test_readiness_synthetic_open_refuses_incomplete_or_invalid_session(tmp_path, monkeypatch, failure):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    record = synthetic_session_record(fixture, provenance=failure != "invalid-actionable-provenance")
    if failure == "missing-coverage":
        fixture.environment["AUTOPOIESIS_SYMBOLS"] = "SPY,QQQ"
    elif failure == "stale-quote":
        record = record.model_copy(update={"snapshot": record.snapshot.model_copy(update={"quote_time": fixture.now.replace(day=12)})})
    elif failure == "cycle-error":
        record = record.model_copy(update={"error": "synthetic secret exception"})
    JsonlJournal(fixture.cfg.journal_path).append(record)
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 1, report
    assert "synthetic secret exception" not in json.dumps(report)


def test_readiness_synthetic_open_before_first_round_is_wait(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    fixture.now = fixture.now.replace(hour=13, minute=45)
    fixture.clock.timestamp = fixture.now
    monkeypatch.setattr(fixture.tool, "_now", lambda: fixture.now)
    fixture.cfg.heartbeat_path.write_text(fixture.heartbeat.model_copy(update={"timestamp": fixture.now}).model_dump_json())
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 2, report
    assert report["status"] == "WAIT"


@pytest.mark.parametrize("variant", ["matched", "missing", "wrong-order", "wrong-action", "unapproved", "duplicate"])
def test_readiness_synthetic_submission_requires_durable_matching_identity(tmp_path, monkeypatch, variant):
    from autopoiesis.models import ExecutionResult, GuardianResult

    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    record = synthetic_session_record(fixture)
    record = record.model_copy(update={
        "decision": record.decision.model_copy(update={"action": "BUY", "quantity": 1}),
        "execution": ExecutionResult(status="SUBMITTED", order_id="synthetic-order", client_order_id="synthetic-client",
            filled_quantity=0, message="synthetic pending order"),
    })
    if variant == "unapproved":
        record = record.model_copy(update={"guardian": GuardianResult(approved=False, reason="synthetic risk refusal")})
    journal = JsonlJournal(fixture.cfg.journal_path)
    journal.append(record)
    if variant != "missing":
        for kind in ("ORDER_SUBMISSION_INTENT", "ORDER_SUBMISSION_ACKNOWLEDGED"):
            journal.append_event(JournalEvent(
                event_id=f"synthetic-{kind}", event_type=kind, timestamp=fixture.now, status="SUCCESS",
                cycle_id=record.cycle_id, strategy_id=record.strategy_id,
                payload={"client_order_id": "synthetic-client", "order_id": "wrong" if variant == "wrong-order" else "synthetic-order",
                    "symbol": "SPY", "action": "SELL" if variant == "wrong-action" else "BUY", "quantity": 1},
            ))
    if variant == "duplicate":
        journal.append(record.model_copy(update={"cycle_id": "synthetic-second"}))
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == (0 if variant == "matched" else 1), report
    if variant == "matched":
        assert report["session"]["submitted_orders"] == 1
        assert report["session"]["pending_fill_observations"] == 1


@pytest.mark.parametrize("change", ["phase", "source", "pid"])
def test_readiness_synthetic_changes_after_slow_preview_require_new_observation(tmp_path, monkeypatch, change):
    fixture = synthetic_readiness(tmp_path, monkeypatch)
    original = fixture.tool._build_agent_daemon

    def build(cfg, client, *, journal=None):
        daemon = original(cfg, client, journal=journal)
        transport = daemon.loop.decision_engine.llm.transport

        def changed(*args):
            result = transport(*args)
            if change == "phase":
                fixture.clock.is_open = True
            else:
                update = {"source_fingerprint": "b" * 16} if change == "source" else {"pid": 124}
                cfg.heartbeat_path.write_text(fixture.heartbeat.model_copy(update=update).model_dump_json())
            return result

        daemon.loop.decision_engine.llm.transport = changed
        return daemon

    monkeypatch.setattr(fixture.tool, "_build_agent_daemon", build)
    report = collect_synthetic(fixture)
    assert report["exit_code"] == 2, report
    assert report["status"] == "WAIT"


def test_readiness_synthetic_actionable_skipped_is_explicitly_unexecuted(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    record = synthetic_session_record(fixture)
    record = record.model_copy(update={"decision": record.decision.model_copy(update={"action": "BUY", "quantity": 1})})
    JsonlJournal(fixture.cfg.journal_path).append(record)
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 0, report
    assert report["session"]["actionable_unexecuted"] == 1
    assert report["session"]["submitted_orders"] == 0


def test_readiness_builder_accepts_existing_readonly_journal(tmp_path):
    """Synthetic construction seam avoids create-capable locks in observer helpers."""
    from autopoiesis.cli import _build_agent_daemon

    cfg = config(tmp_path)
    cfg.journal_path.touch()
    journal = JsonlJournal(cfg.journal_path, lock=False)
    from types import SimpleNamespace
    daemon = _build_agent_daemon(cfg, SimpleNamespace(get_clock=lambda: None), journal=journal)
    assert daemon.journal is journal
    assert daemon.loop.journal is journal
    assert daemon.loop.trade_counter.journal is journal


def test_readiness_builder_refuses_foreign_journal_before_construction(tmp_path):
    from autopoiesis.cli import _build_agent_daemon

    cfg = config(tmp_path)
    journal = JsonlJournal(tmp_path / "foreign.jsonl", lock=False)
    with pytest.raises(ValueError, match="journal path"):
        _build_agent_daemon(cfg, object(), journal=journal)


@pytest.mark.parametrize("condition", ["holiday", "blocked", "orders-incomplete", "stale-clock", "model-failure"])
def test_readiness_synthetic_broker_or_model_failure_never_certifies(tmp_path, monkeypatch, condition):
    fixture = synthetic_readiness(tmp_path, monkeypatch)
    if condition == "holiday":
        fixture.client.get_calendar = lambda **kwargs: []
    elif condition == "blocked":
        fixture.account.trading_blocked = True
    elif condition == "orders-incomplete":
        fixture.client.list_orders = lambda **kwargs: [object()] * 500
    elif condition == "stale-clock":
        fixture.clock.timestamp = fixture.now.replace(day=12)
    else:
        builder = fixture.tool._build_agent_daemon

        def failing_builder(cfg, client, *, journal=None):
            daemon = builder(cfg, client, journal=journal)

            def failed(*args):
                raise RuntimeError("synthetic-secret must remain private")

            daemon.loop.decision_engine.llm.transport = failed
            return daemon

        monkeypatch.setattr(fixture.tool, "_build_agent_daemon", failing_builder)
    report = collect_synthetic(fixture)
    assert report["exit_code"] == (2 if condition == "holiday" else 1), report
    assert "synthetic-secret" not in json.dumps(report)


def test_readiness_synthetic_actual_process_paths_and_limits_win(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch)
    for name in ("AUTOPOIESIS_JOURNAL", "AUTOPOIESIS_HEARTBEAT", "AUTOPOIESIS_PIDFILE", "AUTOPOIESIS_STRATEGY_DIR", "AUTOPOIESIS_KNOWLEDGE_DIR"):
        fixture.environment[name] = str(Path(fixture.environment[name]).relative_to(tmp_path))
    fixture.environment["AUTOPOIESIS_MAX_TRADES_PER_DAY"] = "3"
    report = collect_synthetic(fixture)
    assert report["exit_code"] == 0, report
    assert fixture.calls[0].config.journal_path == fixture.cfg.journal_path
    assert fixture.calls[0].loop.guardian.max_trades_per_day == 3
    assert report["config"]["max_trades_per_day"] == 3


def test_make_readiness_targets_forward_arguments_without_shell_evaluation(tmp_path):
    """Synthetic fake entry command observes Make delegation; no broker/model or timers."""
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "Makefile").write_text((root / "Makefile").read_text())
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "market_readiness.py").write_text(
        "import json, sys\ndef main(args):\n print(json.dumps(args)); return 2\n"
    )
    command = tmp_path / "python-runner"
    command.write_text(f"#!{sys.executable}\nimport subprocess,sys\nraise SystemExit(subprocess.call([sys.executable,*sys.argv[2:]]))\n")
    command.chmod(0o755)
    result = subprocess.run([
        "make", "--no-print-directory", "market-open-verify", "ENVFILE=/dev/null", f"CONDA_RUN={command}",
        "EXPECTED_FINGERPRINT=aaaaaaaaaaaaaaaa", "DEPLOYMENT_AFTER=2026-10-13T13:30:00+00:00",
        "DATE=2026-10-13", "READINESS_OUTPUT=/tmp/synthetic-`touch SHOULD-NOT-EXIST`.json",
    ], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert '--phase", "open"' in result.stdout
    assert "--after" in result.stdout
    assert "WAIT" not in result.stdout  # Make preserves tool status rather than forging a verdict.
    assert not (tmp_path / "SHOULD-NOT-EXIST").exists()


def test_readiness_synthetic_bootstrap_only_locates_heartbeat(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch)
    fixture.environment["AUTOPOIESIS_STALE_AFTER_SECONDS"] = "1800"
    old = fixture.heartbeat.model_copy(update={"timestamp": fixture.now.replace(minute=25)})
    fixture.cfg.heartbeat_path.write_text(old.model_dump_json())
    report = collect_synthetic(fixture)
    assert report["exit_code"] == 0, report
    assert report["config"]["stale_after_seconds"] == 1800


def test_readiness_synthetic_report_bounds_and_exception_redaction(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch)
    fixture.environment["AUTOPOIESIS_SYMBOLS"] = ",".join(f"S{i}" for i in range(1000))
    report = collect_synthetic(fixture)
    assert report["exit_code"] == 1
    assert report["diagnostics"] == ["CONFIG_REPORT_BOUNDS_EXCEEDED"]
    assert len(json.dumps(report)) < 10000
    assert not fixture.calls


def readiness_installer():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "tools" / "install_market_readiness.py"
    assert path.exists(), "staged readiness timer installer is missing"
    spec = importlib.util.spec_from_file_location("install_market_readiness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("verification_ok", [False, True])
def test_readiness_synthetic_timer_install_verifies_before_replacement(tmp_path, verification_ok):
    """Synthetic systemd runner; tests rendering/install ordering without a manager."""
    installer = readiness_installer()
    root = Path(__file__).resolve().parents[2]
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    names = ("market-preflight.service", "market-preflight.timer", "market-open-verify.service", "market-open-verify.timer")
    for name in names:
        (unit_dir / name).write_text("synthetic old installed unit")
    envfile = tmp_path / "external-env"
    envfile.touch()
    called = []

    def runner(command, *, check):
        called.append(command)
        assert check is True
        if command[0] == "systemd-analyze":
            assert all((unit_dir / name).read_text() == "synthetic old installed unit" for name in names)
            staged = [Path(path) for path in command[3:]]
            assert len(staged) == 4
            assert all(path.is_file() for path in staged)
            assert "/usr/bin/make" in staged[0].read_text()
            assert "aaaaaaaaaaaaaaaa" in staged[0].read_text()
            if not verification_ok:
                raise subprocess.CalledProcessError(1, command)

    if verification_ok:
        installer.install(root=root, python=Path(sys.executable), envfile=envfile, expected_fingerprint="a" * 16,
            deployment_after="2026-10-13T13:30:00+00:00", unit_dir=unit_dir, runner=runner)
        assert all((unit_dir / name).read_text() != "synthetic old installed unit" for name in names)
        assert called[1] == ["systemctl", "--user", "daemon-reload"]
        assert called[2] == ["systemctl", "--user", "enable", "--now", "market-preflight.timer", "market-open-verify.timer"]
    else:
        with pytest.raises(subprocess.CalledProcessError):
            installer.install(root=root, python=Path(sys.executable), envfile=envfile, expected_fingerprint="a" * 16,
                deployment_after="2026-10-13T13:30:00+00:00", unit_dir=unit_dir, runner=runner)
        assert all((unit_dir / name).read_text() == "synthetic old installed unit" for name in names)
        assert len(called) == 1


def test_readiness_synthetic_guardian_reviews_at_model_completion_time(tmp_path, monkeypatch):
    from datetime import timedelta

    fixture = synthetic_readiness(tmp_path, monkeypatch)
    builder = fixture.tool._build_agent_daemon

    def slow_builder(cfg, client, *, journal=None):
        daemon = builder(cfg, client, journal=journal)
        transport = daemon.loop.decision_engine.llm.transport

        def slow(*args):
            result = transport(*args)
            fixture.now += timedelta(minutes=20)
            fixture.clock.timestamp = fixture.now
            cfg.heartbeat_path.write_text(fixture.heartbeat.model_copy(update={"timestamp": fixture.now}).model_dump_json())
            return result

        daemon.loop.decision_engine.llm.transport = slow
        return daemon

    monkeypatch.setattr(fixture.tool, "_build_agent_daemon", slow_builder)
    monkeypatch.setattr(fixture.tool, "_now", lambda: fixture.now)
    report = collect_synthetic(fixture)
    assert report["exit_code"] == 0, report
    assert report["preview"]["guardian_approved"] is False
    assert report["preview"]["guardian_refusal"] == "STALE_DATA"


def append_synthetic_readiness_submission(fixture, journal, *, suffix="one", order_id=None, filled_quantity=0):
    """Synthetic durable order identity; no executor or broker submission."""
    from autopoiesis.models import ExecutionResult

    record = synthetic_session_record(fixture)
    record = record.model_copy(update={
        "cycle_id": f"synthetic-cycle-{suffix}",
        "decision": record.decision.model_copy(update={"action": "BUY", "quantity": 3}),
        "execution": ExecutionResult(status="SUBMITTED", order_id=order_id or f"synthetic-order-{suffix}",
            client_order_id=f"synthetic-client-{suffix}", filled_quantity=filled_quantity, message="synthetic"),
    })
    journal.append(record)
    for kind in ("ORDER_SUBMISSION_INTENT", "ORDER_SUBMISSION_ACKNOWLEDGED"):
        journal.append_event(JournalEvent(
            event_id=f"synthetic-{suffix}-{kind}", event_type=kind, timestamp=fixture.now, status="SUCCESS",
            cycle_id=record.cycle_id, strategy_id=record.strategy_id,
            payload={"client_order_id": record.execution.client_order_id, "order_id": record.execution.order_id,
                "symbol": record.symbol, "action": record.decision.action, "quantity": record.decision.quantity},
        ))
    return record


def append_synthetic_readiness_fill(fixture, journal, record, *, quantity, status, suffix, kind="ORDER_FILL_SNAPSHOT", mutation=None):
    """Use the actual cumulative fill schema with labelled synthetic values."""
    from autopoiesis.fill_reconciler import FillConfirmation, PendingOrder

    payload = FillConfirmation(
        pending=PendingOrder(client_order_id=record.execution.client_order_id, order_id=record.execution.order_id,
            symbol=record.symbol, side=record.decision.action, quantity=record.decision.quantity, strategy_id=record.strategy_id),
        broker_status=status, filled_quantity=quantity, filled_avg_price=100 if quantity else None,
    ).payload()
    payload.update(mutation or {})
    journal.append_event(JournalEvent(
        event_id=f"synthetic-fill-{suffix}", event_type=kind, timestamp=fixture.now, status="SUCCESS",
        strategy_id=record.strategy_id, payload=payload,
    ))


def test_readiness_synthetic_rejects_duplicate_broker_order_id(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    journal = JsonlJournal(fixture.cfg.journal_path)
    append_synthetic_readiness_submission(fixture, journal, suffix="one", order_id="same-synthetic-broker-order")
    append_synthetic_readiness_submission(fixture, journal, suffix="two", order_id="same-synthetic-broker-order")
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 1, report
    assert report["diagnostics"] == ["DUPLICATE_BROKER_ORDER_ID"]


@pytest.mark.parametrize(("stages", "expected_quantity", "pending"), [
    ([(1, "partially_filled")], 1, 1),
    ([(1, "partially_filled"), (3, "filled")], 3, 0),
    ([(0, "canceled")], 0, 0),
    ([(1, "partially_filled"), (1, "canceled")], 1, 0),
])
def test_readiness_synthetic_counts_latest_cumulative_fill_once(tmp_path, monkeypatch, stages, expected_quantity, pending):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    journal = JsonlJournal(fixture.cfg.journal_path)
    record = append_synthetic_readiness_submission(fixture, journal, filled_quantity=0.5 if expected_quantity else 0)
    # An old confirmed-fill row plus growing snapshots must not be added together.
    if expected_quantity:
        append_synthetic_readiness_fill(fixture, journal, record, quantity=0.5, status="partially_filled",
            suffix="legacy", kind="ORDER_FILL_CONFIRMED")
    for index, (quantity, status) in enumerate(stages):
        append_synthetic_readiness_fill(fixture, journal, record, quantity=quantity, status=status, suffix=str(index))
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 0, report
    assert report["session"]["filled_quantity"] == expected_quantity
    assert report["session"]["pending_fill_observations"] == pending
    assert report["session"]["learning_feedback"] == "UNOBSERVED"


@pytest.mark.parametrize("field", ["client_order_id", "order_id", "symbol", "side", "strategy_id"])
def test_readiness_synthetic_fill_identity_mismatch_fails(tmp_path, monkeypatch, field):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    journal = JsonlJournal(fixture.cfg.journal_path)
    record = append_synthetic_readiness_submission(fixture, journal)
    append_synthetic_readiness_fill(fixture, journal, record, quantity=3, status="filled", suffix="mismatched",
        mutation={field: "synthetic-mismatched-value"})
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 1, report
    assert report["diagnostics"] == ["FILL_IDENTITY_MISMATCH"]


@pytest.mark.parametrize(("status", "code", "expected_process"), [("WAIT", 2, 0), ("FAIL", 1, 1)])
def test_readiness_synthetic_scheduled_wait_only_normalization(tmp_path, monkeypatch, capsys, status, code, expected_process):
    tool = readiness_tool()
    monkeypatch.setattr(tool, "collect", lambda **kwargs: {"status": status, "exit_code": code, "profitability": "UNPROVEN"})
    output = tmp_path / "private-scheduled-report.json"
    process = tool.main(["--phase", "preopen", "--expected-fingerprint", "a" * 16,
                         "--output", str(output), "--scheduled"])
    assert process == expected_process
    assert json.loads(output.read_text())["exit_code"] == code
    assert json.loads(capsys.readouterr().out)["status"] == status


@pytest.mark.parametrize(("status", "code", "expected_make"), [("WAIT", 2, 0), ("FAIL", 1, 2)])
def test_make_scheduled_readiness_preserves_wait_and_failure(tmp_path, status, code, expected_make):
    """Synthetic collector + real Make recipe; never accesses systemd/broker/model."""
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "Makefile").write_text((root / "Makefile").read_text())
    (tmp_path / "tools").mkdir()
    module = tmp_path / "tools" / "market_readiness.py"
    module.write_text((root / "tools" / "market_readiness.py").read_text())
    command = tmp_path / "synthetic-python"
    command.write_text(
        f"#!{sys.executable}\nimport runpy,sys\n"
        "real = runpy.run_path\n"
        "def synthetic_path(path):\n"
        " module = real(path)\n"
        f" module['main'].__globals__['collect'] = lambda **kwargs: {{'status': {status!r}, 'exit_code': {code}, 'profitability': 'UNPROVEN'}}\n"
        " return module\n"
        "runpy.run_path = synthetic_path\n"
        "exec(sys.argv[2], {'__name__': '__main__'})\n"
    )
    command.chmod(0o755)
    result = subprocess.run([
        "make", "--no-print-directory", "market-preflight", "ENVFILE=/dev/null", f"PY={command}",
        "SCHEDULED=1", "EXPECTED_FINGERPRINT=aaaaaaaaaaaaaaaa", f"READINESS_OUTPUT={tmp_path / 'scheduled.json'}",
    ], cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")})
    assert result.returncode == expected_make, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[0])
    assert report["status"] == status
    assert report["exit_code"] == code
    assert json.loads((tmp_path / "scheduled.json").read_text())["exit_code"] == code
    assert "SuccessExitStatus=2" not in (root / "tools" / "market-preflight.service.in").read_text()
    assert "SuccessExitStatus=2" not in (root / "tools" / "market-open-verify.service.in").read_text()


@pytest.mark.parametrize("quantity", [-1, float("nan"), float("inf"), 4])
def test_readiness_synthetic_fill_quantity_must_fit_submitted_order(tmp_path, monkeypatch, quantity):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    journal = JsonlJournal(fixture.cfg.journal_path)
    record = append_synthetic_readiness_submission(fixture, journal)
    append_synthetic_readiness_fill(fixture, journal, record, quantity=1, status="partially_filled", suffix="invalid-quantity",
        mutation={"filled_quantity": quantity, "cumulative_filled_quantity": quantity})
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 1, report
    assert report["diagnostics"] == ["FILL_QUANTITY_INVALID"]


def test_readiness_synthetic_future_fill_cannot_supply_current_evidence(tmp_path, monkeypatch):
    from datetime import timedelta

    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    journal = JsonlJournal(fixture.cfg.journal_path)
    record = append_synthetic_readiness_submission(fixture, journal)
    append_synthetic_readiness_fill(fixture, journal, record, quantity=1, status="partially_filled", suffix="current")
    fixture.now += timedelta(minutes=1)
    append_synthetic_readiness_fill(fixture, journal, record, quantity=3, status="filled", suffix="future")
    fixture.now -= timedelta(minutes=1)
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 0, report
    assert report["session"]["filled_quantity"] == 1
    assert report["session"]["pending_fill_observations"] == 1


def test_readiness_synthetic_legacy_cumulative_fills_count_order_once(tmp_path, monkeypatch):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=True)
    journal = JsonlJournal(fixture.cfg.journal_path)
    record = append_synthetic_readiness_submission(fixture, journal)
    append_synthetic_readiness_fill(fixture, journal, record, quantity=1, status="partially_filled",
        suffix="legacy-partial", kind="ORDER_FILL_CONFIRMED")
    append_synthetic_readiness_fill(fixture, journal, record, quantity=3, status="filled",
        suffix="legacy-terminal", kind="ORDER_FILL_CONFIRMED")
    report = collect_synthetic(fixture, opened=True)
    assert report["exit_code"] == 0, report
    assert report["session"]["filled_quantity"] == 3
    assert report["session"]["pending_fill_observations"] == 0


@pytest.mark.parametrize("opened", [False, True])
@pytest.mark.parametrize("state", ["STARTING", "BACKING_OFF"])
def test_readiness_synthetic_initial_nonrunning_state_waits_with_private_report(tmp_path, monkeypatch, opened, state):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=opened)
    heartbeat = fixture.heartbeat.model_copy(update={
        "status": state, "error_count": 7, "last_broker_ok": False, "last_model_ok": None,
        "message": "synthetic-sensitive-backoff-message",
    })
    fixture.cfg.heartbeat_path.write_text(heartbeat.model_dump_json())
    if opened:
        JsonlJournal(fixture.cfg.journal_path).append(synthetic_session_record(fixture))
    report = collect_synthetic(fixture, opened=opened)
    assert report["exit_code"] == 2, report
    assert report["status"] == "WAIT"
    assert report["diagnostics"] == ["DAEMON_NOT_RUNNING"]
    assert report["daemon"]["status"] == state
    assert report["daemon"]["error_count"] == 7
    assert report["daemon"]["broker_healthy"] is False
    assert report["daemon"]["model_healthy"] is None
    assert report["report_directory"] == str(tmp_path / "readiness")
    assert not fixture.calls
    assert "synthetic-sensitive" not in json.dumps(report)
    output = Path(report["report_directory"]) / "synthetic-state-wait.json"
    fixture.tool.write_report(output, report)
    assert json.loads(output.read_text())["exit_code"] == 2
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("opened", [False, True])
@pytest.mark.parametrize("state", ["STARTING", "BACKING_OFF"])
def test_readiness_synthetic_late_nonrunning_state_reports_latest_health(tmp_path, monkeypatch, opened, state):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=opened)
    if opened:
        JsonlJournal(fixture.cfg.journal_path).append(synthetic_session_record(fixture))
    original = fixture.tool._build_agent_daemon

    def builder(cfg, client, *, journal=None):
        daemon = original(cfg, client, journal=journal)
        latest = fixture.heartbeat.model_copy(update={
            "status": state, "error_count": 11, "last_broker_ok": True, "last_model_ok": False,
            "message": "synthetic-private-health-message",
        })
        cfg.heartbeat_path.write_text(latest.model_dump_json())
        return daemon

    monkeypatch.setattr(fixture.tool, "_build_agent_daemon", builder)
    report = collect_synthetic(fixture, opened=opened)
    assert report["exit_code"] == 2, report
    assert report["status"] == "WAIT"
    assert report["diagnostics"] == ["DAEMON_NOT_RUNNING"]
    assert report["daemon"]["status"] == state
    assert report["daemon"]["error_count"] == 11
    assert report["daemon"]["broker_healthy"] is True
    assert report["daemon"]["model_healthy"] is False
    assert "synthetic-private" not in json.dumps(report)


@pytest.mark.parametrize("opened", [False, True])
def test_readiness_synthetic_recovered_running_does_not_fail_for_historical_errors(tmp_path, monkeypatch, opened):
    fixture = synthetic_readiness(tmp_path, monkeypatch, opened=opened)
    fixture.cfg.heartbeat_path.write_text(fixture.heartbeat.model_copy(update={
        "status": "RUNNING", "error_count": 99, "last_broker_ok": True, "last_model_ok": None,
    }).model_dump_json())
    if opened:
        JsonlJournal(fixture.cfg.journal_path).append(synthetic_session_record(fixture))
    report = collect_synthetic(fixture, opened=opened)
    assert report["exit_code"] == 0, report
    assert report["daemon"]["status"] == "RUNNING"
    assert report["daemon"]["error_count"] == 99
    assert report["daemon"]["model_healthy"] is None
    if not opened:
        assert report["preview"]["model_response_observed"] is True


def test_readiness_synthetic_health_checks_pid_from_same_payload(tmp_path, monkeypatch):
    """Synthetic heartbeat rollover must not lend another live PID to a dead one."""
    from autopoiesis.health import HealthMonitor

    fixture = synthetic_readiness(tmp_path, monkeypatch)
    monitor = HealthMonitor(fixture.cfg.heartbeat_path, pid_probe=lambda pid: pid == 124)
    reads = []

    def changing_read():
        reads.append(True)
        return fixture.heartbeat.model_copy(update={"pid": 123 if len(reads) == 1 else 124})

    monkeypatch.setattr(monitor, "read", changing_read)
    monkeypatch.setattr(fixture.tool, "HealthMonitor", lambda *args, **kwargs: monitor)
    with pytest.raises(fixture.tool.ObservationRefused) as refused:
        fixture.tool._health(fixture.cfg, "a" * 16, fixture.now)
    assert refused.value.code == "DAEMON_NOT_LIVE"
    assert len(reads) == 1
