"""Print a fixed identity snapshot of the running tree.

Used by step 0.1 of docs/EXECUTION_PLAN_2026-11-05.md: every deploy must be
able to state, in one command, which commit, which code on disk, which process,
and which configuration it is. The values are read from live state, never typed.

Output is JSON on stdout. Secrets are never included: only the names of the
environment variables that are set, plus the (non-secret) configuration paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Environment variables whose *presence* (not value) is reported. Values are never printed.
REPORTED_ENV_VARS = (
    "AUTOPOIESIS_MODEL",
    "OLLAMA_BASE_URL",
    "OLLAMA_BIN",
    "OLLAMA_MODELS",
    "AUTOPOIESIS_MODE",
    "ALPACA_BASE_URL",
    "XDG_CONFIG_HOME",
)


def _git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip()


def _git_short_sha() -> str:
    sha = _git_sha()
    return sha[:12] if sha != "unknown" else sha


def _source_fingerprint() -> str:
    """Mirror of autopoiesis.daemon.source_fingerprint, without importing the package."""
    package_dir = ROOT / "src" / "autopoiesis"
    digest = hashlib.sha256()
    try:
        for path in sorted(package_dir.glob("*.py")):
            digest.update(path.name.encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
    except OSError:
        return "unknown"
    return digest.hexdigest()[:16]


def _env_summary() -> dict[str, object]:
    present = [name for name in REPORTED_ENV_VARS if os.environ.get(name)]
    return {
        "present": present,
        "count": len(present),
        "config_file": os.environ.get("AUTOPOIESIS_ENV_FILE"),
    }


def _model_digest() -> str:
    """A digest of the configured model identity (name + base url), never of weights."""
    model = os.environ.get("AUTOPOIESIS_MODEL", "")
    base = os.environ.get("OLLAMA_BASE_URL", "")
    return hashlib.sha256(f"{model}|{base}".encode()).hexdigest()[:16]


def build_snapshot() -> dict[str, object]:
    return {
        "commit": _git_sha(),
        "commit_short": _git_short_sha(),
        "pid": os.getpid(),
        "cwd": str(Path.cwd().resolve()),
        "root": str(ROOT),
        "source_fingerprint": _source_fingerprint(),
        "model": os.environ.get("AUTOPOIESIS_MODEL", ""),
        "model_digest": _model_digest(),
        "env": _env_summary(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON (default)")
    parser.parse_args(argv)
    snapshot = build_snapshot()
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

