from __future__ import annotations

from datetime import datetime, timezone

from autopoiesis.coerce import datetime_utc


class MarketScheduler:
    def __init__(self, *, clock_provider=None, stale_after_seconds: int = 900):
        self.clock_provider = clock_provider
        # Mirrors config.stale_after_seconds. Supplied rather than imported so the
        # scheduler stays a pure function of the clock, with no config dependency.
        self.stale_after_seconds = stale_after_seconds
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
        closed = max(1, min(self.seconds_until_next_open(default_interval), max_sleep))
        # Stay comfortably inside the heartbeat's staleness budget.
        #
        # `max_sleep` and `stale_after_seconds` are both 900. Sleeping the full cap and
        # writing the heartbeat only before sleeping means the beat lands exactly on the
        # threshold: any check running in the second after it sees a stale daemon and
        # reports the paper agent dead while it is provably alive and correctly parked
        # until the open. That is the worst shape of liveness failure - it cries wolf
        # overnight, every night, until someone stops believing it.
        #
        # Three quarters of the budget leaves room for a slow maintenance pass to
        # overrun without the next beat landing late.
        budget = max(1, int(self.stale_after_seconds * 0.75)) if self.stale_after_seconds else closed
        return max(1, min(closed, budget))

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
    timestamp = datetime_utc(value)
    if timestamp is None:
        return default
    return max(1, int((timestamp - datetime.now(tz=timezone.utc)).total_seconds()))


