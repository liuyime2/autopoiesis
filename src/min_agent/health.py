from __future__ import annotations

import json
from pathlib import Path

from min_agent.models import HeartbeatPayload


class HealthMonitor:
    def __init__(self, heartbeat_path: Path | str):
        self.heartbeat_path = Path(heartbeat_path)

    def write(self, payload: HeartbeatPayload) -> None:
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")

    def read(self) -> HeartbeatPayload | None:
        if not self.heartbeat_path.exists():
            return None
        return HeartbeatPayload.model_validate_json(self.heartbeat_path.read_text(encoding="utf-8"))

    def status_json(self) -> str:
        payload = self.read()
        if payload is None:
            return json.dumps({"status": "missing", "heartbeat_path": str(self.heartbeat_path)}, sort_keys=True)
        return payload.model_dump_json(indent=2)
