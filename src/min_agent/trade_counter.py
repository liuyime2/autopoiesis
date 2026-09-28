from __future__ import annotations

from datetime import datetime, timezone

from min_agent.journal import JsonlJournal


class TradeCounter:
    def __init__(self, *, journal: JsonlJournal):
        self.journal = journal

    def trades_today(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(tz=timezone.utc)
        today = current.astimezone(timezone.utc).date()
        count = 0
        for record in self.journal.read_all():
            timestamp = record.snapshot.timestamp
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp.astimezone(timezone.utc).date() != today:
                continue
            if record.decision.action == "HOLD":
                continue
            if record.execution.status == "SUBMITTED":
                count += 1
        return count
