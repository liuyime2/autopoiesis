"""DataEvent contract and point-in-time join.

Step 2.1/2.2 of docs/EXECUTION_PLAN_2026-11-05.md.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

LIMITED_UNIVERSE = "LIMITED_UNIVERSE"


def _ensure_tz(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class DataEvent:
    source: str
    symbol: str
    event_time: datetime
    first_public_at: datetime | None = None
    available_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    revision: int = 0
    payload_hash: str = ""
    adjustment_policy: str = ""
    quality_status: str = "OK"
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Frozen dataclass: use object.__setattr__.
        object.__setattr__(self, "event_time", _ensure_tz(self.event_time))
        object.__setattr__(self, "first_public_at", _ensure_tz(self.first_public_at))
        object.__setattr__(self, "available_at", _ensure_tz(self.available_at))
        object.__setattr__(self, "ingested_at", _ensure_tz(self.ingested_at))
        if not self.payload_hash and self.payload:
            object.__setattr__(
                self,
                "payload_hash",
                hashlib.sha256(
                    json.dumps(self.payload, sort_keys=True, default=str).encode()
                ).hexdigest()[:16],
            )

    def is_available_at(self, cutoff: datetime) -> bool:
        c = _ensure_tz(cutoff)
        assert c is not None
        return self.available_at <= c


def point_in_time_filter(
    events: Iterable[DataEvent], cutoff: datetime
) -> list[DataEvent]:
    """Drop events not yet public at `cutoff`."""
    c = _ensure_tz(cutoff)
    assert c is not None
    return [e for e in events if e.available_at <= c]


def point_in_time_join(
    events: Iterable[DataEvent], cutoff: datetime, *, symbol: str | None = None
) -> list[DataEvent]:
    """Return events available at `cutoff`, optionally for one symbol.

    Strict: an event whose `available_at` is after `cutoff` is never returned,
    even if its `event_time` is before. This is the leakage guard.
    """
    c = _ensure_tz(cutoff)
    assert c is not None
    out = []
    for e in events:
        if e.available_at > c:
            continue
        if symbol is not None and e.symbol.upper() != symbol.upper():
            continue
        out.append(e)
    return out


def assert_no_leakage(events: Iterable[DataEvent], cutoff: datetime) -> None:
    """Raise if any event in `events` was not available at `cutoff`."""
    c = _ensure_tz(cutoff)
    assert c is not None
    bad = [e for e in events if e.available_at > c]
    if bad:
        raise ValueError(
            f"point-in-time leakage: {len(bad)} event(s) available after {c.isoformat()}"
        )
