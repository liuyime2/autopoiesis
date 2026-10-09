"""Tests for the `syntax-import` check class.

Every other substantive check class runs a slice of the suite from inside the gate. This
one does not, and that is exactly why it was the last class in `CHECK_CLASSES` with no test
file behind it - it is the only one whose own failure mode is a source file that does not
compile, which is the one defect that stops the rest of the suite from running at all.

So these tests exercise the same two properties directly: every module byte-compiles, and
every module in the package imports cleanly with its public names present.
"""
from __future__ import annotations

import importlib
import py_compile
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "autopoiesis"


def _modules() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if p.stem != "__init__")


def test_every_package_module_byte_compiles(tmp_path):
    """A file that cannot compile stops every other test from being collected.

    Compiled into a throwaway directory so the check does not leave `__pycache__` beside
    the sources it is inspecting.
    """
    failures = []
    for path in _modules():
        try:
            py_compile.compile(
                str(path),
                cfile=str(tmp_path / f"{path.stem}.pyc"),
                doraise=True,
            )
        except py_compile.PyCompileError as exc:
            failures.append(f"{path.name}: {exc}")
    assert not failures, "modules that do not compile:\n" + "\n".join(failures)


def test_every_package_module_imports():
    """Import must succeed with `src` on the path, which is how the gate runs it."""
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    failures = []
    for path in _modules():
        name = f"autopoiesis.{path.stem}"
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "modules that fail to import:\n" + "\n".join(failures)


def test_tools_and_tests_also_compile(tmp_path):
    """`compileall` in the check covers `tools` and `tests` too, not just `src`."""
    failures = []
    for base in ("tools", "tests"):
        for path in sorted((ROOT / base).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                py_compile.compile(
                    str(path),
                    cfile=str(tmp_path / f"{path.stem}.pyc"),
                    doraise=True,
                )
            except py_compile.PyCompileError as exc:
                failures.append(f"{path.relative_to(ROOT)}: {exc}")
    assert not failures, "files that do not compile:\n" + "\n".join(failures)


def test_the_entry_point_exposes_main():
    """`autopoiesis = autopoiesis.cli:main` is declared in pyproject.toml.

    The console script is the project's single documented entry point, so a rename of
    `main` would silently break `pip install` consumers while every other test passed.
    """
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from autopoiesis import cli

    assert callable(cli.main), "autopoiesis.cli.main must be callable"
    signature_params = cli.main.__code__.co_varnames[: cli.main.__code__.co_argcount]
    assert "argv" in signature_params, (
        "main should accept argv so tests can drive it without patching sys.argv; "
        f"got {signature_params}"
    )


def test_research_package_is_importable_but_not_imported_by_production():
    """`research/` ships with the package, and production must not reach for it.

    The separation is enforced by the `production-research-separation` check; this test
    pins the other half - that the quarantine is importable at all, so excluding it from
    production does not turn into deleting it.
    """
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    for name in ("autopoiesis.research", "autopoiesis.research.backtest", "autopoiesis.research.walk_forward"):
        assert importlib.import_module(name) is not None


@pytest.mark.parametrize("stem", [p.stem for p in _modules()])
def test_each_module_is_importable_individually(stem):
    """One module failing to import must be attributable to that module.

    The batch test above reports all failures at once; this one exists so a CI failure
    names the module rather than a list.
    """
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    importlib.import_module(f"autopoiesis.{stem}")
