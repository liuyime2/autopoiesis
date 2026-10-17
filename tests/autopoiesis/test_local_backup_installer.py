"""Synthetic installer subprocesses: never contact or change the user's service manager."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _stub(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def installer(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    interpreter = tmp_path / "interpreter"
    interpreter.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    log = tmp_path / "commands.log"
    child_env = tmp_path / "child-env.txt"
    _stub(interpreter / "python", 'printf "%s" "$PYTHONPATH" > "$SYNTHETIC_CHILD_ENV"\necho 60\n')
    _stub(commands / "ollama", "exit 0\n")
    _stub(commands / "systemctl", 'printf "%s\\n" "$*" >> "$SYNTHETIC_COMMAND_LOG"\nexit 0\n')
    _stub(commands / "systemd-analyze", 'printf "%s\\n" "$*" >> "$SYNTHETIC_COMMAND_LOG"\nexit "${SYNTHETIC_VERIFY_EXIT:-0}"\n')
    env = os.environ.copy()
    env.update(
        PATH=str(commands) + os.pathsep + env["PATH"], AUTOPOIESIS_ENV_FILE="/dev/null",
        AUTOPOIESIS_ENVBIN=str(interpreter), AUTOPOIESIS_UNIT_DIR=str(units),
        OLLAMA_BIN=str(commands / "ollama"), OLLAMA_MODELS=str(models),
        PYTHONPATH="synthetic-path-sentinel", SYNTHETIC_COMMAND_LOG=str(log),
        SYNTHETIC_CHILD_ENV=str(child_env),
    )
    return env, units, log, child_env


def _install(env):
    return subprocess.run(["bash", str(ROOT / "autopoiesis"), "install-service"],
                          env=env, capture_output=True, text=True, timeout=10)


def test_installer_preserves_pythonpath_for_derived_budget(installer):
    env, _, _, child_env = installer
    result = _install(env)
    assert result.returncode == 0, result.stderr
    assert child_env.read_text() == str(ROOT / "src") + ":synthetic-path-sentinel"


def test_installer_renders_and_starts_credential_free_persistent_backup_timer(installer):
    env, units, log, _ = installer
    result = _install(env)
    assert result.returncode == 0, result.stderr
    service = (units / "quant-backup.service").read_text()
    timer = (units / "quant-backup.timer").read_text()
    assert "EnvironmentFile=" not in service
    assert "ENVFILE=/dev/null" in service
    assert "journal-local-backup" in service
    assert "@ROOT@" not in service
    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer
    commands = log.read_text()
    assert "quant-backup.service" in next(line for line in commands.splitlines() if line.startswith("verify "))
    assert "--user enable --now quant-backup.timer" in commands
    assert "--user is-active --quiet quant-backup.timer" in commands


def test_installer_refuses_to_enable_units_that_failed_verification(installer):
    env, _, log, _ = installer
    env["SYNTHETIC_VERIFY_EXIT"] = "1"
    result = _install(env)
    assert result.returncode != 0
    assert "enable" not in log.read_text()


def test_make_backup_child_receives_no_broker_or_model_credentials(installer):
    env, _, _, child_env = installer
    interpreter = Path(env["AUTOPOIESIS_ENVBIN"])
    _stub(interpreter / "python", '''printf '%s|%s|%s|%s|%s|%s|%s' \\
      "${ALPACA_API_KEY-unset}" "${ALPACA_SECRET_KEY-unset}" \\
      "${APCA_API_KEY_ID-unset}" "${APCA_API_SECRET_KEY-unset}" \\
      "${OPENAI_API_KEY-unset}" "${ANTHROPIC_API_KEY-unset}" "$AUTOPOIESIS_ENV_FILE" \\
      > "$SYNTHETIC_CHILD_ENV"
''')
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env[name] = "synthetic-test-sentinel"
    env["PATH"] = str(interpreter) + os.pathsep + env["PATH"]
    env["VIRTUAL_ENV"] = str(interpreter.parent)
    result = subprocess.run(["make", "journal-local-backup", "ENVFILE=/dev/null"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert child_env.read_text() == "unset|unset|unset|unset|unset|unset|/dev/null"


@pytest.mark.parametrize("failure", ["invisible", "start", "inactive"])
def test_transient_installer_propagates_backup_timer_failure(installer, tmp_path, failure):
    env, _, _, _ = installer
    commands = Path(env["OLLAMA_BIN"]).parent
    run_units = tmp_path / "synthetic-run" / "systemd" / "user"
    run_units.mkdir(parents=True)
    # The fake UID resolves /run/user/<UID> only to this disposable fixture.
    _stub(commands / "id", 'echo "../../' + str(tmp_path).lstrip("/") + '/synthetic-run"\n')
    _stub(commands / "sleep", "exit 0\n")
    _stub(commands / "systemctl", '''
case "$*" in
  "--user list-unit-files")
    printf '%s\\n' 'ollama.service enabled' 'autopoiesis.service enabled' 'quant-watchdog.timer enabled'
    [ "$SYNTHETIC_TIMER_FAILURE" = invisible ] || echo 'quant-backup.timer enabled'
    exit 0 ;;
  "--user start quant-backup.timer")
    [ "$SYNTHETIC_TIMER_FAILURE" = start ] && exit 1 ;;
  "--user is-active quant-backup.timer")
    if [ "$SYNTHETIC_TIMER_FAILURE" = inactive ]; then echo inactive; exit 3; fi ;;
esac
echo active
exit 0
''')
    env["SYNTHETIC_TIMER_FAILURE"] = failure
    result = subprocess.run(["bash", str(ROOT / "autopoiesis"), "install-transient"],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert (run_units / "quant-backup.timer").exists()
