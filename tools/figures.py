"""Figures the public site quotes, produced by the command that produced them.

The site is a public claim about what this project has measured, so every number on it comes from
here rather than from prose. That is the whole point of the file: a figure typed into a page rots
the moment the code moves, and a reader has no way to tell a current number from a stale one.
Here each figure carries the command that produced it and the date it was produced, so a stale
number is visible as stale rather than believed.

**Why a file rather than reading runtime state at generation time.** The site must build on a
fresh clone, where `runtime/` does not exist. Reading the live journal would make the page
unbuildable for anybody without a paper account - and would publish one deployment's numbers as
though they were the project's. So the measurements are *published*: `make refresh-figures` runs
the commands on an operator's machine and writes this file, `make site` renders it, and both are
committed together. The date field is what tells a reader how old a published measurement is.

Nothing in here is an account position, a credential or a host path. The published figures are
aggregates over the record on disk, and the record is not published.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEFAULT = ROOT / "docs" / "site" / "figures.json"

#: The commands, in the order their output is written. Each returns one JSON object; the key it
#: lands under is the first element of the tuple.
_COLLECTORS = (
    ("benchmark", [sys.executable, "tools/benchmark_json.py"]),
    ("gate", [sys.executable, "tools/gate_summary.py"]),
    ("record", [sys.executable, "tools/record_summary.py"]),
    ("signals", [sys.executable, "tools/signal_summary.py"]),
    ("tests", [sys.executable, "tools/test_summary.py"]),
)


def collect() -> dict:
    """Run every collector and return what they reported.

    A collector that fails is reported as such rather than omitted: a site missing one figure is
    indistinguishable from a project that has nothing to report, and the page says which of the
    two it is.
    """
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "figures": {}}
    for key, command in _COLLECTORS:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0:
            out["figures"][key] = {"error": proc.stderr.strip()[:400] or f"exit {proc.returncode}"}
            continue
        try:
            out["figures"][key] = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            out["figures"][key] = {"error": f"unparseable output from {' '.join(command)}: {exc}"}
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    target = Path(argv[0]) if argv else DEFAULT
    payload = collect()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    broken = [k for k, v in payload["figures"].items() if "error" in v]
    print(f"wrote {target}")
    for key, value in payload["figures"].items():
        if "error" in value:
            print(f"  {key}: FAILED - {value['error'][:120]}")
        else:
            print(f"  {key}: ok")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
