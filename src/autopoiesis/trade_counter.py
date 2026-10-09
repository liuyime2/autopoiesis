from __future__ import annotations

from datetime import datetime, timezone

from autopoiesis.journal import JsonlJournal


class TradeCounter:
    def __init__(self, *, journal: JsonlJournal):
        self.journal = journal

    def trades_today(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(tz=timezone.utc)
        today = current.astimezone(timezone.utc).date()
        # Uses the journal's targeted scan rather than read_all(). This runs on
        # every trading cycle, and read_all() cost scaled with the whole file:
        # 0.73s at 16.9MB and growing linearly with no rotation to bound it.
        return self.journal.count_submitted_on(today)
