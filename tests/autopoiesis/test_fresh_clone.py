"""Synthetic clone/interpreter stubs test actual shell/Make environment isolation."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import verify


@pytest.mark.parametrize("failed_stage", [None, "venv", "probe"])
def test_fresh_clone_children_do_not_load_external_configuration(tmp_path, failed_stage):
    repo = tmp_path / "synthetic-repo"
    evidence = repo / "docs" / "evidence"
    evidence.mkdir(parents=True)
    script = evidence / "run-fresh-clone.sh"
    shutil.copyfile(ROOT / "docs/evidence/run-fresh-clone.sh", script)
    external = tmp_path / "synthetic-external-env"
    external.write_text("SYNTHETIC_EXTERNAL_CONFIG := loaded\n")
    fixture = tmp_path / "synthetic-clone-content"
    fixture.mkdir()
    make_source = (ROOT / "Makefile").read_text()
    config = make_source[make_source.index("AUTOPOIESIS_ENV_FILE ?="):make_source.index(".DEFAULT_GOAL")]
    (fixture / "Makefile").write_text(config + "\ninstall lint check smoke-offline test example-cycle cli-help reproduce verify-self-test verify:\n"
        '\t@test -z "$(SYNTHETIC_EXTERNAL_CONFIG)"\n'
        '\t@test -z "$$AUTOPOIESIS_JOURNAL"\n')
    bins = tmp_path / "synthetic-bin"
    bins.mkdir()
    git = bins / "git"
    git.write_text('#!/usr/bin/env python3\nimport os, shutil, sys\n'
        'if sys.argv[1] == "clone": shutil.copytree(os.environ["SYNTHETIC_CLONE_FIXTURE"], sys.argv[-1])\n'
        'else: print("synthetic-commit fixture only")\n')
    git.chmod(0o755)
    python = bins / "synthetic-python"
    python.write_text('#!/usr/bin/env python3\nimport os, pathlib, sys\n'
        'if sys.argv[1:3] == ["-m", "venv"]:\n'
        '    bins = pathlib.Path(sys.argv[3]) / "bin"\n'
        '    bins.mkdir(parents=True)\n'
        '    child = bins / "python"\n'
        '    child.write_text("#!/bin/sh\\nif [ \\"$1\\" = \\"-c\\" ]; then exit \\"$SYNTHETIC_PROBE_EXIT\\"; fi\\nexit 0\\n")\n'
        '    child.chmod(0o755)\n'
        '    (bins / "activate").write_text("export VIRTUAL_ENV=synthetic\\n")\n'
        '    sys.exit(int(os.environ["SYNTHETIC_SETUP_EXIT"]))\n')
    python.chmod(0o755)
    result = subprocess.run(["bash", str(script), str(tmp_path / "synthetic-destination")],
        cwd=repo, env={**os.environ, "PATH": f"{bins}:{os.environ['PATH']}",
            "MAKEFLAGS": "", "MFLAGS": "",
            "PYTHON": str(python), "SYNTHETIC_CLONE_FIXTURE": str(fixture),
            "SYNTHETIC_SETUP_EXIT": "9" if failed_stage == "venv" else "0",
            "SYNTHETIC_PROBE_EXIT": "9" if failed_stage == "probe" else "0",
            "ENVFILE": str(external), "AUTOPOIESIS_ENV_FILE": str(external),
            "AUTOPOIESIS_JOURNAL": "/synthetic/operator-runtime.jsonl"},
        capture_output=True, text=True, timeout=30)
    log = (evidence / "fresh-clone.log").read_text()
    if failed_stage:
        assert result.returncode != 0
        assert "RESULT: FAIL" in log
        assert "RESULT: PASS" not in log
        return
    assert result.returncode == 0, result.stderr + log
    assert "RESULT: FAIL" not in log
    assert log.count("RESULT: PASS") == 9


@pytest.fixture
def synthetic_unit_templates(tmp_path, monkeypatch):
    """Copy real template/renderer inputs; every install output stays in a private fixture."""
    root = tmp_path / "synthetic-repo"
    tools = root / "tools"
    tools.mkdir(parents=True)
    for pattern in ("*.service.in", "*.timer", "*.timer.in"):
        for template in (ROOT / "tools").glob(pattern):
            shutil.copyfile(template, tools / template.name)
    shutil.copyfile(ROOT / "tools/install_market_readiness.py", tools / "install_market_readiness.py")
    shutil.copyfile(ROOT / "autopoiesis", root / "autopoiesis")
    monkeypatch.setattr(verify, "ROOT", root)
    return root


def test_unit_template_gate_covers_all_fourteen_actual_templates(synthetic_unit_templates, monkeypatch):
    def forbidden_command(*args, **kwargs):
        pytest.fail("template verification must never execute a service command")

    monkeypatch.setattr(subprocess, "run", forbidden_command)
    result = verify.check_unit_templates_have_no_host_paths()
    assert result.status == verify.PASS, result.detail
    assert "14 unit template(s)" in result.detail


@pytest.mark.parametrize("mutation", [
    "unknown-marker", "raw-marker", "missing-value", "wrong-value", "empty-value",
    "disabled-replacement", "missing-service-owner", "missing-timer-owner", "timer-host-path",
    "unclaimed-template", "comment-only-substitution", "echo-only-substitution", "missing-shell-owner",
])
def test_unit_template_gate_rejects_synthetic_broken_renderers(synthetic_unit_templates, mutation):
    root = synthetic_unit_templates
    installer = root / "tools/install_market_readiness.py"
    source = installer.read_text()
    service = root / "tools/market-preflight.service.in"
    if mutation == "unknown-marker":
        service.write_text(service.read_text() + "Environment=SYNTHETIC=@UNKNOWN@\n")
    elif mutation == "raw-marker":
        service.write_text(service.read_text() + "Environment=SYNTHETIC=@root-broken\n")
    elif mutation == "missing-value":
        source = source.replace('"FINGERPRINT": expected_fingerprint, ', "")
    elif mutation == "wrong-value":
        source = source.replace('"FINGERPRINT": expected_fingerprint', '"FINGERPRINT": "b" * 16')
    elif mutation == "empty-value":
        source = source.replace('"PYTHONDIR": str(python.parent)', '"PYTHONDIR": ""')
    elif mutation == "disabled-replacement":
        source = source.replace('text = re.sub(r"@([A-Z]+)@", lambda match: values[match[1]], text)',
            'text = re.sub(r"DOES_NOT_MATCH", lambda match: "", text)')
    elif mutation in {"missing-service-owner", "missing-timer-owner"}:
        name = "market-preflight.service" if mutation == "missing-service-owner" else "market-preflight.timer"
        source = source.replace(f'"{name}",', "", 1)
    elif mutation == "timer-host-path":
        timer = root / "tools/market-preflight.timer.in"
        timer.write_text(timer.read_text() + "EnvironmentFile=/home/synthetic/operator-env\n")
    elif mutation == "unclaimed-template":
        (root / "tools/unclaimed.service.in").write_text("WorkingDirectory=@ROOT@\n")
    elif mutation == "comment-only-substitution":
        shell = root / "autopoiesis"
        shell.write_text(shell.read_text().replace('s|@PYTHON@|$PYTHON_BIN|g', 's|UNUSED|$PYTHON_BIN|g')
            + "\n# @PYTHON@ appears only in this synthetic comment\n")
    elif mutation == "echo-only-substitution":
        shell = root / "autopoiesis"
        shell.write_text(shell.read_text().replace('s|@PYTHON@|$PYTHON_BIN|g', 's|UNUSED|$PYTHON_BIN|g')
            .replace('render() {', 'render() {\n      echo "s|@PYTHON@|$PYTHON_BIN|g"'))
    elif mutation == "missing-shell-owner":
        shell = root / "autopoiesis"
        shell.write_text(shell.read_text().replace('    render "$ROOT_DIR/tools/research.service.in"',
            '    # render "$ROOT_DIR/tools/research.service.in"'))
    installer.write_text(source)
    result = verify.check_unit_templates_have_no_host_paths()
    assert result.status == verify.FAIL, result.detail
    if mutation in {"unclaimed-template", "missing-service-owner", "missing-timer-owner", "missing-shell-owner"}:
        assert "no renderer owner" in result.detail
    elif mutation == "timer-host-path":
        assert "market-preflight.timer.in" in result.detail and "hardcodes a host path" in result.detail
    elif mutation in {"comment-only-substitution", "echo-only-substitution"}:
        assert "shell render never substitutes" in result.detail
    else:
        assert "readiness renderer" in result.detail
