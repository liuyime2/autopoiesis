"""Synthetic clone/interpreter stubs test actual shell/Make environment isolation."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


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
