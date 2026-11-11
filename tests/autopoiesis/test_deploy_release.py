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
    module, main, _tree = repo
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


class _FakeInstallers:
    """Synthetic stand-ins for the two installers; git still runs for real."""

    def __init__(self, monkeypatch, *, readiness_rc=0):
        self.calls: list[tuple[list[str], dict]] = []
        real = subprocess.run

        def fake(cmd, *args, **kwargs):
            if cmd and cmd[0] == "git":
                return real(cmd, *args, **kwargs)
            self.calls.append((list(cmd), dict(kwargs.get("env") or {})))
            rc = readiness_rc if "install-readiness-timers" in cmd else 0
            return subprocess.CompletedProcess(cmd, rc, "", "synthetic readiness failure" if rc else "")

        monkeypatch.setattr(subprocess, "run", fake)

    def readiness(self):
        return [(c, e) for c, e in self.calls if "install-readiness-timers" in c]


def test_install_service_also_refreshes_the_readiness_timers(repo, monkeypatch):
    """Measured 2026-11-11: market-preflight, market-open-verify and vix-refresh were still running
    from an October release and asserting its fingerprint, so both readiness units failed every day
    with SOURCE_MISMATCH while the deploys went through. `install-service` does not touch them;
    they have their own installer, which deploys never called."""
    module, _, _ = repo
    fake = _FakeInstallers(monkeypatch)
    monkeypatch.setenv("MAKEFLAGS", "PY=/bin/false")
    entry = module.deploy(approved_by="tester", now=SATURDAY, install_service=True)

    ((cmd, env),) = fake.readiness()
    assert cmd[:4] == ["make", "--no-print-directory", "-C", entry["release_dir"]]
    assert env["EXPECTED_FINGERPRINT"] == entry["source_fingerprint"]
    assert env["DEPLOYMENT_AFTER"] == entry["timestamp"]
    assert env["READINESS_PYTHON"] == sys.executable
    # A make variable carried through MAKEFLAGS reached the tests' child make on 2026-11-10.
    assert "MAKEFLAGS" not in env and "MAKEOVERRIDES" not in env
    assert entry["install_readiness"] == "ok"


def test_a_failed_readiness_install_fails_the_deploy_loudly(repo, monkeypatch):
    module, _, _ = repo
    _FakeInstallers(monkeypatch, readiness_rc=1)
    with pytest.raises(SystemExit, match="install-readiness-timers failed"):
        module.deploy(approved_by="tester", now=SATURDAY, install_service=True)


def test_without_install_service_no_installer_runs(repo, monkeypatch):
    module, _, _ = repo
    fake = _FakeInstallers(monkeypatch)
    module.deploy(approved_by="tester", now=SATURDAY)
    assert fake.calls == []


def _verify_module():
    path = Path(__file__).resolve().parents[2] / "tools" / "verify.py"
    spec = importlib.util.spec_from_file_location("verify_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_gate_names_a_unit_pinned_to_another_release():
    """Synthetic unit text. The live release is the one autopoiesis.service runs from."""
    verify = _verify_module()
    units = {
        "autopoiesis.service": "WorkingDirectory=/q/releases/ba4a082fa383\nExecStart=/q/releases/ba4a082fa383/x",
        "market-open-verify.service": "ExecStart=/usr/bin/make -C /q/releases/2037cdb market-open-verify",
        "information-refresh.service": "WorkingDirectory=/q/releases/ba4a082fa383",
        "ollama.service": "ExecStart=/usr/local/bin/ollama serve",
    }
    problems = verify.units_pinned_to_other_releases(units)
    assert len(problems) == 1 and "market-open-verify.service" in problems[0] and "2037cdb" in problems[0]
    units["market-open-verify.service"] = units["market-open-verify.service"].replace("2037cdb", "ba4a082fa383")
    assert verify.units_pinned_to_other_releases(units) == []


def test_without_a_live_daemon_unit_the_release_rule_says_nothing():
    verify = _verify_module()
    assert verify.units_pinned_to_other_releases({"x.service": "ExecStart=/q/releases/abc123/y"}) == []
