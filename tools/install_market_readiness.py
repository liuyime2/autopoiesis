"""Explicitly install reporting timers after validating staged unit definitions."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from autopoiesis.atomicio import write_text_atomic

UNIT_NAMES = (
    "vix-refresh.service", "vix-refresh.timer",
    "market-preflight.service", "market-preflight.timer",
    "market-open-verify.service", "market-open-verify.timer",
)


def install(*, root: Path, python: Path, envfile: Path, expected_fingerprint: str,
            deployment_after: str, unit_dir: Path, runner=subprocess.run):
    paths = (root, python, envfile, unit_dir)
    if not all(path.is_absolute() and re.fullmatch(r"[A-Za-z0-9_./-]+", str(path)) for path in paths):
        raise ValueError("absolute paths with safe characters required")
    root, python, envfile, unit_dir = (path.resolve() for path in paths)
    if not python.is_file() or not os.access(python, os.X_OK) or not envfile.is_file():
        raise ValueError("interpreter and external environment file required")
    if envfile.is_relative_to(root):
        raise ValueError("environment file must be outside the repository")
    if re.fullmatch(r"[0-9a-f]{16}", expected_fingerprint) is None:
        raise ValueError("16 hex expected fingerprint required")
    stamp = datetime.fromisoformat(deployment_after.replace("Z", "+00:00"))
    if stamp.tzinfo is None or re.fullmatch(r"[0-9T:+.Z-]+", deployment_after) is None:
        raise ValueError("aware deployment cutoff required")
    values = {
        "ROOT": str(root), "PYTHON": str(python), "PYTHONDIR": str(python.parent),
        "ENVFILE": str(envfile), "FINGERPRINT": expected_fingerprint, "AFTER": deployment_after,
    }
    rendered = {}
    for name in UNIT_NAMES:
        text = (root / "tools" / (name + ".in")).read_text()
        text = re.sub(r"@([A-Z]+)@", lambda match: values[match[1]], text)
        if "@" in text:
            raise ValueError("unresolved template placeholder")
        rendered[name] = text
    with tempfile.TemporaryDirectory(prefix="market-readiness-units-") as directory:
        stage = Path(directory)
        for name, text in rendered.items():
            write_text_atomic(stage / name, text)
        runner(["systemd-analyze", "--user", "verify", *(str(stage / name) for name in UNIT_NAMES)], check=True)
        for name, text in rendered.items():
            write_text_atomic(unit_dir / name, text)
    runner(["systemctl", "--user", "daemon-reload"], check=True)
    runner(["systemctl", "--user", "enable", "--now", "market-preflight.timer", "market-open-verify.timer"], check=True)
    runner(["systemctl", "--user", "list-timers", "market-preflight.timer", "market-open-verify.timer", "--no-pager"], check=True)


def main():
    try:
        install(
            root=Path(os.environ["READINESS_ROOT"]), python=Path(os.environ["READINESS_PYTHON"]),
            envfile=Path(os.environ["READINESS_ENVFILE"]), unit_dir=Path(os.environ["READINESS_UNIT_DIR"]),
            expected_fingerprint=os.environ["EXPECTED_FINGERPRINT"], deployment_after=os.environ["DEPLOYMENT_AFTER"],
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "diagnostic": "TIMER_INSTALL_FAILED", "exception_type": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
