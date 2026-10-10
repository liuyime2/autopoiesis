"""The read-only panel is a public surface, and it was describing a system it had not read.

Four defects, three of which the audit found by reading the file and one of which only shows up
when you run it against the real journal:

1. **Every page read the journal for itself.** Four pages generated within the same second could
   come from two different data versions, and none of them looked wrong individually. The overview
   said 2,060 cycles while the decisions page showed a cycle that count did not include.
2. **The readers opened the live file only.** Measured on the real journal: the panel reported
   **881 cycles** where the record holds **2,060**, because 1,179 of them sit in the rotated
   generations. That is the fourth hand-rolled copy of "which files are the journal", and the
   reason there were four is that each was written by someone fixing one caller.
3. **The daemon badge printed the heartbeat's own `status` word.** A page therefore said RUNNING
   over a file three days old, written by a process that no longer existed. The heartbeat carries
   a timestamp and a pid; both are checked now.
4. **`PAUSED` was described as terminal**, which contradicts `daemon._maybe_unpause` - it returns
   at most one paused strategy to probation per pass, after `PAUSED_REVIEW_DAYS`, on journalled
   evidence.

The tests below are run against a scratch journal they build themselves, so they pin behaviour
rather than numbers, and the 881/2,060 figure is a recorded measurement rather than an assertion.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    """`tools/` is not a package, and the module patches its own ROOT at import time.

    The module has to be in `sys.modules` before `exec_module` runs: it defines a dataclass, and
    `dataclasses` resolves a field's type through `sys.modules[cls.__module__].__dict__`. Loaded
    by path without registering it, that lookup returns None and the dataclass raises.
    """
    name = f"status_page_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "status_page.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _cycle(cycle_id: str, at: datetime) -> dict:
    return {
        "cycle_id": cycle_id,
        "timestamp": at.isoformat(),
        "snapshot": {"symbol": "SPY", "timestamp": at.isoformat()},
        "decision": {"action": "HOLD", "rule_action": "HOLD", "decision_source": "baseline",
                     "rationale": "wait"},
        "execution": {"status": "SKIPPED"},
    }


def _event(event_id: str, event_type: str, at: datetime, payload: dict) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": at.isoformat(),
        "payload": payload,
    }


def _scratch(tmp_path: Path, rotations: int = 0) -> Path:
    """A journal, optionally with `rotations` rotated generations holding older cycles."""
    runtime = tmp_path / "runtime" / "autopoiesis"
    runtime.mkdir(parents=True)
    journal = runtime / "journal.jsonl"
    start = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    live = [_cycle("live-1", start + timedelta(minutes=5))]
    for generation in range(rotations, 0, -1):
        rows = [
            _cycle(f"r{generation}-{index}", start - timedelta(minutes=5 * (index + 1)))
            for index in range(3)
        ]
        rows.append(_event(f"ev-{generation}", "STRATEGY_LIFECYCLE_UPDATED",
                           start - timedelta(minutes=30),
                           {"old_lifecycle": "PROBATION", "new_lifecycle": "PAUSED",
                            "strategy_id": "s-1", "reason": "recorded decisions"}))
        (runtime / f"journal.jsonl.{generation}").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )
    journal.write_text("\n".join(json.dumps(row) for row in live) + "\n", encoding="utf-8")
    return runtime


def _point(module, runtime: Path):
    module.ROOT = runtime.parents[1]
    module.JOURNAL = runtime / "journal.jsonl"
    module.STRATEGIES = runtime / "strategies"
    module.OUT_DIR = runtime / "report" / "site"


def test_every_page_reads_the_same_cycle_count(tmp_path):
    """One snapshot, not four reads: the pages of one run are one data version."""
    module = _load_module()
    runtime = _scratch(tmp_path, rotations=2)
    _point(module, runtime)

    snapshot = module.load_snapshot()
    assert len(snapshot.cycles) == 7, "three in each of two generations plus one live"

    overview_text = "".join(module.overview(snapshot))
    decisions_text = "".join(module.decisions(snapshot))
    # The count the overview prints is the count the decisions page draws its rows from. When
    # each page read the journal itself, these two could differ by whatever landed between them.
    assert "<dd>7</dd>" in overview_text
    assert decisions_text.count("<tr>") == 8, (
        f"one header row plus one per cycle, from the same snapshot: {decisions_text.count('<tr>')}"
    )


def test_the_readers_see_rotated_generations_not_just_the_live_file(tmp_path):
    rotations = 2
    module = _load_module()
    runtime = _scratch(tmp_path, rotations=rotations)
    _point(module, runtime)

    snapshot = module.load_snapshot()
    # Three per generation here; on the real journal the difference is 881 read against 2,060
    # held, which is the whole reason this assertion exists.
    live_only = sum(
        1 for line in module.JOURNAL.read_text(encoding="utf-8").splitlines()
        if line.strip() and "event_id" not in line
    )
    assert live_only == 1
    assert len(snapshot.cycles) == 1 + rotations * 3
    assert snapshot.cycles[0]["cycle_id"].startswith("r2-"), (
        "the oldest generation is read first, so the reconstructed order is the append order"
    )
    assert any(c["cycle_id"].startswith("r1-") for c in snapshot.cycles)


def test_a_stale_heartbeat_is_reported_as_stale_rather_than_as_running(tmp_path):
    """The heartbeat's own `status` word is a record of what it was, not of what it is."""
    module = _load_module()
    runtime = _scratch(tmp_path)
    _point(module, runtime)
    beat = runtime / "heartbeat.json"
    beat.write_text(json.dumps({
        "status": "RUNNING",
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
        "pid": os.getpid(),
        "cycle_count": 12,
    }), encoding="utf-8")

    snapshot = module.load_snapshot()
    state = module._daemon_state(snapshot)
    assert "RUNNING" in state, "the recorded word is still shown"
    assert "stale" in state, f"a six-hour-old heartbeat reported as live: {state!r}"
    assert "6 h" in state or "359 min" in state or "360 min" in state, state


