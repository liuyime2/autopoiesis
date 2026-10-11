"""The gate's own count, for the site.

`verify.py --list` prints the class count; this runs the gate's listing rather than re-deriving
it, because a site that states how many classes exist must state the number the gate states.
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
        [sys.executable, str(ROOT / "tools" / "verify.py"), "--list"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    match = re.search(r"^(\d+) classes on a full run", proc.stdout, re.MULTILINE)
    if not match:
        sys.stderr.write("could not read a class count from verify.py --list\n")
        return 1
    print(json.dumps({"check_classes": int(match.group(1))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
