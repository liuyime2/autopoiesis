"""The benchmark's figures, as one JSON object, on stdout.

A thin wrapper over `tools/benchmark.py --json` so `tools/figures.py` can name a collector rather
than an argument, and so `make benchmark-json` has a target that says what it publishes. The
measurement is the benchmark's own - this adds nothing to it.

Exit zero means the JSON measurement was collected successfully. Its `exit_code`
and `target.status` fields describe the measurement's outcome; UNKNOWN is a valid
collected result and never means the profitability target passed. Unexpected
execution failures propagate nonzero to the collector.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "benchmark.py"), "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=3600,
    )
    # A Python exception also exits 1. Only a complete matching measurement is
    # collectable; malformed output must not be published as a successful figure.
    try:
        payload = json.loads(proc.stdout)
        target = payload["target"]
        valid = (
            proc.returncode in (0, 1, 2)
            and payload["exit_code"] == proc.returncode
            and target["status"] == {0: "PASS", 1: "FAIL", 2: "UNKNOWN"}.get(proc.returncode)
            and target["met"] is (proc.returncode == 0)
        )
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        sys.stderr.write(proc.stderr[-400:])
        sys.stderr.write("\nbenchmark collector: invalid or inconsistent measurement\n")
        return proc.returncode or 1
    sys.stdout.write(proc.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
