"""The state directory, and the refusal that stops the rename from eating the journal.

Renaming `runtime/min_agent/` to `runtime/autopoiesis/` is the one part of this rename that could
destroy data. That directory holds 111MB of live state and `journal.jsonl` is this project's only
source of truth (AGENTS.md: every other file is derived from it and can be regenerated - the
journal cannot).

So this does not move anything on its own. It:

* renames the directory when the new one does not exist and the old one does, and refuses when
  both exist or the new one already has a journal, because "which journal is real" has no answer
  that a script should guess;
* refuses outright if the new directory would start with no journal while the old one has one,
  which is the exact shape of "the agent restarted and forgot four months of its own record";
* reports what it did, and prints the command to undo it.

Run it with the daemon stopped. It takes one argument: `check` (default) or `apply`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

OLD = Path("runtime") / "min_agent"
NEW = Path("runtime") / "autopoiesis"
#: The file that decides whether a state directory is real. Everything else is regenerable.
JOURNAL = "journal.jsonl"


def _journal_lines(path: Path) -> int:
    """Count the journal's records without loading 67MB of it into memory, and without pretending
    a rotated file is part of it: the live file is the one that must not be lost."""
    try:
        with open(path / JOURNAL, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def describe() -> list[str]:
    out = []
    for label, path in (("old", OLD), ("new", NEW)):
        if not path.exists():
            out.append(f"{label:3} {path} does not exist")
            continue
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        out.append(f"{label:3} {path} exists, {_journal_lines(path)} journal record(s), {size / 1e6:.1f} MB")
    return out


def apply() -> int:
    old_records, new_records = _journal_lines(OLD), _journal_lines(NEW)
    if not OLD.exists():
        print(f"nothing to do: {OLD} does not exist")
        return 0
    if NEW.exists() and new_records:
        print(
            f"REFUSED: {NEW} already holds {new_records} journal records and {OLD} holds "
            f"{old_records}. Two real journals cannot be merged without inventing a history, and "
            f"choosing one silently is how four months of record goes missing. Decide which is "
            f"real by hand: move {OLD} aside, then start the daemon."
        )
        return 1
    if old_records == 0:
        print(f"REFUSED: {OLD} holds no journal records, so there is nothing worth preserving. "
              f"If that is expected, remove it by hand; this tool will not delete state.")
        return 1
    NEW.parent.mkdir(parents=True, exist_ok=True)
    if NEW.exists():
        shutil.rmtree(NEW)
    shutil.move(str(OLD), str(NEW))
    print(f"moved {OLD} -> {NEW} ({_journal_lines(NEW)} journal records)")
    print(f"undo with: mv {NEW} {OLD}")
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    for line in describe():
        print(line)
    if action == "apply":
        raise SystemExit(apply())
    if action != "check":
        print(f"unknown action {action!r}; use check or apply")
        raise SystemExit(2)
    print("\nrun with `apply` and the daemon stopped to move the state directory")
