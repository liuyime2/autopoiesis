"""A record of every research trial, including the ones that failed.

The objective requires every failed trial to be recorded, and the reason is
selection bias: keep the best of forty variants and report it as though it were the
only candidate, and the number inherits the selection without showing it. The
production side already records admission rejections, but a backtest that failed its
out-of-sample bar left **no trace at all** - `research/` wrote nothing, so a search
could be run, could fail, and could leave behind no evidence that it had happened.

The obvious way to fix that - letting research append to the production journal -
would break the separation the objective also requires, and for a good reason. A
backtest that graded itself would then be able to influence production state, and the
one invariant that keeps the research layer honest is that it cannot.

So the trial log is a **separate sink**, in its own file, read by nothing in
production. It satisfies both requirements rather than trading one for the other:

* every trial is recorded, pass or fail, with the verdict and the numbers behind it;
* production never reads it, so a research run cannot influence what trades.

Writes are append-only and atomic, and a failure to record never raises into a
research run - a trial that cannot be journalled is still worth evaluating, and losing
the run would be worse than losing the record.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

#: Kept out of the production journal's directory naming so a stray glob cannot
#: mistake one for the other.
DEFAULT_TRIALS_PATH = Path("runtime") / "min_agent" / "research_trials.jsonl"


def record_trial(
    payload: dict[str, Any],
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one trial. Returns whether it was written.

    A trial that cannot be recorded still ran, and its value is in the result rather
    than in the bookkeeping, so a write failure is reported by the return value
    rather than raised into the caller.
    """
    target = Path(path or DEFAULT_TRIALS_PATH)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    line = json.dumps({"recorded_at": stamp, **payload}, sort_keys=True)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Append with O_APPEND so concurrent research runs cannot interleave a
        # partial line, and fsync before returning so a crash cannot lose a trial
        # that the caller was told was recorded.
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return False
    return True


def read_trials(path: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded trial. A torn final line is skipped rather than raising.

    A crash mid-append is expected, and refusing to read the whole file because of it
    would mean one bad write loses the record of every trial before it.
    """
    target = Path(path or DEFAULT_TRIALS_PATH)
    if not target.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in target.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summarise(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """How many trials ran, and how many passed.

    `trials_run` is the number that matters for overfitting control: it is the
    denominator the multiple-testing gate divides by, and a report that omits it
    hides its own selection bias.
    """
    by_verdict: dict[str, int] = {}
    for trial in trials:
        verdict = str(trial.get("verdict", "UNRECORDED"))
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    passed = sum(
        n for v, n in by_verdict.items() if v.startswith("PASSES")
    )
    return {
        "trials_run": len(trials),
        "passed": passed,
        "failed": len(trials) - passed,
        "by_verdict": dict(sorted(by_verdict.items())),
    }
