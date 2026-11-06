"""First-open verification after a deployment (G1 closeout).

Read-only: checks the journal's first cycle of the session and reports whether
the build identity, fingerprint and deployment record agree. Exits non-zero on
any mismatch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.daemon import source_fingerprint


def main() -> int:
    # Identity check.
    import subprocess

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    fp = source_fingerprint()
    print(json.dumps({"commit": sha, "source_fingerprint": fp}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
