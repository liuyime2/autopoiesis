"""A backup that has never been restored is a claim, not a capability.

The drill in `tools/journal_snapshot.py` does the whole path on the real record. These tests pin
the properties that path depends on, on fixtures small enough to assert about, and the safety one
that a real run cannot exercise without risking the live journal:

- restore refuses to write to the live runtime directory - the failure mode being prevented is an
  operator restoring a stale snapshot over the record that was right;
- a snapshot that does not verify is not restored;
- a restored directory holds the record and not the snapshot's own manifest;
- a record restored from a snapshot reproduces the ledger figures the journal produces, not merely
  the bytes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    import importlib.util

    name = f"journal_snapshot_{id(object()):x}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "journal_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def runtime(tmp_path):
    """Explicitly synthetic valid cycles in live, rotated and archived generations."""
    from autopoiesis.models import (
        AccountSnapshot,
        CycleRecord,
        DataSnapshot,
        ExecutionResult,
        GuardianResult,
        TradeDecision,
    )

    def cycle(number):
        return CycleRecord(
            cycle_id=f"c{number}",
            snapshot=DataSnapshot(
                symbol="SPY", timestamp=datetime(2026, 10, number, 12, tzinfo=timezone.utc),
                market_open=True, last_price=100, source="synthetic-test",
                account=AccountSnapshot(equity=1000, cash=1000, buying_power=1000,
                                        portfolio_value=1000, daily_loss=0),
            ),
            decision=TradeDecision(symbol="SPY", action="HOLD", quantity=0,
                                   confidence=0.2, rationale="synthetic test"),
            guardian=GuardianResult(approved=True, reason="synthetic test"),
            execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0,
                                      message="synthetic test"),
        ).model_dump_json() + "\n"
    module = _load()
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "journal.jsonl").write_text(
        cycle(3) + cycle(4),
        encoding="utf-8",
    )
    (rt / "journal.jsonl.1").write_text(
        cycle(2), encoding="utf-8"
    )
    # Match the production writer's three retained generations. Empty synthetic
    # generations contribute no cycles but their presence satisfies the manifest.
    (rt / "journal.jsonl.2").write_text("", encoding="utf-8")
    (rt / "journal.jsonl.3").write_text("", encoding="utf-8")
    archive = rt / "journal-archive"
    archive.mkdir()
    (archive / "r000000.jsonl").write_text(
        cycle(1), encoding="utf-8"
    )
    (archive / "journal-manifest.json").write_text(
        json.dumps({"schema_version": 1, "next_generation": 1, "entries": [{
            "name": "r000000.jsonl", "bytes": (archive / "r000000.jsonl").stat().st_size,
            "sha256": hashlib.sha256((archive / "r000000.jsonl").read_bytes()).hexdigest(),
        }], "baseline_attestation": {
            "operator_id": "synthetic-test", "evidence_reference": "synthetic-complete-history",
            "reason": "explicitly synthetic recovery fixture", "attested_at": "2026-10-01T00:00:00Z",
        }}),
        encoding="utf-8",
    )
    from autopoiesis.journal import JsonlJournal

    assert JsonlJournal(rt / "journal.jsonl").archive_manifest_status()["trusted"]
    return module, rt


def test_a_snapshot_holds_every_generation_and_verifies(runtime, tmp_path):
    module, rt = runtime
    snap = tmp_path / "snap"
    payload = module.snapshot(rt, snap)

    names = [entry["name"] for entry in payload["files"]]
    assert "journal.jsonl" in names
    assert "journal.jsonl.1" in names
    # The relative path is preserved, not flattened. `history_paths()` finds the archive under
    # `journal-archive/`, so a flat copy would be a record whose own reader cannot see part of it.
    assert "journal-archive/r000000.jsonl" in names
    # The archive manifest is part of the record: a restored archive without it is untrusted again,
    # which is how the first version of this left a "restored" copy the daemon would refuse.
    assert "journal-archive/journal-manifest.json" in names

    ok, problems = module.verify(snap)
    assert ok, problems
    assert (snap / "journal-archive" / "r000000.jsonl").exists()


def test_a_snapshot_with_a_missing_file_does_not_verify(runtime, tmp_path):
    module, rt = runtime
    snap = tmp_path / "snap"
    module.snapshot(rt, snap)
    (snap / "journal.jsonl.1").unlink()

    ok, problems = module.verify(snap)
    assert ok is False
    assert any("absent" in problem for problem in problems)


def test_a_snapshot_with_a_corrupted_file_does_not_verify(runtime, tmp_path):
    """A byte changed in transit is caught by the manifest's sha256, not by the file being openable."""
    module, rt = runtime
    snap = tmp_path / "snap"
    module.snapshot(rt, snap)
    target = snap / "journal-archive" / "r000000.jsonl"
    target.write_text(target.read_text(encoding="utf-8") + '{"torn": true}\n', encoding="utf-8")

    ok, problems = module.verify(snap)
    assert ok is False
    assert any("sha256" in problem or "size" in problem for problem in problems)


