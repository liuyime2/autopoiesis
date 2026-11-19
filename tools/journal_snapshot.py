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
unrealized PnL, the assumed cost - are compared against the sealed snapshot. A file can be byte-identical
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
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

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
        return (
            all(a == b for a, b in self.fields.values())
            and "ledger_error" not in self.fields
            and all(value == 0 for value in self.fields.get("unparsed_lines", (0, 0)))
            and self.fields.get("archive_recovery") == ("CERTIFIED", "CERTIFIED")
        )

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
    prefix = "journal.jsonl."
    rotated = sorted(
        (p for p in runtime.glob(prefix + "*") if p.is_file()
         and p.name[len(prefix):].isdigit() and int(p.name[len(prefix):]) > 0),
        key=lambda p: int(p.name[len(prefix):]), reverse=True,
    )
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

    The manifest records each copied file's sha256 and size, plus its *semantic* figures -
    cycle count, event count, first and last timestamp - so a restored copy can be compared against
    what the snapshot claims to hold. Shared locking makes the copied bytes one source boundary.
    Without those, `verify` could only
    prove a snapshot is internally consistent, which is a weaker claim than the one a recovery
    decision needs.
    """
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"refusing to snapshot into a non-empty directory: {destination}")
    from autopoiesis.atomicio import write_json_atomic
    from autopoiesis.journal import JsonlJournal

    destination.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, object]] = []
    # The archive manifest is copied and *listed*, not copied and tracked separately. A file in the
    # snapshot that the manifest does not name is a file `verify` reports as unlisted, which is
    # what it did on the first run of this against the live record - the restored archive would
    # then be untrusted for want of a file that was there all along.
    with JsonlJournal(runtime / "journal.jsonl")._read_lock():
        files = record_files(runtime)
        if not files.all_paths():
            raise SystemExit(f"no journal record under {runtime}; nothing to snapshot")
        for source in files.all_paths():
            relative = _relative(source, runtime)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # The writer resumes immediately after copying. Hashing, fsync and evaluation
    # concern only these sealed files and cannot race a subsequent live append.
    for source in files.all_paths():
        relative = _relative(source, runtime)
        target = destination / relative
        with target.open("rb") as handle:
            os.fsync(handle.fileno())
        copied.append({"name": relative, "bytes": target.stat().st_size, "sha256": _sha256(target)})
    figures = semantic_figures(destination)
    payload = {
        "schema_version": 1,
        "source_runtime": str(runtime),
        "files": copied,
        "manifest": _relative(files.manifest, runtime) if files.manifest else None,
        "figures": figures,
    }
    write_json_atomic(destination / SNAPSHOT_MANIFEST, payload)
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

    journal = JsonlJournal(runtime / "journal.jsonl", lock=False)
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
    raw_lines = 0
    for path in journal.history_paths():
        with path.open(encoding="utf-8") as handle:
            raw_lines += sum(bool(line.strip()) for line in handle)
    figures["unparsed_lines"] = raw_lines - len(records) - len(events)
    if journal.archive_manifest_path.exists():
        status = journal.archive_manifest_status()
        figures["archive_recovery"] = (
            "CERTIFIED" if status["valid"] and status["trusted"] else "UNSUPPORTED"
        )
        figures["archive_reason"] = status["reason"]
    else:
        # Do not call archive_manifest_status on absent manifests: on an empty
        # record it writes an automatic baseline into the sealed directory.
        figures["archive_recovery"] = "UNKNOWN"
        figures["archive_reason"] = "archive manifest missing; baseline is not certified"

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
    except Exception as exc:  # Bytes are preserved, but this is not a working ledger proof.
        figures["ledger_error"] = type(exc).__name__
    return figures


def backup(runtime: Path, directory: Path) -> Path:
    """Publish a durable, verified local journal backup; retain all previous backups."""
    def sync_directory(path: Path) -> None:
        # The ordinary atomic writer tolerates unsupported directory fsync. A
        # backup must propagate it instead of claiming durability after failure.
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Existing directories may be left by a failed earlier attempt. Sync their
    # ancestor entries on every attempt; existence is not proof of durability.
    for parent in (directory, *directory.parents):
        sync_directory(parent)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    staging = directory / (".staging-" + name)
    completed = directory / name
    try:
        payload = snapshot(runtime, staging)
        ok, problems = verify(staging)
        figures = payload["figures"]
        if ("ledger_error" in figures or figures.get("unparsed_lines", 0)
                or figures.get("archive_recovery") != "CERTIFIED"):
            problems.append("semantic recovery unsupported: " + json.dumps(figures, sort_keys=True))
        if not ok or problems:
            raise SystemExit("backup was not published:\n  " + "\n  ".join(problems))
        for child in sorted((p for p in staging.rglob("*") if p.is_dir()), reverse=True):
            sync_directory(child)
        sync_directory(staging)
        os.replace(staging, completed)
        sync_directory(directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return completed


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
            "figures, sealed snapshot against restored:",
            comparison.render(),
        ]
        if problems:
            report.append("verification problems:\n  " + "\n  ".join(problems))
        return (ok and comparison.identical), "\n".join(report)


def prune_backups(
    directory: Path,
    *,
    keep_days: int = 7,
    max_bytes: int | None = None,
    execute: bool = False,
    golden_marker: str = "golden",
    now: datetime | None = None,
) -> tuple[dict[str, object], list[Path]]:
    """Prune expired backups adhering to strict invariants:

    1. NEVER prune the newest valid backup.
    2. NEVER prune a golden backup (containing golden_marker in name or .golden file).
    3. Backups younger than keep_days are retained, unless total size exceeds max_bytes.
    4. Even under max_bytes, at least the newest backup and all golden backups are preserved.
    5. Backups without a valid SNAPSHOT_MANIFEST are preserved and flagged as unmanaged.
    6. If execute is False, perform dry run only.
    """
    directory = directory.resolve()
    if not directory.exists() or not directory.is_dir():
        return {
            "total": 0,
            "kept": 0,
            "pruned": 0,
            "freed_bytes": 0,
            "kept_bytes": 0,
            "dry_run": not execute,
            "protected_newest": None,
            "golden_count": 0,
            "unmanaged_count": 0,
        }, []

    entries = [p for p in directory.iterdir() if p.is_dir() and not p.name.startswith(".staging-")]

    valid_backups: list[tuple[datetime, Path, int, bool]] = []
    unmanaged: list[Path] = []

    for p in entries:
        manifest = p / SNAPSHOT_MANIFEST
        if not manifest.exists():
            unmanaged.append(p)
            continue

        is_golden = (golden_marker in p.name.lower()) or (p / ".golden").exists()

        try:
            if len(p.name) >= 16 and p.name[8] == "T" and p.name[15] == "Z":
                ts = datetime.strptime(p.name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            else:
                ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except Exception:
            ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)

        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        valid_backups.append((ts, p, size, is_golden))

    valid_backups.sort(key=lambda item: item[0], reverse=True)

    if not valid_backups:
        return {
            "total": 0,
            "kept": 0,
            "pruned": 0,
            "freed_bytes": 0,
            "kept_bytes": sum(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) for p in unmanaged),
            "dry_run": not execute,
            "protected_newest": None,
            "golden_count": 0,
            "unmanaged_count": len(unmanaged),
        }, []

    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=keep_days)
    newest = valid_backups[0]

    to_prune: list[Path] = []
    to_keep: list[tuple[datetime, Path, int, bool]] = []

    for item in valid_backups:
        ts, p, _sz, is_golden = item
        if p == newest[1]:
            to_keep.append(item)
            continue
        if is_golden:
            to_keep.append(item)
            continue
        if ts < cutoff:
            to_prune.append(p)
        else:
            to_keep.append(item)

    if max_bytes is not None:
        kept_bytes = sum(item[2] for item in to_keep)
        if kept_bytes > max_bytes:
            eligible_for_quota = [
                item for item in to_keep
                if item[1] != newest[1] and not item[3]
            ]
            eligible_for_quota.sort(key=lambda item: item[0])
            for item in eligible_for_quota:
                if kept_bytes <= max_bytes:
                    break
                to_keep.remove(item)
                to_prune.append(item[1])
                kept_bytes -= item[2]

    if not to_keep:
        raise RuntimeError("Safety invariant violated: pruning would delete all backups!")

    freed_bytes = sum(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) for p in to_prune)
    kept_bytes = sum(item[2] for item in to_keep)

    if execute:
        for p in to_prune:
            shutil.rmtree(p)

    summary = {
        "total": len(valid_backups),
        "kept": len(to_keep),
        "pruned": len(to_prune),
        "freed_bytes": freed_bytes,
        "kept_bytes": kept_bytes,
        "dry_run": not execute,
        "protected_newest": str(newest[1].name),
        "golden_count": sum(1 for item in valid_backups if item[3]),
        "unmanaged_count": len(unmanaged),
    }
    return summary, to_prune


def _parse_bytes(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    v = str(value).strip().upper()
    if v.endswith(("GB", "G")):
        num = v.rstrip("GB").rstrip("G")
        return int(float(num) * (1024**3))
    if v.endswith(("MB", "M")):
        num = v.rstrip("MB").rstrip("M")
        return int(float(num) * (1024**2))
    return int(v)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--runtime", default=None, help="the runtime directory to snapshot")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="write the record into DEST")
    snap.add_argument("destination")

    scheduled = sub.add_parser("backup", help="atomically publish a verified local backup")
    scheduled.add_argument("directory")

    check = sub.add_parser("verify", help="check a snapshot in DIR")
    check.add_argument("directory")

    put = sub.add_parser("restore", help="restore a snapshot into DEST")
    put.add_argument("source")
    put.add_argument("destination")

    sub.add_parser("drill", help="snapshot, restore and compare, end to end")

    prune = sub.add_parser("prune", help="prune expired backups by retention window and quota")
    prune.add_argument("directory", help="the directory containing backups")
    prune.add_argument("--keep-days", type=int, default=7, help="retention window in days (default: 7)")
    prune.add_argument("--max-bytes", default=None, help="maximum disk quota (e.g. 85GB)")
    prune.add_argument("--execute", action="store_true", default=False, help="perform deletion (default: dry run)")

    args = parser.parse_args(argv)
    runtime = _runtime_from_arg(args.runtime)

    if args.command == "backup":
        print(f"backup published: {backup(runtime, Path(args.directory))}")
        return 0

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
        comparison = compare(Path(args.source), Path(args.destination))
        print(comparison.render())
        return 0 if comparison.identical else 1

    if args.command == "prune":
        max_bytes = _parse_bytes(args.max_bytes)
        summary, pruned = prune_backups(
            Path(args.directory),
            keep_days=args.keep_days,
            max_bytes=max_bytes,
            execute=args.execute,
        )
        mode = "EXECUTE" if args.execute else "DRY-RUN"
        freed_mb = summary["freed_bytes"] / (1024**2)
        kept_mb = summary["kept_bytes"] / (1024**2)
        print(f"prune [{mode}]: total={summary['total']}, kept={summary['kept']} ({kept_mb:.1f} MB), "
              f"pruned={summary['pruned']} ({freed_mb:.1f} MB), golden={summary['golden_count']}")
        if summary["protected_newest"]:
            print(f"  protected newest: {summary['protected_newest']}")
        if pruned:
            action_verb = "deleted" if args.execute else "would prune"
            print(f"  {action_verb} {len(pruned)} backup(s):")
            for p in pruned[:5]:
                print(f"    - {p.name}")
            if len(pruned) > 5:
                print(f"    ... and {len(pruned) - 5} more")
        return 0

    ok, report = drill(runtime)
    print(report)
    print(f"drill: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
