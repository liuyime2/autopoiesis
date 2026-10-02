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
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "min_agent"


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
        module = importlib.import_module(f"min_agent.{path.stem}")
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


def test_the_daemon_startup_path_resolves_every_name():
    import min_agent.cli as cli

    source = inspect.getsource(cli._run_daemon)
    tree = ast.parse(source.lstrip())
    bound = _bound_names(tree) | set(vars(cli)) | set(dir(builtins))
    unresolved = sorted(_used_names(tree) - bound)

    assert not unresolved, (
        "_run_daemon references names that do not exist: " + ", ".join(unresolved)
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
    import min_agent.cli as cli

    assert callable(getattr(cli, name))


def test_watchdog_restarts_a_dead_daemon_and_leaves_a_live_one_alone(tmp_path, monkeypatch):
    """The timer unit ran `--doctor`, so it reported a dead daemon and never revived it.

    With `StartLimitBurst=5` and `StartLimitIntervalSec=900` in the daemon's unit, five
    crashes inside fifteen minutes left it stopped permanently - systemd had latched the
    limit - and the only process still running was the one that noticed. The fix is that the
    watchdog acts rather than reports, and these are the two ways that could do harm if it
    got them wrong: restarting a healthy daemon, and retrying in a tight loop.
    """
    import subprocess

    from min_agent.cli import SERVICE_NAME, _watchdog
    from min_agent.config import AgentConfig

    config = AgentConfig.from_env()

    calls: list[tuple[str, ...]] = []

    class Result:
        def __init__(self, code, out=""):
            self.returncode = code
            self.stdout = out
            self.stderr = ""

    def fake_run(args, **_kwargs):
        calls.append(tuple(args))
        # `systemctl --user <verb> <unit>`: the verb is args[2]. Reading args[-1] looked at
        # the unit name, so the first version of this test asserted against 'min-agent.service'
        # four times and reported that reset-failed was never called.
        verb = args[2]
        if verb == "is-active":
            state = state_holder["value"]
            return Result(0 if state == "active" else 3, state)
        if verb == "start":
            if state_holder["value"] != "active":
                state_holder["value"] = "active"
            return Result(0)
        return Result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    # A healthy daemon: nothing is started, and nothing is stopped.
    state_holder = {"value": "active"}
    calls.clear()
    assert _watchdog(config) == 0
    assert not any(c[2] in {"start", "stop", "restart"} for c in calls), (
        f"the watchdog touched a running daemon: {calls}"
    )

    # A dead one: reset-failed then start, and never a stop.
    state_holder = {"value": "failed"}
    calls.clear()
    assert _watchdog(config) == 0
    verbs = [c[2] for c in calls]
    assert "reset-failed" in verbs, f"the latched start limit was never cleared: {verbs}"
    assert "start" in verbs, f"a dead daemon was never started: {verbs}"
    assert "stop" not in verbs and "restart" not in verbs, (
        f"the watchdog may only start, never stop: {verbs}"
    )
    assert verbs.index("reset-failed") < verbs.index("start"), (
        "reset-failed must come first, or systemd refuses the start"
    )
