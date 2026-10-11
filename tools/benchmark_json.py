"""The benchmark's figures, as one JSON object, on stdout.

A thin wrapper over `tools/benchmark.py --json` so `tools/figures.py` can name a collector rather
than an argument, and so `make benchmark-json` has a target that says what it publishes. The
measurement is the benchmark's own - this adds nothing to it.

The exit code is the benchmark's contract (0 met, 1 behind, 2 nothing to measure) and is passed
through: a collector that swallows it would let "the target is not met" read as "the figure was
collected".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "benchmark.py"), "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=3600,
    )
    # stdout of the benchmark is the JSON object; its prose goes to a sink already.
    sys.stdout.write(proc.stdout)
    if proc.returncode not in (0, 1, 2):
        sys.stderr.write(proc.stderr[-400:])
        return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
