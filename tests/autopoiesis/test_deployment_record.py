"""The deployment record refuses a verdict that does not belong to the tree it names.

`tools/record_deployment.py` runs the full gate and appends what it observed. Two properties make the
entry worth reading, and this file exists to keep both: a red gate is never recorded, and a tree that
changed while the gate was running is never recorded. The second was missing until 2026-10-20 - the
tool took the commit and the fingerprint only *after* the run, so a twenty-five minute gate over one
tree could be stamped with another tree's identity, which is the failure the ledger exists to prevent.

Usage: pytest tests/autopoiesis/test_deployment_record.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_tool():
    spec = importlib.util.spec_from_file_location("record_deployment", ROOT / "tools" / "record_deployment.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["record_deployment"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


def _refusal(tool, **overrides):
    arguments = dict(
        classes=59,
        passed=59,
        failed=0,
        failing=[],
        fingerprint_before="aaaa1111aaaa1111",
        fingerprint_after="aaaa1111aaaa1111",
        commit_before="aaaaaaa",
        commit_after="aaaaaaa",
    )
    arguments.update(overrides)
    return tool._refusal(**arguments)


def test_a_green_gate_on_an_unchanged_tree_is_recorded(tool):
    assert _refusal(tool) is None


def test_a_red_gate_is_not_recorded(tool):
    reason = _refusal(tool, passed=57, failed=2, failing=["doctor", "unit-integration"])
    assert reason is not None
    assert "the gate is red" in reason
    assert "doctor" in reason, "the refusal names what failed, or an operator cannot act on it"


def test_the_bootstrap_class_alone_is_the_one_allowed_red(tool):
    """`deploy-gate-record` cannot be green while the record it demands is being written."""
    assert _refusal(tool, passed=58, failed=1, failing=["deploy-gate-record"]) is None


def test_a_tally_that_does_not_match_the_names_is_not_accepted_as_bootstrap(tool):
    """Two failures with one name parsed is a gate this tool cannot read, not a bootstrap."""
    reason = _refusal(tool, passed=57, failed=2, failing=["deploy-gate-record"])
    assert reason is not None
    assert "the gate is red" in reason


def test_a_tree_that_changed_during_the_run_is_not_recorded(tool):
    """The 2026-10-20 finding: the identity was taken after the run, not before it."""
    reason = _refusal(tool, fingerprint_after="bbbb2222bbbb2222", commit_after="bbbbbbb")
    assert reason is not None
    assert "changed while the gate was running" in reason
    assert "aaaaaaa" in reason and "bbbbbbb" in reason, "both ends must be named to be diagnosable"


def test_a_commit_that_changed_during_the_run_is_not_recorded(tool):
    assert _refusal(tool, commit_after="bbbbbbb") is not None
