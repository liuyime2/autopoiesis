"""A clock that advances only to real bar timestamps (no fast-forward)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone


class SimulationClock:
    def __init__(self, start: datetime):
        self._now = start if start.tzinfo else start.replace(tzinfo=timezone.utc)

    @property
    def now(self) -> datetime:
        return self._now

    def advance_to(self, ts: datetime) -> None:
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if ts < self._now:
            raise ValueError("clock cannot move backwards")
        self._now = ts

    def advance_by_bars(self, timestamps: Iterable[datetime]) -> None:
        for ts in timestamps:
            self.advance_to(ts)
