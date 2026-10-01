from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from min_agent.atomicio import write_text_atomic
from min_agent.models import HeartbeatPayload


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


class HealthMonitor:
    """Reads and writes the daemon heartbeat, and judges whether it is alive.

    A heartbeat file on disk is a claim, not evidence. Reading it back and
    reporting "RUNNING" is what let a daemon that died 97 days ago pass 112
    consecutive automated reviews. This class therefore checks the pid and the
    heartbeat age, not just the status string.
    """

    def __init__(
        self,
        heartbeat_path: Path | str,
        *,
        stale_after_seconds: int = 900,
        pid_probe=_pid_alive,
    ):
        self.heartbeat_path = Path(heartbeat_path)
        self.stale_after_seconds = stale_after_seconds
        self._pid_probe = pid_probe

    def write(self, payload: HeartbeatPayload) -> None:
        write_text_atomic(self.heartbeat_path, payload.model_dump_json(indent=2) + "\n")

    def read(self) -> HeartbeatPayload | None:
        if not self.heartbeat_path.exists():
            return None
        try:
            return HeartbeatPayload.model_validate_json(self.heartbeat_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def age_seconds(self, now: datetime | None = None) -> float | None:
        payload = self.read()
        if payload is None:
            return None
        current = now or datetime.now(timezone.utc)
        timestamp = payload.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return (current - timestamp).total_seconds()

    def is_stale(self, now: datetime | None = None) -> bool:
        age = self.age_seconds(now)
        if age is None:
            return True
        return age > self.stale_after_seconds

    def is_live(self, now: datetime | None = None) -> bool:
        payload = self.read()
        if payload is None:
            return False
        if payload.status not in {"RUNNING", "STARTING", "BACKING_OFF"}:
            return False
        if self.is_stale(now):
            return False
        if payload.pid <= 0:
            return False
        return bool(self._pid_probe(payload.pid))

    def liveness(self, now: datetime | None = None) -> dict:
        payload = self.read()
        age = self.age_seconds(now)
        pid_alive = bool(self._pid_probe(payload.pid)) if payload is not None and payload.pid > 0 else False
        return {
            "heartbeat_present": payload is not None,
            "heartbeat_path": str(self.heartbeat_path),
            "pid": payload.pid if payload is not None else None,
            "pid_alive": pid_alive,
            "status": payload.status if payload is not None else "missing",
            "age_seconds": None if age is None else round(age, 3),
            "stale_after_seconds": self.stale_after_seconds,
            "stale": self.is_stale(now),
            "live": self.is_live(now),
        }

    def status_json(self, now: datetime | None = None) -> str:
        return json.dumps(self.liveness(now), indent=2, sort_keys=True)
