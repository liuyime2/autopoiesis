"""A unit written where systemd does not read it is a silent success, not a warning.

Measured 2026-10-13, first open session after deploying source `9fb9066d820f27c9`.
`make install-readiness-timers` was run without an explicit `READINESS_UNIT_DIR`. The
Makefile's default was

    READINESS_UNIT_DIR ?= $(if $(XDG_CONFIG_HOME),$(XDG_CONFIG_HOME),$(HOME)/.config)/systemd/user

which trusts the *shell's* `XDG_CONFIG_HOME`. On this host that is
`/localscratch/liuyime2/ohome/.config`, while `systemctl --user show-environment` reports no
`XDG_CONFIG_HOME` at all, so the manager searches `$HOME/.config/systemd/user`. The installer
validated four rendered units with `systemd-analyze --user verify`, wrote them to the
directory systemd never reads, printed no warning and **exited 0**.

Nothing failed for twenty minutes. The correction only surfaced because the installed bytes
were diffed against a dry render afterwards, and the live `ExecStart` still carried the old
`DEPLOYMENT_AFTER` while the Makefile's dry run carried the new one. Four unit files had to
be deleted from a directory they should never have been created in, and the install repeated
against the manager's real path.

`tools/systemd_unit_dir.sh` already asks the manager rather than assuming, and its own
docstring records this exact failure from an earlier incident. The Makefile reimplemented it
incorrectly instead of calling it.

Usage: pytest tests/autopoiesis/test_readiness_unit_dir_default.py -q
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _make_default() -> str:
    """The value the Makefile actually resolves, without running an install.

    `make -p` prints the unexpanded definition, so the recipe is expanded through `make -n`
    instead - the same text the operator would run.
    """
    out = subprocess.run(
        ["make", "-f", str(ROOT / "Makefile"), "-n", "install-readiness-timers",
         "PY=/nonexistent/python"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    ).stdout
    match = re.search(r'test -n "([^"]+)"', out)
    return match.group(1) if match else ""


def _has_systemd_user_manager() -> bool:
    """Whether this host can answer the question the environment-dependent tests ask.

    Measured on GitHub's runners 2026-10-14. The tests below asserted against
    `tools/systemd_unit_dir.sh` and skipped only when that script exited non-zero. The
    script does not fail on a host with no systemd user manager: with no manager to ask
    it falls back to `$HOME/.config`, finds the parent directory writable, and prints
    `$HOME/.config/systemd/user` with exit 0. So the skip never fired, the comparison
    ran, and it failed on a runner where the Makefile's own `$(shell ...)` had resolved
    nothing to compare against.

    The dependency was never "the script failed" - it was "this host has a manager whose
    answer means anything". That is what is asked here, so the skip fires for the right
    reason rather than a proxy that happens to hold on one machine.
    """
    if shutil.which("systemctl") is None:
        return False
    probe = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        text=True, capture_output=True, check=False,
    )
    return probe.returncode == 0


def test_the_default_is_derived_from_the_manager_not_from_the_shell():
    """`XDG_CONFIG_HOME` is a shell fact. The manager resolves unit paths from its own.

    Asserted against the Makefile's *source*, not against `make -p`'s output. `-p` prints a
    variable either unexpanded or expanded depending on how the variable is reached, so the
    same tree and the same command produced `$(shell ...)` in one invocation and the resolved
    path in another - which is a property of `make -p`, not of this Makefile. The source text
    is the thing that has to change.
    """
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    line = next(
        (l for l in text.splitlines() if l.startswith("READINESS_UNIT_DIR ?=")),
        None,
    )

    assert line is not None, "READINESS_UNIT_DIR lost its default"
    assert "XDG_CONFIG_HOME" not in line, (
        f"the default still trusts the shell's XDG_CONFIG_HOME: {line!r}. "
        "The systemd user manager resolves unit paths from its own start-time environment, "
        "which on this host has none, so it searches $HOME/.config/systemd/user."
    )
    assert "systemd_unit_dir.sh" in line, (
        f"the default must ask the manager, the way install-service does: {line!r}"
    )


def test_the_default_matches_where_the_manager_actually_reads_units():
    """The two must agree, and if they cannot this must not pass quietly."""
    if not _has_systemd_user_manager():
        import pytest

        pytest.skip(
            "no systemd user manager on this host: tools/systemd_unit_dir.sh falls back "
            "to $HOME/.config and succeeds anyway, so there is no manager answer to "
            "compare the Makefile default against"
        )
    manager_dir = subprocess.run(
        [str(ROOT / "tools" / "systemd_unit_dir.sh")], cwd=ROOT, text=True,
        capture_output=True, check=False,
    )
    if manager_dir.returncode != 0:
        import pytest

        pytest.skip("no durable systemd user unit directory on this host")

    resolved = _make_default()

    assert resolved == manager_dir.stdout.strip(), (
        f"the Makefile would install into {resolved!r} while the manager reads "
        f"{manager_dir.stdout.strip()!r}"
    )


def test_installing_without_a_unit_directory_fails_loudly():
    """`$(shell ...)` swallows the exit code, so the recipe has to check the result."""
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = text.split("install-readiness-timers:", 1)[1].split("\n\n", 1)[0]

    assert 'test -n "$(READINESS_UNIT_DIR)"' in recipe, (
        "an undeterminable unit directory must stop the install, not produce an empty path "
        "that the Python installer rejects with a generic TIMER_INSTALL_FAILED"
    )


def test_the_resolved_default_is_an_absolute_path():
    """The installer refuses a relative path, and that refusal is generic.

    `install()` raises `ValueError` for anything not absolute, and `main()` turns that into
    one line: `TIMER_INSTALL_FAILED`. A relative default would therefore fail with a message
    that names neither the path nor the reason, which is the opposite of actionable.
    """
    default = _make_default()

    assert default.startswith("/"), (
        f"the default must resolve to an absolute path, got {default!r}. "
        "`systemd_unit_dir.sh` prints one; a relative default is a generic failure."
    )