def test_a_snapshot_with_an_extra_file_does_not_verify(runtime, tmp_path):
    """An unnamed file is a problem, not harmless: it is how a stray or foreign file gets restored."""
    module, rt = runtime
    snap = tmp_path / "snap"
    module.snapshot(rt, snap)
    (snap / "not-part-of-the-record.jsonl").write_text("{}\n", encoding="utf-8")

    ok, problems = module.verify(snap)
    assert ok is False
    assert any("not listed" in problem for problem in problems)


def test_a_snapshot_that_does_not_verify_is_not_restored(runtime, tmp_path):
    module, rt = runtime
    snap = tmp_path / "snap"
    module.snapshot(rt, snap)
    (snap / "journal.jsonl").unlink()
    out = tmp_path / "restored"

    with pytest.raises(SystemExit) as excinfo:
        module.restore(snap, out, rt)
    assert "does not verify" in str(excinfo.value)
    assert not out.exists(), "a snapshot that failed verification must not be restored at all"


def test_restore_refuses_to_write_to_the_live_runtime(runtime, tmp_path):
    """The failure mode being prevented, stated as a test.

    Restoring a stale snapshot over the live record destroys the only copy that was right, and the
    operator who does it is surprised. The refusal is at the tool boundary rather than in a warning
    nobody reads.
    """
    module, rt = runtime
    snap = tmp_path / "snap"
    module.snapshot(rt, snap)
    before = (rt / "journal.jsonl").read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        module.restore(snap, rt, rt)
    assert "live runtime" in str(excinfo.value)
    assert (rt / "journal.jsonl").read_text(encoding="utf-8") == before


def test_a_restored_directory_holds_the_record_and_not_the_snapshot_manifest(runtime, tmp_path):
    """A restored directory is a runtime directory.

    It has no `snapshot.json` and must not have one - that file describes the snapshot, it is not
    part of the record. The first version copied it and the restored directory then failed its own
    verification as an unlisted file.
    """
    module, rt = runtime
    snap = tmp_path / "snap"
    module.snapshot(rt, snap)
    out = tmp_path / "restored"
    module.restore(snap, out, rt)

    assert not (out / "snapshot.json").exists()
    assert (out / "journal.jsonl").exists()
    assert (out / "journal.jsonl.1").exists()
    # The archive is restored where the reader looks for it.
    assert (out / "journal-archive" / "r000000.jsonl").exists()
    assert (out / "journal-archive" / "journal-manifest.json").exists()
    ok, problems = module.verify_restored(snap, out)
    assert ok, problems


def test_the_restored_record_reads_back_the_same_generations(runtime, tmp_path):
    """Reading the restored copy through the project's own reader sees the same history.

    Byte identity is the easy half. This is the one that catches a restored copy that is the right
    bytes of the wrong record: the generations must reconstruct in order, oldest first, through
    `history_paths()` - the same reader that made the live record what it is.
    """
    module, rt = runtime
    sys.path.insert(0, str(ROOT / "src"))
    from autopoiesis.journal import JsonlJournal

    snap = tmp_path / "snap"
    module.snapshot(rt, snap)
    out = tmp_path / "restored"
    module.restore(snap, out, rt)

    live_paths = [p.name for p in JsonlJournal(rt / "journal.jsonl").history_paths()]
    restored_paths = [p.name for p in JsonlJournal(out / "journal.jsonl").history_paths()]
    assert restored_paths == live_paths
    assert restored_paths[0] == "r000000.jsonl", "the archive is read first, oldest first"
    assert restored_paths[-1] == "journal.jsonl"


def test_the_drill_reports_the_ledger_figures(runtime, tmp_path):
    """The drill compares ledger figures, not only bytes - and on a fixture they are all present.

    On the real record the comparison carries closed lots and realized PnL. On a fixture with no
    evidence journalled, the evaluator reports MISSING and those fields are simply absent, which the
    comparison renders rather than hiding - a figure that cannot be computed must read as absent,
    not as zero.
    """
    module, rt = runtime
    ok, report = module.drill(rt)
    assert ok is True
    assert "restored copy matches the snapshot byte for byte: yes" in report
    assert "DIFFERENT" not in report


