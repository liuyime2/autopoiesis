"""Record that the full gate passed, for the build that is about to be installed.

AGENTS.md 19 says a deploy needs the full gate green on the commit being deployed, and the fast
gate green at install time. The second half is enforced - `deploy-gate-record` fails when the
installed fingerprint has no passing full-gate record. The first half was prose, and prose is what
gets skipped when someone is in a hurry.

So this tool exists to make the first half a fact rather than a claim: it **runs the gate itself**
and writes down the verdict it observed. A record an operator types is a record an operator can
type wrongly, and the failure mode is invisible - the deployment record says the gate passed, and
it did, on a different tree, at a different time. Nothing but running it can tell the difference.

It refuses to write anything when the gate is red. A ledger that can hold a red run is a ledger
that gets read as "the gate was run", and the one thing a red run must never become is a reason
someone felt they had done the paperwork.

Usage:
    python3 tools/record_deployment.py            # run the gate, then append
    python3 tools/record_deployment.py --dry-run  # print the line, write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "evidence" / "deployments.jsonl"

#: The gate's own tally line, e.g. `classes: 56   passed: 56   failed: 0   test executions: ...`.
#: Parsed rather than inferred: the count this check compares against is the number the gate
#: reported, so a partial run recorded here is caught later by `deploy-gate-record` instead of
#: quietly satisfying it.
TALLY = re.compile(r"classes:\s*(\d+)\s+passed:\s*(\d+)\s+failed:\s*(\d+)")

#: The class names under the gate's `FAIL - the gate is red:` listing.
FAILED_CLASSES = re.compile(r"^ {2}([a-z0-9-]+):", re.MULTILINE)

#: The one class that cannot be green while this tool writes the record it asks for.
#:
#: `deploy-gate-record` fails when the installed build has no record, and writing that record is
#: this tool's whole job - so the gate it runs is red for the reason the tool exists, every time.
#: Refusing on it deadlocked the first attempt: the gate was red, so nothing was written, so the
#: gate stayed red. Naming it here rather than waiving the gate keeps the exception countable -
#: the entry stores the failing classes, so a record accepted for any other reason is visible.
BOOTSTRAP_CLASS = "deploy-gate-record"


def _fingerprint() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from autopoiesis.daemon import source_fingerprint

    return source_fingerprint()


def _head_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise SystemExit(f"could not read HEAD: {proc.stderr.strip()}")
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the line and write nothing")
    parser.add_argument("--note", default="", help="one sentence of context, stored as-is")
    args = parser.parse_args()

    print("running the full gate; this takes about 23 minutes on the deployment host", flush=True)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "verify.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5400,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    tail = "\n".join(proc.stdout.strip().splitlines()[-12:])
    print(tail, flush=True)

    match = TALLY.search(proc.stdout)
    if not match:
        print("the gate printed no tally line, so there is nothing to record", file=sys.stderr)
        return 2
    classes, passed, failed = (int(group) for group in match.groups())
    failing = sorted(set(FAILED_CLASSES.findall(proc.stdout.split("FAIL - the gate is red:")[-1])))
    # A red gate is not recorded, with one named exception: the check that demands this record
    # cannot be green while the record is being written. `failed == 1` as well as the name, because
    # a tally saying two classes failed while only one name parsed is a gate this tool cannot read,
    # and recording on a gate it cannot read is the failure this whole file exists to prevent.
    # Storing the failing classes in the entry keeps the exception countable instead of invisible.
    bootstrap_only = failing == [BOOTSTRAP_CLASS] and failed == 1
    if failed and not bootstrap_only:
        print(
            f"the gate is red ({passed}/{classes}) on {', '.join(failing) or 'unnamed classes'}; "
            "nothing was written. A ledger that can hold a red run would be read as proof the gate "
            "was run.",
            file=sys.stderr,
        )
        return 1

    entry = {
        "fingerprint": _fingerprint(),
        "commit": _head_commit(),
        # When the gate observed this build, not when it was installed. The deploy happens after
        # this tool runs, and a field named `deployed_at` that holds the earlier moment would be
        # the kind of small lie that makes a whole record untrustworthy.
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "gate": {
            "classes": classes,
            "passed": passed,
            "failed": failed,
            # Recorded even when empty, and it is the whole reason this is honest: a record
            # accepted because the only red was the bootstrap check says so in the file, rather
            # than reading afterwards as a clean 57/57 that nobody can distinguish from one.
            "failed_classes": failing,
            "commit": _head_commit(),
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            # Which tier produced the verdict. The check requires "full" rather than comparing the
            # count against the current one, and the reason is a deadlock this hit on the first run:
            # adding a check class makes the full count 57 while an existing record says 56, so the
            # record fails - and this tool cannot write a new one, because it refuses to write when
            # the gate is red, and the gate is red because the record is old. A count that must
            # match today is a count that cannot be extended, and the tool that fixes the record
            # could never run. The tier is written by the tool rather than typed, which is what
            # makes it worth checking.
            #
            # Inside `gate`, where the reader looks: the first two entries in the ledger put it at
            # the entry level, and the reader accepts both rather than rewriting a produced
            # artefact - docs/evidence/README.md says these files are regenerated by running, never
            # by editing.
            "tier": "full",
        },
    }
    if args.note:
        entry["note"] = args.note
    line = json.dumps(entry, sort_keys=False)

    if args.dry_run:
        print(f"dry run, nothing written:\n{line}")
        return 0

    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(f"appended to {LEDGER.relative_to(ROOT)}:\n{line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
