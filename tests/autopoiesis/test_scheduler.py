from datetime import datetime, timedelta, timezone

from autopoiesis.scheduler import MarketScheduler


class Clock:
    def __init__(self, is_open, *, next_open=None, next_close=None):
        self.is_open = is_open
        self.next_open = next_open
        self.next_close = next_close


def test_scheduler_closed_without_clock_provider():
    scheduler = MarketScheduler()
    assert scheduler.should_trade_now() is False


def test_scheduler_uses_clock_provider():
    scheduler = MarketScheduler(clock_provider=lambda: Clock(True))
    assert scheduler.should_trade_now() is True


def test_scheduler_reports_closed_clock():
    scheduler = MarketScheduler(clock_provider=lambda: Clock(False))
    assert scheduler.should_trade_now() is False


def test_scheduler_uses_next_open_for_closed_sleep():
    next_open = datetime.now(tz=timezone.utc) + timedelta(seconds=120)
    scheduler = MarketScheduler(clock_provider=lambda: Clock(False, next_open=next_open))

    seconds = scheduler.sleep_seconds(default_interval=30, max_sleep=300)

    assert 1 <= seconds <= 120


def test_the_cycle_interval_governs_while_the_market_is_open():
    """The configured interval must actually be the interval.

    This test previously asserted the opposite: with the market open and the close
    500s away it expected a 60s sleep against a configured 30s. That is the defect -
    the loop slept until the close, capped by `max_sleep`, so the shipped
    configuration of 300s against a 900s cap produced one cycle every 15 minutes
    during a session instead of every 5. `daemon_interval_seconds` was inert
    exactly when it mattered, and nothing reported it: the heartbeat stayed fresh and
    the loop looked healthy.
    """
    next_close = datetime.now(tz=timezone.utc) + timedelta(seconds=500)
    scheduler = MarketScheduler(clock_provider=lambda: Clock(True, next_close=next_close))

    assert scheduler.sleep_seconds(default_interval=30, max_sleep=60) == 30


def test_the_shipped_configuration_cycles_at_the_configured_rate_while_open():
    """The numbers that were actually running: 300s interval, 900s cap."""
    next_close = datetime.now(tz=timezone.utc) + timedelta(hours=6)
    scheduler = MarketScheduler(clock_provider=lambda: Clock(True, next_close=next_close))

    assert scheduler.sleep_seconds(default_interval=300, max_sleep=900) == 300


def test_max_sleep_still_caps_the_interval():
    """A cap below the interval is honoured, so a test or a tight run can shorten it."""
    next_close = datetime.now(tz=timezone.utc) + timedelta(seconds=500)
    scheduler = MarketScheduler(clock_provider=lambda: Clock(True, next_close=next_close))

    assert scheduler.sleep_seconds(default_interval=300, max_sleep=60) == 60


def test_a_closed_market_sleeps_until_the_next_open_but_still_wakes_for_maintenance():
    """Sleeping overnight at the cycle rate would wake 288 times a session for
    nothing. Sleeping until the open, capped, is the right behaviour when closed.

    The cap is deliberately below `max_sleep` rather than equal to it. `max_sleep` and
    `stale_after_seconds` are both 900, and the daemon beats once before sleeping, so
    sleeping the full cap puts the next heartbeat exactly on the staleness threshold -
    any liveness check in the second after it reports a provably live daemon as dead,
    every night. Three quarters of the budget leaves room for maintenance to overrun.
    """
    next_open = datetime.now(tz=timezone.utc) + timedelta(hours=8)
    scheduler = MarketScheduler(
        clock_provider=lambda: Clock(False, next_open=next_open),
        stale_after_seconds=900,
    )

    slept = scheduler.sleep_seconds(default_interval=300, max_sleep=900)
    assert slept == 675, "closed-market sleep must stay inside the heartbeat budget"
    assert slept < 900, "and must not sit on the staleness threshold"


def test_a_short_sleep_until_open_is_not_shortened_by_the_budget():
    """The budget only caps the sleep. An open 200s away must still be slept through."""
    next_open = datetime.now(tz=timezone.utc) + timedelta(seconds=200)
    scheduler = MarketScheduler(
        clock_provider=lambda: Clock(False, next_open=next_open),
        stale_after_seconds=900,
    )

    # int() truncation plus the microseconds elapsed while computing `next_open` means
    # the remainder can be a second under 200. What matters is that the budget did not
    # shorten it: 675 would mean the cap was applied where it should not be.
    assert 150 <= scheduler.sleep_seconds(default_interval=300, max_sleep=900) <= 200


def test_an_open_market_ignores_the_heartbeat_budget():
    """While open, the cycle interval governs. It is already well inside the budget."""
    scheduler = MarketScheduler(
        clock_provider=lambda: Clock(True),
        stale_after_seconds=900,
    )

    assert scheduler.sleep_seconds(default_interval=300, max_sleep=900) == 300


def test_scheduler_fails_closed_when_the_clock_raises():
    """A ConnectTimeout here used to kill the daemon; run_forever.sh then
    restarted it every 30 seconds forever."""
    def boom():
        raise ConnectionError("ConnectTimeout to paper-api.alpaca.markets")

    scheduler = MarketScheduler(clock_provider=boom)

    assert scheduler.should_trade_now() is False
    assert "ConnectTimeout" in scheduler.last_error


def test_scheduler_fails_closed_for_every_clock_read():
    def boom():
        raise TimeoutError("read timed out")

    scheduler = MarketScheduler(clock_provider=boom)

    assert scheduler.market_is_open() is False
    assert scheduler.seconds_until_next_open(30) == 30
    assert scheduler.seconds_until_next_close(30) == 30
    assert scheduler.sleep_seconds(default_interval=30, max_sleep=300) >= 1


def test_scheduler_clears_last_error_on_a_successful_read():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("transient")
        return Clock(True)

    scheduler = MarketScheduler(clock_provider=flaky)

    assert scheduler.should_trade_now() is False
    assert scheduler.should_trade_now() is True
    assert scheduler.last_error is None
