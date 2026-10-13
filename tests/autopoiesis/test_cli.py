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
