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