def test_snapshot_recovers_every_actual_generation(runtime, tmp_path):
    from autopoiesis.journal import JsonlJournal

    module, rt = runtime
    (rt / "journal.jsonl.21").write_text(
        (rt / "journal.jsonl.1").read_text().replace('"c2"', '"deep"'), encoding="utf-8"
    )
    payload = module.snapshot(rt, tmp_path / "snap")
    assert "journal.jsonl.21" in {entry["name"] for entry in payload["files"]}
    assert payload["figures"]["cycles"] == 5
    assert [r.cycle_id for r in JsonlJournal(tmp_path / "snap" / "journal.jsonl", lock=False).read_all()] == [
        "c1", "deep", "c2", "c3", "c4",
    ]


def test_snapshot_manifest_describes_copied_bytes_not_later_live_append(runtime, tmp_path, monkeypatch):
    module, rt = runtime
    original_copy = module.shutil.copy2

    def copy_then_append(source, target):
        result = original_copy(source, target)
        if Path(source).name == "journal.jsonl":
            # Deliberately bypass locking to isolate the sealed-byte requirement.
            with (rt / "journal.jsonl").open("a") as handle:
                handle.write((rt / "journal.jsonl.1").read_text())
        return result

    monkeypatch.setattr(module.shutil, "copy2", copy_then_append)
    payload = module.snapshot(rt, tmp_path / "snap")
    assert payload["figures"]["cycles"] == 4
    assert module.verify(tmp_path / "snap") == (True, [])
    assert not (tmp_path / "snap" / "journal.jsonl.lock").exists()


def test_drill_fails_when_reader_drops_unparsed_history(runtime):
    module, rt = runtime
    with (rt / "journal.jsonl").open("a") as handle:
        handle.write('{"torn":true}\n')
    ok, report = module.drill(rt)
    assert not ok
    assert "unparsed_lines" in report


def test_drill_does_not_accept_equal_ledger_errors(runtime, monkeypatch):
    from autopoiesis import evaluator

    module, rt = runtime

    def failed_ledger(*args, **kwargs):
        raise ValueError("synthetic evaluation failure")

    monkeypatch.setattr(evaluator, "_pnl_evidence", failed_ledger)
    ok, report = module.drill(rt)
    assert not ok
    assert "ledger_error" in report