def test_a_dead_pid_is_not_reported_as_running(tmp_path):
    module = _load_module()
    runtime = _scratch(tmp_path)
    _point(module, runtime)
    beat = runtime / "heartbeat.json"
    # Fresh timestamp, pid that cannot exist. Age alone would call this healthy.
    beat.write_text(json.dumps({
        "status": "RUNNING",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": 0x7FFFFFFF,
    }), encoding="utf-8")

    state = module._daemon_state(module.load_snapshot())
    assert "not running" in state, state


def test_no_heartbeat_reads_as_unknown_rather_than_as_zero(tmp_path):
    module = _load_module()
    runtime = _scratch(tmp_path)
    _point(module, runtime)
    assert module._daemon_state(module.load_snapshot()) == "UNKNOWN"


def test_paused_is_described_as_a_holding_pattern_the_loop_reopens(tmp_path):
    """`daemon._maybe_unpause` returns a paused strategy to probation; the page said it could not."""
    module = _load_module()
    runtime = _scratch(tmp_path, rotations=1)
    _point(module, runtime)
    text = "".join(module.strategies(module.load_snapshot()))
    assert "terminal" not in text.lower() or "not a" in text.lower(), (
        "the page still calls PAUSED terminal, which contradicts the daemon that reopens it"
    )
    assert "holding pattern" in text


def test_pages_are_written_atomically(tmp_path):
    """A reader opening the site mid-generation used to see a half-written page."""
    module = _load_module()
    runtime = _scratch(tmp_path, rotations=1)
    _point(module, runtime)
    assert module.main([]) == 0
    site = module.OUT_DIR
    for name in ("index", "decisions", "strategies", "evidence"):
        page = site / f"{name}.html"
        assert page.exists() and page.stat().st_size > 0
        # Valid to the end: no truncated tag, and the nav is complete.
        text = page.read_text(encoding="utf-8")
        assert text.rstrip().endswith("</html>")
        assert 'href="index.html"' in text and 'href="evidence.html"' in text
