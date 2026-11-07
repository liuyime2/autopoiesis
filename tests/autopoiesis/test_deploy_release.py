"""tools/deploy_release.py against a throwaway git repository (no real release is touched)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "deploy_release.py"
SATURDAY = datetime(2026, 11, 7, 17, 0, tzinfo=timezone.utc)
TUESDAY_NOON_ET = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A main checkout with a linked worktree, as on the host: the tool runs from the worktree."""
    monkeypatch.delenv("AUTOPOIESIS_MARKET_OPEN", raising=False)
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@example.invalid")
    _git(main, "config", "user.name", "t")
    (main / "autopoiesis").write_text("#!/bin/sh\necho ok\n")
    (main / "autopoiesis").chmod(0o755)
    (main / "docs" / "evidence").mkdir(parents=True)
    (main / "docs" / "evidence" / "deployments.jsonl").write_text(
        json.dumps({"commit": "abc123", "fingerprint": "f" * 16, "recorded_at": "2026-11-05T13:47:00-05:00"}) + "\n"
    )
    (main / "runtime").mkdir()
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "init")
    tree = tmp_path / "main" / ".worktrees" / "branch"
    _git(main, "worktree", "add", "-q", "-b", "branch", str(tree))

    spec = importlib.util.spec_from_file_location("deploy_release_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tree)
    root = module._main_root(tree)
    monkeypatch.setattr(module, "MAIN_ROOT", root)
    monkeypatch.setattr(module, "LOCK_FILE", root / "runtime" / "deploy.lock")
    monkeypatch.setattr(module, "RELEASES_DIR", root / "releases")
    monkeypatch.setattr(module, "DEPLOYMENTS_LEDGER", tree / "docs" / "evidence" / "release-actions.jsonl")
    monkeypatch.setattr(module, "GATE_LEDGER", tree / "docs" / "evidence" / "deployments.jsonl")
    monkeypatch.setattr(module, "_git_sha", lambda: subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tree, text=True).strip())
    return module, main, tree


def test_a_release_lands_beside_the_main_checkout_with_the_runtime_linked(repo):
    module, main, tree = repo
    entry = module.deploy(approved_by="tester", now=SATURDAY)

    release = Path(entry["release_dir"])
    assert release.parent == main / "releases", "releases belong to the main checkout, not the worktree"
    assert (release / "runtime").is_symlink() and (release / "runtime").resolve() == (main / "runtime").resolve()
    assert os.access(release / "autopoiesis", os.X_OK), "the entry script must stay executable"
    assert entry["rollback_to"] == "abc123"


def test_its_record_does_not_go_into_the_gate_ledger(repo):
    """equity_curve.cohort_start reads row["fingerprint"] from deployments.jsonl; this entry has none."""
    module, _, tree = repo
    module.deploy(approved_by="tester", now=SATURDAY)

    gate_rows = (tree / "docs" / "evidence" / "deployments.jsonl").read_text().splitlines()
    assert all("fingerprint" in json.loads(r) for r in gate_rows)
    assert (tree / "docs" / "evidence" / "release-actions.jsonl").exists()


def test_a_second_deploy_of_the_same_commit_and_a_market_hours_deploy_are_refused(repo):
    module, _, _ = repo
    module.deploy(approved_by="tester", now=SATURDAY)
    with pytest.raises(SystemExit, match="already exists"):
        module.deploy(approved_by="tester", now=SATURDAY)
    with pytest.raises(SystemExit, match="market is open"):
        module.deploy(approved_by="tester", now=TUESDAY_NOON_ET)
