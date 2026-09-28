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
        target = self.seconds_until_next_close(default_interval) if self.market_is_open() else self.seconds_until_next_open(default_interval)
        return max(1, min(target, max_sleep))

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
