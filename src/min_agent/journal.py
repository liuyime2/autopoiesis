from __future__ import annotations

from collections import deque
from pathlib import Path

from min_agent.models import CycleRecord, JournalEvent


class JsonlJournal:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, record: CycleRecord) -> None:
        self._append_json(record.model_dump_json())

    def append_event(self, event: JournalEvent) -> None:
        self._append_json(event.model_dump_json())

    def read_all(self) -> list[CycleRecord]:
        if not self.path.exists():
            return []

        records: list[CycleRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    try:
                        records.append(CycleRecord.model_validate_json(stripped))
                    except Exception:
                        continue
        return records

    def last_n(self, n: int) -> list[CycleRecord]:
        if not self.path.exists():
            return []

        tail: deque[CycleRecord] = deque(maxlen=n)
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    try:
                        tail.append(CycleRecord.model_validate_json(stripped))
                    except Exception:
                        continue
        return list(tail)

    def read_events(self, event_type: str | None = None, limit: int | None = None) -> list[JournalEvent]:
        if not self.path.exists():
            return []

        normalized_type = event_type.strip().upper() if event_type else None
        events: list[JournalEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = JournalEvent.model_validate_json(stripped)
                except Exception:
                    continue
                if normalized_type is not None and event.event_type != normalized_type:
                    continue
                events.append(event)

        if limit is not None:
            return events[-limit:]
        return events

    def _append_json(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
