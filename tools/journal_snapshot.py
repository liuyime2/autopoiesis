"""Snapshot the journal record, restore it somewhere else, and prove the two agree.

What this is for. The journal is the only source of truth, and until now nothing could move it or
check that a copy was whole. Recovery was an assertion, not a demonstration: the README claims the
record can be regenerated from the journal, and nothing had ever verified that a copy of the
journal, taken out from under a running daemon and put down somewhere else, still says the same
thing.

Three commands and one drill:

    snapshot DEST    write the whole record into DEST, with its own manifest
    verify DIR       check a snapshot is whole, without restoring it
    restore SRC DST  restore a snapshot into DST and verify it reads back the same
    drill            snapshot to a scratch dir, restore to another, compare the numbers

**Restore never writes to the live runtime.** A restore target is an argument and is refused if it
is the configured runtime directory. That refusal is the whole safety property of this tool: the
failure mode being prevented is an operator restoring a stale snapshot over the live record and
destroying the only copy that was right.

**What "the same" means.** Two checks, and the second is the one that matters. The first is byte
identity: every file's sha256 in the snapshot manifest matches the file that was restored. That
proves nothing was corrupted in transit and is the easy half. The second is *semantic* identity:
the restored journal is read through this project's own reader and evaluator, and the figures that
depend on it - cycle count, event count, first and last timestamp, closed lots, realized and
unrealized PnL, the assumed cost - are compared against the live record. A file can be byte-identical
and still be a record of a different system if it is the wrong file; the figures are what catch that.

**What is not claimed.** This snapshots and restores on one host. It does not ship anything
anywhere: an off-site copy is a deployment decision this tool makes possible and does not make. The
snapshot is self-contained and its manifest is verifiable, so it can be copied by any means and
checked afterwards with `verify`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_RUNTIME = ROOT / "runtime" / "autopoiesis"
SNAPSHOT_MANIFEST = "snapshot.json"


@dataclass
class RecordFiles:
    """Every file that together are the record, as (relative path, source) pairs.

    The *relative* path is part of the record, not a convenience. `history_paths()` finds the
    archived generations under `<runtime>/journal-archive/`, so a copy that flattens them to the
    top level is a copy whose reader cannot see them - and the record silently becomes shorter.
    The first version of this tool flattened them, and only the generation-count comparison in the
    drill caught it.
    """

    live: Path | None
    rotated: list[Path]
    archived: list[Path]
    manifest: Path | None

    def all_paths(self) -> list[Path]:
        paths = [self.live] if self.live else []
        paths.extend(self.rotated)
        paths.extend(self.archived)
        if self.manifest is not None:
            paths.append(self.manifest)
        return [p for p in paths if p is not None]



@dataclass
class Comparison:
    """The semantic comparison of two records."""

    fields: dict[str, tuple[object, object]] = field(default_factory=dict)

    def add(self, name: str, live: object, restored: object) -> None:
        self.fields[name] = (live, restored)

    @property
    def identical(self) -> bool:
        return all(a == b for a, b in self.fields.values())

    def render(self) -> str:
        if not self.fields:
            return "nothing compared"
        lines = []
        for name, (live, restored) in self.fields.items():
            mark = "same" if live == restored else "DIFFERENT"
            lines.append(f"  {name:26s} live={live!r:>22}  restored={restored!r:<22} {mark}")
        return "\n".join(lines)


def _runtime_from_arg(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_RUNTIME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, runtime: Path) -> str:
    """`path` relative to the runtime directory, as a posix path.

    The archive manifest and the archived generations live one directory down; the live journal and
    its rotated generations live at the top. Preserving that is what makes a restored copy a runtime
    directory rather than a pile of files.
    """
    return path.relative_to(runtime).as_posix()


def record_files(runtime: Path) -> RecordFiles:
    """The live journal, its rotated generations, the archived ones and the manifest."""
    live = runtime / "journal.jsonl"
    rotated = []
    for index in range(1, 20):
        candidate = runtime / f"journal.jsonl.{index}"
        if candidate.exists():
            rotated.append(candidate)
    archive_dir = runtime / "journal-archive"
    archived = sorted(p for p in archive_dir.glob("*.jsonl") if p.is_file()) if archive_dir.exists() else []
    manifest = archive_dir / "journal-manifest.json"
    return RecordFiles(
        live=live if live.exists() else None,
        rotated=rotated,
        archived=archived,
        manifest=manifest if manifest.exists() else None,
    )


def snapshot(runtime: Path, destination: Path) -> dict:
    """Copy the record into `destination` and write a manifest describing what was copied.

    The manifest records each file's sha256 and size, plus the *semantic* figures of the source -
    cycle count, event count, first and last timestamp - so a restored copy can be compared against
    what the snapshot claims to hold, not merely against itself. Without those, `verify` could only
    prove a snapshot is internally consistent, which is a weaker claim than the one a recovery
    decision needs.
    """
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"refusing to snapshot into a non-empty directory: {destination}")
    files = record_files(runtime)
    if not files.all_paths():
        raise SystemExit(f"no journal record under {runtime}; nothing to snapshot")

    destination.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, object]] = []
    # The archive manifest is copied and *listed*, not copied and tracked separately. A file in the
    # snapshot that the manifest does not name is a file `verify` reports as unlisted, which is
    # what it did on the first run of this against the live record - the restored archive would
    # then be untrusted for want of a file that was there all along.
    for source in files.all_paths():
        # The relative path from the runtime directory is preserved: `journal-archive/r000000.jsonl`
        # stays under `journal-archive/`. A flat copy would put the archive where `history_paths()`
        # does not look, and the restored record would read as the live generations alone.
        relative = _relative(source, runtime)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({
            "name": relative,
            "bytes": source.stat().st_size,
            "sha256": _sha256(source),
        })
    figures = semantic_figures(runtime)
    payload = {
        "schema_version": 1,
        "source_runtime": str(runtime),
        "files": copied,
        "manifest": _relative(files.manifest, runtime) if files.manifest else None,
        "figures": figures,
    }
    (destination / SNAPSHOT_MANIFEST).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def verify(directory: Path) -> tuple[bool, list[str]]:
    """Check a snapshot is whole: every file it names, and every file it names only.

    An extra file is a problem and a missing one is a problem. Both directions are checked, because
    a restore that silently dropped a rotated generation would leave a record one generation shorter
    and nothing downstream would say so.
    """
    manifest_path = directory / SNAPSHOT_MANIFEST
    if not manifest_path.exists():
        return False, [f"no {SNAPSHOT_MANIFEST} in {directory}"]
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    named = {entry["name"] for entry in payload["files"]}
    # Recursive, because the archive lives one directory down and a snapshot's files keep their
    # runtime-relative paths. `iterdir()` alone sees only the top level, so the archived
    # generations were reported absent from a snapshot that contained them - which is what the
    # first run of this did against the real record.
    present = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.relative_to(directory).as_posix() != SNAPSHOT_MANIFEST
    }
    for missing in sorted(named - present):
        problems.append(f"file listed in the snapshot is absent: {missing}")
    for extra in sorted(present - named):
        problems.append(f"file present in the snapshot is not listed: {extra}")
    for entry in payload["files"]:
        path = directory / str(entry["name"])
        if not path.exists():
            continue  # already reported above
        if path.stat().st_size != entry["bytes"]:
            problems.append(f"{entry['name']}: size {path.stat().st_size} against {entry['bytes']}")
        actual = _sha256(path)
        if actual != entry["sha256"]:
            problems.append(f"{entry['name']}: sha256 {actual[:12]}... against {entry['sha256'][:12]}...")
    return (not problems), problems


def restore(source: Path, destination: Path, runtime: Path) -> dict:
    """Copy a verified snapshot into `destination`.

    Refuses a destination that is the configured runtime directory - restoring over the live record
    is the failure this tool exists to prevent, and a surprised operator is the person who does it.
    """
    resolved_runtime = runtime.resolve()
    resolved_destination = destination.resolve()
    if resolved_destination == resolved_runtime:
        raise SystemExit(
            f"refusing to restore into the live runtime directory {runtime}; "
            "restore to a scratch directory and compare it with `verify`"
        )
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"refusing to restore into a non-empty directory: {destination}")

    ok, problems = verify(source)
    if not ok:
        raise SystemExit("the snapshot does not verify, so it is not restored:\n  " + "\n  ".join(problems))

    # Exactly the files the snapshot lists, and nothing else. `snapshot.json` itself is deliberately
    # not copied: it describes the snapshot, it is not part of the record, and a restored directory
    # containing it would fail its own `verify` as an unlisted file - which is what the first run of
    # this did. A restored directory is a runtime directory, and a runtime directory has no
    # snapshot manifest in it.
    destination.mkdir(parents=True, exist_ok=True)
    payload = json.loads((source / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    for entry in payload["files"]:
        relative = str(entry["name"])
        target = destination / relative
        # The subdirectory is created rather than assumed: an archived generation's name carries
        # `journal-archive/` and the flat copy would fail on a directory that does not exist yet.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    return payload


def semantic_figures(runtime: Path) -> dict:
    """The record's figures, read through this project's own reader and evaluator.

    This is what a restore is compared on. Byte identity proves the copy is the copy; these prove
    the copy is a record of the same system, which is what a recovery decision actually needs.
    """
    from autopoiesis.journal import JsonlJournal

    journal = JsonlJournal(runtime / "journal.jsonl")
    figures: dict[str, object] = {}
    records = journal.read_all()
    events = journal.read_events()
    figures["cycles"] = len(records)
    figures["events"] = len(events)
    stamps = sorted(
        r.snapshot.timestamp for r in records if getattr(r.snapshot, "timestamp", None)
    )
    figures["first_cycle"] = stamps[0].isoformat() if stamps else None
    figures["last_cycle"] = stamps[-1].isoformat() if stamps else None
    figures["generations"] = len(journal.history_paths())

    # The ledger figures the evaluator's headline is built from: closed lots, realized and
    # unrealized PnL, and the assumed cost. These are the numbers a recovery has to reproduce, and
    # they are a property of the journal alone - the broker evidence batch is *in* the journal as
    # journalled events, which is what makes a restored copy able to report them at all. That is
    # the whole reason this works from a snapshot rather than needing the broker.
    try:
        from autopoiesis.broker_evidence import latest_evidence_batch
        from autopoiesis.evaluator import _pnl_evidence, confirmed_fill_activities

        evidence = latest_evidence_batch(journal)
        seeded = confirmed_fill_activities(journal, records)
        pnl = _pnl_evidence(records, evidence, seeded)
        figures["closed_lots"] = len(pnl.closed_lots)
        figures["realized_pnl"] = round(sum(pnl.strategy_realized_pnl.values()), 6)
        figures["unrealized_pnl"] = pnl.unrealized_pnl
        figures["assumed_cost"] = round(sum(pnl.assumed_cost.values()), 6)
        figures["pnl_evidence_status"] = pnl.status
    except Exception as exc:  # a record too old for the current evaluator still restores
        figures["ledger_error"] = type(exc).__name__
    return figures


def compare(source: Path, restored: Path) -> Comparison:
    """Compare the figures a snapshot claims against the figures the restored copy reports."""
    claimed = json.loads((source / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))["figures"]
    actual = semantic_figures(restored)
    comparison = Comparison()
    for name in sorted(set(claimed) | set(actual)):
        comparison.add(name, claimed.get(name), actual.get(name))
    return comparison


def verify_restored(source: Path, restored: Path) -> tuple[bool, list[str]]:
    """Check a restored directory against the snapshot it came from.

    A restored directory is a runtime directory, not a snapshot: it has no `snapshot.json` and must
    not have one. So it is checked against the snapshot's own listing - every file the snapshot
    named, with the sha256 it recorded - and against nothing else. Calling `verify` on a restored
    directory asks it for a manifest it correctly does not have.
    """
    payload = json.loads((source / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in payload["files"]:
        path = restored / str(entry["name"])
        if not path.exists():
            problems.append(f"not restored: {entry['name']}")
            continue
        if path.stat().st_size != entry["bytes"]:
            problems.append(
                f"{entry['name']}: size {path.stat().st_size} against {entry['bytes']}"
            )
        if _sha256(path) != entry["sha256"]:
            problems.append(f"{entry['name']}: content differs from the snapshot")
    return (not problems), problems


def drill(runtime: Path) -> tuple[bool, str]:
    """Snapshot, restore, and compare - the whole path, end to end, on this run.

    A drill that only checked bytes would prove the copy mechanism. Comparing the evaluator's
    figures proves the copy is a working record, which is the only kind of recovery worth having.
    """
    with tempfile.TemporaryDirectory(prefix="journal-drill-") as scratch:
        base = Path(scratch)
        snap, back = base / "snapshot", base / "restored"
        snapshot(runtime, snap)
        restore(snap, back, runtime)
        comparison = compare(snap, back)
        ok, problems = verify_restored(snap, back)
        report = [
            f"snapshot: {len(json.loads((snap / SNAPSHOT_MANIFEST).read_text())['files'])} file(s)",
            f"restored copy matches the snapshot byte for byte: {'yes' if ok else 'no'}",
            "figures, live against restored:",
            comparison.render(),
        ]
        if problems:
            report.append("verification problems:\n  " + "\n  ".join(problems))
        return (ok and comparison.identical), "\n".join(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--runtime", default=None, help="the runtime directory to snapshot")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="write the record into DEST")
    snap.add_argument("destination")

    check = sub.add_parser("verify", help="check a snapshot in DIR")
    check.add_argument("directory")

    put = sub.add_parser("restore", help="restore a snapshot into DEST")
    put.add_argument("source")
    put.add_argument("destination")

    sub.add_parser("drill", help="snapshot, restore and compare, end to end")

    args = parser.parse_args(argv)
    runtime = _runtime_from_arg(args.runtime)

    if args.command == "snapshot":
        payload = snapshot(runtime, Path(args.destination))
        print(f"snapshot: {len(payload['files'])} file(s) into {args.destination}")
        print(json.dumps(payload["figures"], indent=2, sort_keys=True))
        return 0

    if args.command == "verify":
        ok, problems = verify(Path(args.directory))
        if ok:
            print(f"verify: {args.directory} is whole")
            return 0
        print(f"verify: {args.directory} is NOT whole")
        for problem in problems:
            print(f"  {problem}")
        return 1

    if args.command == "restore":
        restore(Path(args.source), Path(args.destination), runtime)
        print(f"restored {args.source} into {args.destination}")
        return 0

    ok, report = drill(runtime)
    print(report)
    print(f"drill: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
