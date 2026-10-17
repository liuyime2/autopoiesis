"""Every name a function touches must actually exist at call time.

The daemon ran reporting healthy, then failed with
`NameError: name 'open_lot_cost_basis' is not defined` and did not come back. The
369-test suite imported modules and exercised units but never called
`_run_daemon`, so a dangling reference in the start-up wiring survived a green
run and killed the service. These checks make a start-up wiring bug as visible as
a logic bug.
"""

import ast
import builtins
import inspect
import os
import pathlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "autopoiesis"


def _bound_names(node) -> set[str]:
    """Every name bound anywhere inside `node`: args, assignments, imports, comprehensions."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.arg):
            names.add(inner.arg)
        elif isinstance(inner, ast.Name) and isinstance(inner.ctx, (ast.Store, ast.Del)):
            names.add(inner.id)
        elif isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(inner.name)
        elif isinstance(inner, (ast.Import, ast.ImportFrom)):
            for alias in inner.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(inner, ast.ExceptHandler) and inner.name:
            names.add(inner.name)
        elif isinstance(inner, (ast.Global, ast.Nonlocal)):
            names.update(inner.names)
    return names


def _used_names(node) -> set[str]:
    return {
        inner.id
        for inner in ast.walk(node)
        if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)
    }


def test_no_function_loads_a_name_that_does_not_exist():
    import importlib

    failures: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        module = importlib.import_module(f"autopoiesis.{path.stem}")
        module_names = set(vars(module)) | set(dir(builtins))
        tree = ast.parse(path.read_text())
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Names bound in the function, in enclosing functions, or the module.
            bound = _bound_names(func)
            for outer in ast.walk(tree):
                if isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)) and outer is not func:
                    if any(child is func for child in ast.walk(outer)):
                        bound |= _bound_names(outer)
            for name in sorted(_used_names(func) - bound - module_names):
                failures.append(f"{path.stem}.{func.name}(): {name!r}")

    assert not failures, (
        "these names are loaded but never bound; each is a NameError waiting for the "
        "line that reaches it:\n" + "\n".join(failures)
    )


@pytest.mark.parametrize("entry", ["_run_daemon", "_build_agent_daemon"])
def test_the_daemon_startup_path_resolves_every_name(entry):
    import autopoiesis.cli as cli

    assert callable(getattr(cli, entry, None)), f"{entry} is missing"
    source = inspect.getsource(getattr(cli, entry))
    tree = ast.parse(source.lstrip())
    bound = _bound_names(tree) | set(vars(cli)) | set(dir(builtins))
    unresolved = sorted(_used_names(tree) - bound)

    assert not unresolved, (
        f"{entry} references names that do not exist: " + ", ".join(unresolved)
    )


@pytest.mark.parametrize("name", [
    "main",
    "_run_daemon",
    "_agent_open_lots",
    "_ollama_curriculum_transport",
    "_evidence_report",
])
def test_cli_entry_points_are_present(name):
    """The public CLI surface is what operators and the unit files invoke."""
    import autopoiesis.cli as cli

    assert callable(getattr(cli, name))


def _watchdog_harness(tmp_path, monkeypatch):
    import subprocess

    from autopoiesis.cli import SERVICE_NAME, _watchdog
    from autopoiesis.config import AgentConfig
    from autopoiesis.health import HealthMonitor
    from autopoiesis.models import HeartbeatPayload

    config = replace(AgentConfig.from_env(), heartbeat_path=tmp_path / "heartbeat.json")

    calls: list[tuple[str, ...]] = []
    output: list[str] = []
    state_holder = {
        "value": "active",
        "restart_rc": 0,
        "reset_failed_rc": 0,
        "reset_failed_out": "",
        "timeout_calls": set(),
    }

    class Result:
        def __init__(self, code, out=""):
            self.returncode = code
            self.stdout = out
            self.stderr = ""

    def fake_run(args, **_kwargs):
        call_index = len(calls)
        calls.append(tuple(args))
        if call_index in state_holder["timeout_calls"]:
            raise subprocess.TimeoutExpired(args, timeout=60)
        verb = args[2]
        if verb == "is-active":
            if "is_active_result" in state_holder:
                return Result(*state_holder["is_active_result"])
            state = state_holder["value"]
            return Result(0 if state == "active" else 3, state)
        if verb == "reset-failed":
            return Result(state_holder["reset_failed_rc"], state_holder["reset_failed_out"])
        if verb in {"start", "restart"}:
            if state_holder["value"] != "active":
                state_holder["value"] = "active"
            return Result(state_holder["restart_rc"] if verb == "restart" else 0)
        return Result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.print", lambda *parts, **_kwargs: output.append(" ".join(map(str, parts))))
    monitor = HealthMonitor(config.heartbeat_path, stale_after_seconds=config.stale_after_seconds)

    def write_heartbeat(*, status="RUNNING", timestamp=None, pid=None):
        monitor.write(HeartbeatPayload(
            pid=os.getpid() if pid is None else pid,
            status=status,
            timestamp=timestamp or datetime.now(timezone.utc),
            cycle_count=1,
            error_count=0,
        ))

    return config, calls, output, state_holder, write_heartbeat


def test_watchdog_leaves_a_fresh_live_daemon_alone(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, _output, _state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat()

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active"]


def test_watchdog_restarts_an_active_daemon_with_a_stale_heartbeat(tmp_path, monkeypatch):
    """An active unit can still be wedged; the old code only checked systemd state."""
    from autopoiesis.cli import _watchdog

    config, calls, _output, _state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat(timestamp=datetime.now(timezone.utc) - timedelta(
        seconds=config.stale_after_seconds + 1,
    ))

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "restart", "is-active"]


def test_watchdog_does_not_restart_when_heartbeat_turns_stopping_during_liveness_check(
    tmp_path, monkeypatch,
):
    from autopoiesis.cli import _watchdog
    from autopoiesis.health import HealthMonitor

    config, calls, _output, _state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat(timestamp=datetime.now(timezone.utc) - timedelta(
        seconds=config.stale_after_seconds + 1,
    ))
    original_is_live = HealthMonitor.is_live

    def begin_shutdown_during_check(monitor, *args, **kwargs):
        write_heartbeat(status="STOPPING")
        return original_is_live(monitor, *args, **kwargs)

    monkeypatch.setattr(HealthMonitor, "is_live", begin_shutdown_during_check)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active"]


def test_watchdog_does_not_restart_when_final_heartbeat_observation_is_fresh(
    tmp_path, monkeypatch,
):
    from autopoiesis.cli import _watchdog
    from autopoiesis.health import HealthMonitor

    config, calls, _output, _state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat(timestamp=datetime.now(timezone.utc) - timedelta(
        seconds=config.stale_after_seconds + 1,
    ))
    original_is_live = HealthMonitor.is_live

    def become_fresh_after_stale_liveness_check(monitor, *args, **kwargs):
        is_live = original_is_live(monitor, *args, **kwargs)
        if not is_live:
            write_heartbeat()
        return is_live

    monkeypatch.setattr(HealthMonitor, "is_live", become_fresh_after_stale_liveness_check)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active"]


@pytest.mark.parametrize("heartbeat_state", ["missing", "malformed"])
def test_watchdog_leaves_an_active_daemon_alone_when_heartbeat_is_unknown(
    tmp_path, monkeypatch, heartbeat_state,
):
    from autopoiesis.cli import _watchdog

    config, calls, _output, _state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    if heartbeat_state == "malformed":
        config.heartbeat_path.write_text("not-json", encoding="utf-8")

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active"]


def test_watchdog_leaves_a_stopping_daemon_alone(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, _output, _state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat(status="STOPPING")

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active"]


@pytest.mark.parametrize("unit_state", ["inactive", "failed"])
def test_watchdog_preserves_recovery_order_for_known_stopped_states(tmp_path, monkeypatch, unit_state):
    """Only explicit stopped states may enter reset-failed then start."""
    from autopoiesis.cli import _watchdog

    config, calls, _output, state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    state_holder["value"] = unit_state

    assert _watchdog(config) == 0
    verbs = [call[2] for call in calls]
    assert verbs == ["is-active", "reset-failed", "start", "is-active"]


@pytest.mark.parametrize(
    ("unit_state", "is_active_result", "expected_detail"),
    [
        ("", None, "exit status 3"),
        ("unknown", None, "unknown"),
        ("activating", None, "activating"),
        ("deactivating", None, "deactivating"),
        ("active", (1, "Failed to connect to bus"), "Failed to connect to bus"),
    ],
)
def test_watchdog_does_not_mutate_unknown_or_in_progress_unit_state(
    tmp_path, monkeypatch, unit_state, is_active_result, expected_detail,
):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    state_holder["value"] = unit_state
    if is_active_result is not None:
        state_holder["is_active_result"] = is_active_result

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active"]
    assert any(expected_detail in line for line in output), output


def test_watchdog_reports_a_failed_stale_daemon_restart(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat(timestamp=datetime.now(timezone.utc) - timedelta(
        seconds=config.stale_after_seconds + 1,
    ))
    state_holder["restart_rc"] = 1

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "restart"]
    assert any("could not restart" in line for line in output), output


def test_watchdog_does_not_change_service_when_initial_state_check_times_out(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    state_holder["timeout_calls"].add(0)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active"]
    assert any("is-active" in line and "timed out after 60 seconds" in line for line in output)


def test_watchdog_does_not_start_after_reset_failed_times_out(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    state_holder["value"] = "failed"
    state_holder["timeout_calls"].add(1)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "reset-failed"]
    assert any("reset-failed" in line and "timed out after 60 seconds" in line for line in output)


def test_watchdog_does_not_start_after_reset_failed_returns_error(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    state_holder["value"] = "failed"
    state_holder["reset_failed_rc"] = 1
    state_holder["reset_failed_out"] = "permission denied"

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "reset-failed"]
    assert any("reset-failed" in line and "permission denied" in line for line in output)


def test_watchdog_reports_restart_timeout_without_verification_or_retry(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat(timestamp=datetime.now(timezone.utc) - timedelta(
        seconds=config.stale_after_seconds + 1,
    ))
    state_holder["timeout_calls"].add(1)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "restart"]
    assert any("restart" in line and "timed out after 60 seconds" in line for line in output)


def test_watchdog_reports_start_timeout_without_verification_or_retry(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    state_holder["value"] = "failed"
    state_holder["timeout_calls"].add(2)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "reset-failed", "start"]
    assert any("start" in line and "timed out after 60 seconds" in line for line in output)


def test_watchdog_reports_restart_verification_timeout(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    write_heartbeat(timestamp=datetime.now(timezone.utc) - timedelta(
        seconds=config.stale_after_seconds + 1,
    ))
    state_holder["timeout_calls"].add(2)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "restart", "is-active"]
    assert any("unit is" in line and "timed out after 60 seconds" in line for line in output)


def test_watchdog_reports_start_verification_timeout(tmp_path, monkeypatch):
    from autopoiesis.cli import _watchdog

    config, calls, output, state_holder, _write_heartbeat = _watchdog_harness(tmp_path, monkeypatch)
    state_holder["value"] = "failed"
    state_holder["timeout_calls"].add(3)

    assert _watchdog(config) == 0
    assert [call[2] for call in calls] == ["is-active", "reset-failed", "start", "is-active"]
    assert any("unit is" in line and "timed out after 60 seconds" in line for line in output)
