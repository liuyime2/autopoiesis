from datetime import datetime, timedelta, timezone

from min_agent.scheduler import MarketScheduler


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


def test_scheduler_caps_sleep_at_max_sleep():
    next_close = datetime.now(tz=timezone.utc) + timedelta(seconds=500)
    scheduler = MarketScheduler(clock_provider=lambda: Clock(True, next_close=next_close))

    assert scheduler.sleep_seconds(default_interval=30, max_sleep=60) == 60


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
