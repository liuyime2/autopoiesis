"""The journal's own extent, as the benchmark states it.

Not the journal's line count: a page that quotes one number and the CLI another is the drift this
project keeps finding. The benchmark already computes the window from the journal, so this reads
the same object it prints.
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
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write("the benchmark produced no JSON object\n")
        return 1
    print(json.dumps(data.get("record") or {}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
