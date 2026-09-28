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
