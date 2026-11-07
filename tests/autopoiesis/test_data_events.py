"""The G2 contract: a replay must not see an event before it was available."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.research.data import (
    LIMITED_UNIVERSE,
    DataEvent,
    point_in_time_filter,
    point_in_time_join,
    universe_as_of,
)
from autopoiesis.research.data.events import assert_no_leakage

UTC = timezone.utc


def _event(
    *,
    symbol: str = "SPY",
    event_time: datetime | None = None,
    available_at: datetime | None = None,
    revision: int = 0,
    payload: dict | None = None,
) -> DataEvent:
    return DataEvent(
        source="test",
        symbol=symbol,
        event_time=event_time or datetime(2024, 1, 10, 21, tzinfo=UTC),
        first_public_at=datetime(2024, 1, 10, 21, tzinfo=UTC),
        available_at=available_at or datetime(2024, 1, 10, 21, tzinfo=UTC),
        ingested_at=datetime(2024, 1, 11, 9, tzinfo=UTC),
        revision=revision,
        payload=payload or {"value": 1},
    )


def test_payload_hash_is_deterministic_and_changes_with_payload() -> None:
    first = _event(payload={"value": 1, "extra": "a"})
    same = _event(payload={"extra": "a", "value": 1})
    other = _event(payload={"value": 2})
    assert first.payload_hash
    assert first.payload_hash == same.payload_hash
    assert first.payload_hash != other.payload_hash


def test_naive_datetimes_are_normalised_to_utc() -> None:
    event = DataEvent(
        source="test",
        symbol="SPY",
        event_time=datetime(2024, 1, 10, 21),
        available_at=datetime(2024, 1, 10, 21),
    )
    assert event.event_time.tzinfo is not None
    assert event.available_at.tzinfo is not None
    assert event.available_at.utcoffset().total_seconds() == 0


def test_future_earnings_are_not_visible_even_if_event_time_is_past() -> None:
    # Event time is before the cutoff, but the filing became available after it.
    earnings = _event(
        event_time=datetime(2024, 1, 5, 21, tzinfo=UTC),
        available_at=datetime(2024, 1, 12, 21, tzinfo=UTC),
    )
    visible = _event(available_at=datetime(2024, 1, 9, 21, tzinfo=UTC))
    cutoff = datetime(2024, 1, 10, 21, tzinfo=UTC)

    joined = point_in_time_join([earnings, visible], cutoff)
    assert joined == [visible]
    assert point_in_time_filter([earnings, visible], cutoff) == [visible]


def test_a_future_revision_does_not_replace_the_visible_one() -> None:
    original = _event(revision=0, payload={"value": 1})
    revision = _event(
        revision=1,
        available_at=datetime(2024, 1, 15, 21, tzinfo=UTC),
        payload={"value": 2},
    )
    early = datetime(2024, 1, 10, 21, tzinfo=UTC)
    late = datetime(2024, 1, 16, 21, tzinfo=UTC)

    assert point_in_time_join([original, revision], early) == [original]
    assert point_in_time_join([original, revision], late) == [original, revision]


def test_symbol_filter_is_case_insensitive() -> None:
    spy = _event(symbol="SPY")
    qqq = _event(symbol="QQQ")
    cutoff = datetime(2024, 1, 11, 21, tzinfo=UTC)
    assert point_in_time_join([spy, qqq], cutoff, symbol="spy") == [spy]


def test_assert_no_leakage_rejects_a_future_event() -> None:
    cutoff = datetime(2024, 1, 10, 21, tzinfo=UTC)
    assert_no_leakage([_event()], cutoff)
    with pytest.raises(ValueError, match="point-in-time leakage"):
        assert_no_leakage(
            [_event(available_at=datetime(2024, 1, 12, 21, tzinfo=UTC))], cutoff
        )


def test_universe_as_of_marks_unknown_and_unlisted_symbols_as_limited() -> None:
    available, limited = universe_as_of("2000-01-01", symbols=["SPY", "GLD", "NEW"])
    assert available == ["SPY"]
    assert limited == ["GLD", "NEW"]
    assert LIMITED_UNIVERSE == "LIMITED_UNIVERSE"


def test_universe_as_of_includes_an_etf_after_inception() -> None:
    available, limited = universe_as_of("2020-01-01", symbols=["SPY", "GLD", "QQQ"])
    assert available == ["SPY", "GLD", "QQQ"]
    assert limited == []
