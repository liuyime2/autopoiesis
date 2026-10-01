from datetime import datetime, timezone

import pytest

from min_agent.cli import _evidence_report, _stop_daemon, _verify_profit_target
from min_agent.config import AgentConfig
from min_agent.journal import JsonlJournal
from min_agent.models import BrokerEvidenceBatch, JournalEvent, PortfolioHistoryPoint


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

    monkeypatch.setattr("min_agent.cli.os.kill", fake_kill)

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

    monkeypatch.setattr("min_agent.cli.os.kill", fake_kill)

    exit_code = _stop_daemon(cfg)

    assert exit_code == 0
    assert calls[0][0] == 123
    assert not cfg.pidfile_path.exists()


def append_evidence(journal, *, return_pct=0.11):
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


def test_verify_profit_target_fails_without_broker_evidence(tmp_path, capsys):
    cfg = config(tmp_path)

    exit_code = _verify_profit_target(cfg)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"status": "unproven"' in output


def test_verify_profit_target_fails_below_threshold(tmp_path, capsys):
    cfg = config(tmp_path)
    append_evidence(JsonlJournal(cfg.journal_path), return_pct=0.02)

    exit_code = _verify_profit_target(cfg)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"status": "not satisfied"' in output


def test_verify_profit_target_succeeds_only_with_broker_verified_threshold(tmp_path, capsys):
    cfg = config(tmp_path)
    append_evidence(JsonlJournal(cfg.journal_path), return_pct=0.11)

    exit_code = _verify_profit_target(cfg)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"status": "satisfied"' in output


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
    from min_agent import cli

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0

    out = capsys.readouterr().out
    assert "usage:" in out
    # The flags an operator needs to discover the system.
    for flag in ("--daemon", "--status", "--doctor", "--once"):
        assert flag in out, f"{flag} missing from --help output"
