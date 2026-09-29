from __future__ import annotations

from datetime import datetime, timezone


class MarketScheduler:
    def __init__(self, *, clock_provider=None):
        self.clock_provider = clock_provider
        self.last_error: str | None = None

    def market_is_open(self) -> bool:
        clock = self._clock()
        if clock is None:
            return False
        return bool(getattr(clock, "is_open", False))

    def should_trade_now(self) -> bool:
        return self.market_is_open()

    def seconds_until_next_open(self, default: int) -> int:
        clock = self._clock()
        return _seconds_until(getattr(clock, "next_open", None) if clock is not None else None, default)

    def seconds_until_next_close(self, default: int) -> int:
        clock = self._clock()
        return _seconds_until(getattr(clock, "next_close", None) if clock is not None else None, default)

    def sleep_seconds(self, *, default_interval: int, max_sleep: int) -> int:
        """How long to wait before the next cycle.

        While the market is **open** this is the configured cycle interval. It used
        to sleep until the next *close* instead, capped by `max_sleep` - and with the
        shipped configuration that meant 900 seconds against a configured 300. The
        daemon was therefore taking one cycle every 15 minutes during a session
        instead of every 5, with `daemon_interval_seconds` inert exactly when it
        mattered. Nothing reported it: the loop looked healthy, the heartbeat stayed
        fresh, and the only symptom was that the market opened, produced a single
        cycle, and then went quiet.

        That is three times fewer decisions per session, and it slows the very
        measurement the system is waiting on - 68 unscoreable decisions whose 24-hour
        horizon needs market hours to resolve.

        Sleeping until the next open is still correct when the market is **closed**:
        there is no point waking every 5 minutes overnight, and `max_sleep` caps it so
        maintenance still runs.
        """
        if self.market_is_open():
            return max(1, min(default_interval, max_sleep))
        return max(1, min(self.seconds_until_next_open(default_interval), max_sleep))

    def _clock(self):
        if self.clock_provider is None:
            return None
        try:
            clock = self.clock_provider()
        except Exception as exc:
            # Fail closed: a clock we cannot read means the market is not known
            # to be open, so no order is placed. Propagating this instead is
            # what turned a single Alpaca ConnectTimeout into a daemon that
            # died and was restarted every 30 seconds by run_forever.sh.
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        self.last_error = None
        return clock


def _seconds_until(value, default: int) -> int:
    timestamp = _datetime(value)
    if timestamp is None:
        return default
    return max(1, int((timestamp - datetime.now(tz=timezone.utc)).total_seconds()))


def _datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
