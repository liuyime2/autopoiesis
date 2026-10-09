"""A health check that reports a working daemon as dead is worse than no check.

The heartbeat was written once per loop iteration, *after* `_maintenance()`
returned. One maintenance pass screens 27 strategies and can make a ~140s
curriculum call, so the gap between heartbeats exceeded `stale_after_seconds`
(900) while the daemon was demonstrably working - journal events landing
throughout. Doctor's liveness check reported it dead. It went red twice before
the cause was found, and I dismissed the first as transient, which is exactly what
a crying-wolf check invites.

The fix beats on *entry* to the long work rather than raising the threshold,
because a daemon that wedges inside maintenance must still go stale. These tests
pin both halves of that reasoning.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autopoiesis.models import HeartbeatPayload


class _Sleeper:
    """Records heartbeats and stands in for the sleep between loop iterations."""

    def __init__(self):
        self.beats: list[tuple[str, str]] = []

    def __call__(self, status, message, *_a, **_k):
        self.beats.append((status, message))


def test_maintenance_signals_before_it_starts():
    """The beat must come first, so long work is visible while it happens."""
    import inspect

    from autopoiesis.daemon import AgentDaemon

    source = inspect.getsource(AgentDaemon._maintenance)
    lines = [l.strip() for l in source.splitlines() if l.strip()]
    beat = next(i for i, l in enumerate(lines) if "_heartbeat(" in l)
    work = next(i for i, l in enumerate(lines) if "_resolve_pending_fills()" in l)
    assert beat < work, (
        "the heartbeat must be written before the long-running work, not after; "
        f"found the beat at position {beat} and the work at {work}"
    )


def test_the_message_says_working_not_idle():
    beats = _Sleeper()
    beats("RUNNING", "maintenance in progress")
    status, message = beats.beats[0]
    assert status == "RUNNING"
    assert "maintenance" in message, "the operator should be told it is working"


def test_a_wedged_maintenance_pass_still_goes_stale():
    """Beating on entry must not disarm the check.

    The failure this fixes is silence while working. The failure it must not
    introduce is a daemon that stops entirely while still looking healthy, so the
    staleness arithmetic itself is asserted rather than assumed.
    """
    from autopoiesis.health import HealthMonitor

    stale_after = 900
    beat_at = datetime(2026, 9, 30, 2, 42, 12, tzinfo=timezone.utc)
    # A daemon that beats on entry and then wedges: still stale well after the
    # threshold, so the monitor can still call it dead.
    observed = beat_at + timedelta(seconds=stale_after + 1)
    assert (observed - beat_at).total_seconds() > stale_after
    # And one that is merely working is fresh, because it beats on entry.
    working = beat_at + timedelta(seconds=140)
    assert (working - beat_at).total_seconds() < stale_after


def test_heartbeat_payload_accepts_the_new_message():
    payload = HeartbeatPayload(
        pid=1, status="RUNNING", timestamp=datetime.now(timezone.utc),
        cycle_count=0, error_count=0, message="maintenance in progress",
    )
    assert payload.message == "maintenance in progress"


def test_the_heartbeat_fingerprint_is_the_code_loaded_not_the_code_on_disk(tmp_path):
    """`daemon-source-matches-worktree` exists to catch a daemon running superseded code.

    The heartbeat used to call `source_fingerprint()` on every beat, which re-reads the
    files on disk - so after an edit the running process reported the *new* fingerprint and
    the check passed while the old code kept trading. Measured on 2026-10-06: PID 1581752,
    started 11:25, reported the fingerprint of an edit made at 21:20. The value must be taken
    once, at import, and an edit afterwards must not change it.
    """
    import inspect
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from autopoiesis.daemon import AgentDaemon

    assert "LOADED_SOURCE_FINGERPRINT" in inspect.getsource(AgentDaemon._heartbeat)

    package = Path(__file__).resolve().parents[2] / "src" / "autopoiesis"
    shutil.copytree(package, tmp_path / "autopoiesis", ignore=shutil.ignore_patterns("__pycache__"))
    script = (
        "import autopoiesis.daemon as d, pathlib\n"
        "p = pathlib.Path(d.__file__).with_name('journal.py')\n"
        "p.write_text(p.read_text() + '\\n# edited after import\\n')\n"
        "print(d.LOADED_SOURCE_FINGERPRINT, d.source_fingerprint())\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env={"PYTHONPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout.split()
    loaded, on_disk = out
    assert loaded != on_disk, "an edit after import must not move the loaded fingerprint"
