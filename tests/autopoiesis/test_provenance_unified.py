"""One provenance record, or a reader assembling one from three files.

The objective asks for config, commit hash, environment, seed, dataset version, output paths and
core metrics so any result can be traced back to what produced it. Those existed. Two more things a
result depends on did not: the dataset a research verdict was judged on, and whether the system
producing it was actually live at the time.

Both are now in the same record. A verdict quoted without its dataset fingerprint cannot be
compared with another verdict, because the tape has no version number and two runs' tapes are not
the same tape. A figure produced while the model had stopped answering is a different kind of figure
from one produced while everything was live, and only the heartbeat knows which.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("provenance", ROOT / "tools" / "provenance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["provenance"] = module
    spec.loader.exec_module(module)
    return module


def test_the_record_carries_the_research_dataset_fingerprint(tmp_path, monkeypatch):
    """The tape has no version number, so the fingerprint is the only way to compare two verdicts."""
    module = _load()
    ledger = tmp_path / "runtime" / "autopoiesis" / "research_trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"strategy_id": "old", "bars": 100, "fingerprint": "sha256:aaaa"}) + "\n"
        + json.dumps({"strategy_id": "new", "bars": 200, "fingerprint": "sha256:bbbb"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "RUNTIME", tmp_path / "runtime" / "autopoiesis")

    newest_first = module._research_fingerprints()
    assert newest_first == ["sha256:bbbb", "sha256:aaaa"], (
        "the newest trial is the one a fresh verdict was judged against, so it comes first"
    )


def test_a_ledger_without_fingerprints_says_so_rather_than_claiming_one(tmp_path, monkeypatch):
    """Legacy rows predate the field. Absent is absent, not invented.

    A reader who needs to know whether a verdict is comparable gets "no fingerprint", which is the
    truth, rather than a guess at which tape it was.
    """
    module = _load()
    ledger = tmp_path / "runtime" / "autopoiesis" / "research_trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"strategy_id": "legacy", "bars": 24764}) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "RUNTIME", tmp_path / "runtime" / "autopoiesis")

    assert module._research_fingerprints() == []


def test_the_record_carries_the_daemon_state(tmp_path, monkeypatch):
    """Whether the system was live is part of what a figure means.

    A figure produced while the model had stopped answering is a different kind of figure from one
    produced while everything worked, and the heartbeat is the only place that knows.
    """
    module = _load()
    runtime = tmp_path / "runtime" / "autopoiesis"
    runtime.mkdir(parents=True)
    (runtime / "heartbeat.json").write_text(
        json.dumps({
            "status": "RUNNING", "pid": 4242, "source_fingerprint": "abc123",
            "last_cycle_age_seconds": 48 * 3600,
            "last_broker_ok": True, "last_model_ok": False,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "RUNTIME", runtime)

    state = module._daemon_state()
    assert state["status"] == "RUNNING"
    assert state["last_broker_ok"] is True
    assert state["last_model_ok"] is False, (
        "a model that stopped answering must be visible in the provenance of the results it "
        "influenced"
    )
    assert state["last_cycle_age_seconds"] == 48 * 3600


_HEARTBEAT = {
    "status": "RUNNING",
    "pid": 4242,
    "cycle_count": 1284,
    "error_count": 0,
    "source_fingerprint": "sha256:synthetic",
    "last_cycle_age_seconds": 42,
    "last_broker_ok": True,
    "last_model_ok": True,
}


def _run_main(module) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main()
    return buffer.getvalue()


def test_the_published_record_contains_both_new_sections(tmp_path, monkeypatch):
    """The end-to-end check on what the tool prints, on a runtime it can have anywhere.

    **Synthetic, and deliberately so.** The version this replaces ran `main()` against whatever
    this host had in `runtime/`, so on a fresh clone - no journal, no heartbeat - the daemon
    section printed "(no heartbeat on this host)" and the assertions on `status:` failed. The
    suite went 1 failed / 1319 passed in a clean checkout and 1323 passed here, and the difference
    was exactly this test: every green run on this machine was leaning on this machine's state.

    The heartbeat below is fabricated, and the value it is being used for is not a claim about
    this host. It is a fixed input to the question the test actually asks: does a field the helper
    computes reach the printed record? That question needs a heartbeat to exist, not a real one.
    """
    module = _load()
    runtime = tmp_path / "runtime" / "autopoiesis"
    runtime.mkdir(parents=True)
    (runtime / "heartbeat.json").write_text(json.dumps(_HEARTBEAT), encoding="utf-8")
    (runtime / "research_trials.jsonl").write_text(
        json.dumps({"strategy_id": "t1", "bars": 100, "fingerprint": "sha256:aaaa"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "RUNTIME", runtime)

    text = _run_main(module)

    for heading in ("## code", "## configuration in effect", "## research data",
                    "## daemon state at the time of this record"):
        assert heading in text, f"{heading} is missing from the provenance record"
    assert "status:" in text
    assert "broker answered:" in text
    assert "model answered:" in text
    assert "sha256:synthetic" in text, (
        "the daemon state has to carry what the helper read, not a placeholder"
    )


def test_the_real_record_on_this_host_still_prints_its_daemon_state():
    """The published record, on the runtime this host actually produced.

    The mechanism test above proves the sections are emitted given a heartbeat. This one keeps the
    original assertion - the real printed record - because that is the artefact a reader opens. It
    skips, with the reason, on a checkout with no running system: a fresh clone has no heartbeat,
    and asserting the real record exists there would be asserting this machine.
    """
    module = _load()
    if not (module.RUNTIME / "heartbeat.json").exists():
        pytest.skip(
            "no runtime/heartbeat.json in this checkout; the real record is produced by a running "
            "system, so the printed record can only be checked where one ran. The mechanism is "
            "covered by test_the_published_record_contains_both_new_sections."
        )
    text = _run_main(module)
    assert "status:" in text
    assert "broker answered:" in text
    assert "model answered:" in text