def test_snapshot_holds_shared_lock_through_copy_then_releases_before_evaluation(runtime, tmp_path, monkeypatch):
    """Synthetic writer exclusion is checked with the actual nonblocking OS lock."""
    import fcntl

    from autopoiesis.journal import JsonlJournal

    module, rt = runtime
    original_copy = module.shutil.copy2
    original_figures = module.semantic_figures
    lock_path = rt / "journal.jsonl.lock"
    copied_live = False

    def copy_while_locked(source, target):
        nonlocal copied_live
        with lock_path.open("a+") as handle, pytest.raises(BlockingIOError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        copied_live = copied_live or Path(source).name == "journal.jsonl"
        return original_copy(source, target)

    def evaluate_after_writer_resumes(directory):
        assert copied_live
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        # A genuine append and rotation after the copied boundary must not affect it.
        writer = JsonlJournal(rt / "journal.jsonl", max_bytes=1)
        writer.append(writer.read_all()[-1].model_copy(update={"cycle_id": "later"}))
        return original_figures(directory)

    monkeypatch.setattr(module.shutil, "copy2", copy_while_locked)
    monkeypatch.setattr(module, "semantic_figures", evaluate_after_writer_resumes)
    payload = module.snapshot(rt, tmp_path / "snap")
    assert payload["figures"]["cycles"] == 4
    assert len(JsonlJournal(rt / "journal.jsonl").read_all()) == 5
    assert module.verify(tmp_path / "snap") == (True, [])


def test_scheduled_backup_is_atomically_published_and_retains_previous_copy(runtime, tmp_path, monkeypatch):
    module, rt = runtime
    original_replace = module.os.replace
    published = []

    def check_publication(source, target):
        if Path(source).is_dir():
            assert Path(source).name.startswith(".staging-")
            assert module.verify(Path(source)) == (True, [])
            assert not Path(target).exists()
            published.append(Path(target))
        return original_replace(source, target)

    monkeypatch.setattr(module.os, "replace", check_publication)
    first = module.backup(rt, tmp_path / "backups")
    second = module.backup(rt, tmp_path / "backups")
    assert first != second
    assert published == [first, second]
    assert set((tmp_path / "backups").iterdir()) == {first, second}


def test_scheduled_backup_does_not_publish_broken_semantic_recovery(runtime, tmp_path):
    module, rt = runtime
    with (rt / "journal.jsonl").open("a") as handle:
        handle.write("synthetic torn line\n")
    with pytest.raises(SystemExit, match="semantic recovery unsupported"):
        module.backup(rt, tmp_path / "backups")
    assert list((tmp_path / "backups").iterdir()) == []


def test_backup_does_not_claim_durability_when_directory_fsync_fails(runtime, tmp_path, monkeypatch):
    import stat

    module, rt = runtime
    original_fsync = module.os.fsync

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(module.os.fstat(fd).st_mode):
            raise OSError("synthetic directory fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="directory fsync failure"):
        module.backup(rt, tmp_path / "backups")
    assert list((tmp_path / "backups").iterdir()) == []


def test_first_backup_persists_new_root_and_intermediate_parent_entries(runtime, tmp_path, monkeypatch):
    module, rt = runtime
    directory = tmp_path / "new-parent" / "backups"
    original_fsync = module.os.fsync
    original_replace = module.os.replace
    synced = set()

    def record_fsync(fd):
        info = module.os.fstat(fd)
        synced.add((info.st_dev, info.st_ino))
        return original_fsync(fd)

    def check_ancestors_before_publication(source, target):
        if Path(source).is_dir():
            for parent in (tmp_path, directory.parent):
                info = parent.stat()
                assert (info.st_dev, info.st_ino) in synced
        return original_replace(source, target)

    monkeypatch.setattr(module.os, "fsync", record_fsync)
    monkeypatch.setattr(module.os, "replace", check_ancestors_before_publication)
    module.backup(rt, directory)


def test_new_backup_root_parent_fsync_failure_prevents_publication(runtime, tmp_path, monkeypatch):
    module, rt = runtime
    directory = tmp_path / "new-parent" / "backups"
    original_fsync = module.os.fsync
    parent = tmp_path.stat()

    def fail_new_entry_fsync(fd):
        info = module.os.fstat(fd)
        if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
            raise OSError("synthetic new-parent fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_new_entry_fsync)
    with pytest.raises(OSError, match="new-parent fsync failure"):
        module.backup(rt, directory)
    assert not list(directory.iterdir())


def test_backup_retry_persists_parent_entries_left_by_failed_creation(runtime, tmp_path, monkeypatch):
    module, rt = runtime
    directory = tmp_path / "new-parent" / "backups"
    original_fsync = module.os.fsync
    original_replace = module.os.replace
    ancestor = tmp_path.stat()
    ancestor_id = (ancestor.st_dev, ancestor.st_ino)

    def fail_first_creation(fd):
        info = module.os.fstat(fd)
        if (info.st_dev, info.st_ino) == ancestor_id:
            raise OSError("synthetic first creation failure")
        return original_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_first_creation)
    with pytest.raises(OSError, match="first creation failure"):
        module.backup(rt, directory)
    assert directory.exists()
    synced = set()

    def record_retry_sync(fd):
        info = module.os.fstat(fd)
        synced.add((info.st_dev, info.st_ino))
        return original_fsync(fd)

    def check_retry_publication(source, target):
        if Path(source).is_dir():
            assert ancestor_id in synced
        return original_replace(source, target)

    monkeypatch.setattr(module.os, "fsync", record_retry_sync)
    monkeypatch.setattr(module.os, "replace", check_retry_publication)
    module.backup(rt, directory)


@pytest.mark.parametrize("manifest_problem", ["missing", "invalid", "unattested", "changed-checksum"])
def test_archive_integrity_cannot_be_certified_by_equal_snapshot_bytes(runtime, tmp_path, manifest_problem):
    module, rt = runtime
    manifest = rt / "journal-archive" / "journal-manifest.json"
    if manifest_problem == "missing":
        manifest.unlink()
    else:
        payload = json.loads(manifest.read_text())
        if manifest_problem == "invalid":
            payload["entries"] = []
        elif manifest_problem == "unattested":
            payload["baseline_attestation"] = None
        else:
            payload["entries"][0]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
    before = {p.relative_to(rt): p.read_bytes() for p in rt.rglob("*") if p.is_file()}
    snapshot = tmp_path / "byte-preservation"
    module.snapshot(rt, snapshot)
    assert module.verify(snapshot) == (True, [])
    ok, report = module.drill(rt)
    assert not ok
    assert "archive" in report
    with pytest.raises(SystemExit, match="semantic recovery unsupported"):
        module.backup(rt, tmp_path / "completed-backups")
    assert not list((tmp_path / "completed-backups").iterdir())
    after = {p.relative_to(rt): p.read_bytes() for p in rt.rglob("*") if p.is_file()
             and p.name != "journal.jsonl.lock"}
    assert after == before
