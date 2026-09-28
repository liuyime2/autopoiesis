from datetime import datetime, timezone

from min_agent.health import HealthMonitor
from min_agent.models import HeartbeatPayload


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
