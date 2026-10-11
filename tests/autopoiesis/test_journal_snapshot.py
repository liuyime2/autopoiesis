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

import json
import sys
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
    """A runtime directory with a journal, a rotated generation and an archive."""
    module = _load()
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "journal.jsonl").write_text(
        '{"timestamp": "2026-10-02T12:00:00Z", "cycle_id": "c3"}\n'
        '{"timestamp": "2026-10-03T12:00:00Z", "cycle_id": "c4"}\n',
        encoding="utf-8",
    )
    (rt / "journal.jsonl.1").write_text(
        '{"timestamp": "2026-10-01T12:00:00Z", "cycle_id": "c2"}\n', encoding="utf-8"
    )
    archive = rt / "journal-archive"
    archive.mkdir()
    (archive / "r000000.jsonl").write_text(
        '{"timestamp": "2026-09-30T12:00:00Z", "cycle_id": "c1"}\n', encoding="utf-8"
    )
    (archive / "journal-manifest.json").write_text(
        json.dumps({"schema_version": 1, "next_generation": 1, "entries": [],
                    "baseline_attestation": {"operator_id": "test"}}),
        encoding="utf-8",
    )
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
