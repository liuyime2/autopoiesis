"""The signal ledger's verdicts, counted from the ledger itself.

Every hypothesis about what predicts returns goes through `research/signal_test.py` and lands in
`runtime/autopoiesis/signal_trials.jsonl`. The site quotes the totals; this counts them, because
the research scripts are the only thing that should decide that number.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "runtime" / "autopoiesis" / "signal_trials.jsonl"


def main() -> int:
    if not LEDGER.exists():
        # No ledger on this checkout. The site says so rather than showing zero, which would
        # read as "nothing was tested" when the truth is "this machine has not run the research".
        print(json.dumps({"present": False}))
        return 0
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    verdicts = collections.Counter(str(r.get("verdict")) for r in rows)
    distinct = {r.get("name") for r in rows if r.get("name")}
    print(json.dumps({
        "present": True,
        "rows": len(rows),
        "hypotheses": len(distinct),
        "verdicts": dict(sorted(verdicts.items(), key=lambda kv: -kv[1])),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
