"""How many tests there are, counted rather than asserted.

`--collect-only -q` is what counts: a number typed into a page is a number nobody can check,
and this project has already had two gates fail on exactly that drift.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    )
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    if not match:
        sys.stderr.write(proc.stdout[-400:])
        return 1
    files = len(set(re.findall(r"^(tests/\S+\.py)::", proc.stdout, re.MULTILINE)))
    print(json.dumps({"tests": int(match.group(1)), "test_files": files}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
