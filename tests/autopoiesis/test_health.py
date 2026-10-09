import os
from datetime import datetime, timedelta, timezone

from autopoiesis.health import HealthMonitor
from autopoiesis.models import HeartbeatPayload


def test_health_monitor_returns_none_when_missing(tmp_path):
    monitor = HealthMonitor(tmp_path / "heartbeat.json")
    assert monitor.read() is None


def test_health_monitor_writes_and_reads_heartbeat(tmp_path):
    monitor = HealthMonitor(tmp_path / "heartbeat.json")
    payload = HeartbeatPayload(
        pid=123,
        status="RUNNING",
        timestamp=datetime.now(tz=timezone.utc),
        cycle_count=2,
        error_count=0,
        message="ok",
    )

    monitor.write(payload)

    loaded = monitor.read()
    assert loaded is not None
    assert loaded.status == "RUNNING"
    assert loaded.cycle_count == 2


def test_health_monitor_status_json_for_missing(tmp_path):
    monitor = HealthMonitor(tmp_path / "missing.json")
    status = monitor.status_json()
    assert "missing" in status


def _payload(pid, status="RUNNING", *, age_seconds=0):
    return HeartbeatPayload(
        pid=pid,
        status=status,
        timestamp=datetime.now(tz=timezone.utc) - timedelta(seconds=age_seconds),
        cycle_count=39,
        error_count=0,
        message="ok",
    )


def test_heartbeat_from_a_dead_pid_is_not_live(tmp_path):
    """The 97-day-stale case: status says RUNNING, the process is gone."""
    monitor = HealthMonitor(tmp_path / "heartbeat.json", pid_probe=lambda pid: False)
    monitor.write(_payload(pid=2590767))

    assert monitor.read().status == "RUNNING"
    assert monitor.is_live() is False


def test_heartbeat_older_than_the_stale_window_is_not_live(tmp_path):
    monitor = HealthMonitor(tmp_path / "heartbeat.json", stale_after_seconds=900, pid_probe=lambda pid: True)
    monitor.write(_payload(pid=os.getpid(), age_seconds=97 * 24 * 3600))

    assert monitor.is_stale() is True
    assert monitor.is_live() is False


def test_fresh_heartbeat_with_a_live_pid_is_live(tmp_path):
    monitor = HealthMonitor(tmp_path / "heartbeat.json", pid_probe=lambda pid: pid == os.getpid())
    monitor.write(_payload(pid=os.getpid()))

    assert monitor.is_live() is True


def test_liveness_reports_both_failure_modes(tmp_path):
    monitor = HealthMonitor(tmp_path / "heartbeat.json", stale_after_seconds=900, pid_probe=lambda pid: False)
    monitor.write(_payload(pid=424242, age_seconds=5000))

    liveness = monitor.liveness()

    assert liveness["heartbeat_present"] is True
    assert liveness["pid_alive"] is False
    assert liveness["stale"] is True
    assert liveness["live"] is False


def test_non_running_status_is_not_live(tmp_path):
    monitor = HealthMonitor(tmp_path / "heartbeat.json", pid_probe=lambda pid: True)
    monitor.write(_payload(pid=os.getpid(), status="STOPPED"))

    assert monitor.is_live() is False


def test_corrupt_heartbeat_reads_as_absent_not_live(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text("{not json", encoding="utf-8")

    monitor = HealthMonitor(path, pid_probe=lambda pid: True)

    assert monitor.read() is None
    assert monitor.is_live() is False
    assert monitor.is_stale() is True


def test_default_pid_probe_detects_this_process():
    monitor = HealthMonitor("/nonexistent/heartbeat.json")

    assert monitor._pid_probe(os.getpid()) is True
    assert monitor._pid_probe(0x7FFFFFF0) is False
