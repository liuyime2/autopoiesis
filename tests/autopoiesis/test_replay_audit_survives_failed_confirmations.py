"""The independent replay must report new data, not die on it.

Measured 2026-10-15: six `ORDER_FILL_CONFIRMED` events landed with `error` and
`error_type` in place of `side` - failed confirmations, `SubmissionJournalError` - and
`tools/replay_audit.py` read `payload["side"]` unguarded. The script raised `KeyError`
and produced no output at all.

That is worse than the mismatch it exists to find. A check that dies on new data reports
nothing, and the failure looks like an unrelated crash rather than "here are six fills
that were never confirmed". The same six events also broke the gate class, which is how
this was noticed.

Usage: pytest tests/autopoiesis/test_replay_audit_survives_failed_confirmations.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "replay_audit.py"


def _run() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=ROOT, text=True, capture_output=True, check=False,
    )


def test_the_audit_runs_and_does_not_raise():
    """The crash, asserted where it happened.

    Skipped when there is no runtime journal: this is a statement about the record, and a
    fresh clone has none. The behaviour it pins is still exercised by the source check
    below, which needs no data.
    """
    if not (ROOT / "runtime" / "autopoiesis" / "journal.jsonl").is_file():
        import pytest

        pytest.skip("no runtime journal on this host")

    result = _run()

    assert "KeyError" not in result.stderr, (
        "a confirmation without a `side` must be reported, not raised on:\n"
        + result.stderr[-600:]
    )
    assert "broker-confirmed fills" in result.stdout, (
        "the script must reach the fills section, not stop before it:\n"
        + result.stdout[-400:]
    )


def test_a_failed_confirmation_is_not_counted_as_a_fill():
    """The source-level statement, which needs no journal to check.

    Six events with `error`/`error_type` are failures to confirm. Counting them as fills
    would inflate the bought and sold quantities the audit prints, and the FIFO arithmetic
    below it would pair lots against trades that never happened.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'isinstance(e["payload"].get("side"), str)' in source, (
        "the fills list must require a string `side` before counting an event as a fill"
    )
    assert "failed confirmation(s), not counted as fills" in source, (
        "and the ones it excludes must be printed, or a reader cannot tell a quiet "
        "journal from a filtered one"
    )


def test_the_audit_reports_its_three_comparisons():
    """Whatever it finds, it must get far enough to compare. A check that stops early is
    indistinguishable from a check that passed."""
    if not (ROOT / "runtime" / "autopoiesis" / "journal.jsonl").is_file():
        import pytest

        pytest.skip("no runtime journal on this host")

    out = _run().stdout

    for label in ("closed lots: naive FIFO vs ledger", "realized PnL: naive FIFO vs ledger",
                  "unmatched sell shares: naive vs ledger"):
        assert label in out, f"the comparison {label!r} did not run"


def test_the_naive_figures_are_the_ones_the_plan_recorded():
    """A guard on the independent arithmetic itself.

    `2026-10-13-replay-audit-scope.md` records the naive pass at 47 closed lots, $757.95
    and 29 unmatched sell shares. A change to the naive pass that moves these has changed
    the independent arithmetic, which is the one thing the plan's stop conditions forbid -
    so this asserts them rather than leaving them to be noticed later.
    """
    if not (ROOT / "runtime" / "autopoiesis" / "journal.jsonl").is_file():
        import pytest

        pytest.skip("no runtime journal on this host")

    out = _run().stdout
    line = next(
        (ln for ln in out.splitlines() if "FIFO closed lots=" in ln), None
    )
    assert line is not None, out[-500:]
    assert "closed lots=47" in line, (
        f"the naive pass moved; the scope plan says this arithmetic is not to be edited: {line}"
    )
    assert "757.95" in line, line


def test_the_script_still_imports_nothing_from_autopoiesis_in_its_counting_path():
    """The independence that makes a disagreement worth anything.

    A naive pass that imports the machinery it is checking can only ever agree with it,
    and the plan names that as a stop condition.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    # The one import is inside `_production_lot_figures`, deliberately, so the counting
    # above it stays free of the package.
    head = source.split("def _production_lot_figures", 1)[0]
    # Line-based, because the module docstring says the words "autopoiesis import" in
    # prose and a substring check matched that instead of an import statement.
    # Top-level only. An import inside a function is deferred deliberately - that is how
    # `_production_lot_figures` keeps the counting above it free of the package - and an
    # indented one is not a statement about the counting path.
    offenders = [
        ln.strip()
        for ln in head.splitlines()
        if not ln.startswith((" ", "\t"))
        and (ln.startswith("import autopoiesis") or ln.startswith("from autopoiesis"))
    ]

    assert offenders == [], (
        f"the counting path must not import the machinery it checks: {offenders}"
    )
