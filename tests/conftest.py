"""Shared test configuration."""
import pytest


@pytest.fixture(autouse=True)
def _no_make_flags_in_child_processes(monkeypatch):
    """Tests run `make` as a child. make exports the command line's variable overrides to its
    recipes through MAKEFLAGS, so `make record-deployment PY=...` reached the tests' own child make
    and replaced the stub interpreters they depend on. Measured 2026-11-10: two full gate runs of
    about 50 minutes were lost, and one test wrote real backups into the live runtime. A test's
    environment is the test's own business, not the operator's command line."""
    for name in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS"):
        monkeypatch.delenv(name, raising=False)
