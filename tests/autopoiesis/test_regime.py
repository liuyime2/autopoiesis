"""What kind of market was this, computed from the data and never from the future.

`attribution.py` reported `regime: CANNOT ATTRIBUTE` because nothing computed a
regime. This closes the measurement half of that. It deliberately does not allocate
by regime - allocation is Phase 8, and starting it before the earlier phases finish
would be entering a phase out of order.

The tests below pin the two properties that are enforced rather than assumed:

* **Point-in-time.** A label at bar *i* uses bars up to *i* only. The test that
  matters is the last one: appending future bars must not change an earlier label.
  A regime label attached to a past decision is exactly the kind of context that
  leaks, and it would leak silently.
* **A gap is a gap.** The live journal contains a 97-day outage. Computing a "recent
  volatility" straight across it would describe two different months as one market, so
  the sample is refused instead.
"""

from datetime import datetime, timedelta, timezone

from autopoiesis import regime

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _bars(prices, step_minutes=5.0):
    return [
        regime.Bar(T0 + timedelta(minutes=step_minutes * i), p)
        for i, p in enumerate(prices)
    ]


def _ramp(n, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------

def test_a_rising_series_is_trending_up():
    result = regime.classify(_bars(_ramp(60, step=0.5)))
    assert result.label == regime.TRENDING_UP
    assert result.trend_pct > 0
    assert result.contiguous is True


def test_a_falling_series_is_trending_down():
    result = regime.classify(_bars(_ramp(60, step=-0.5)))
    assert result.label == regime.TRENDING_DOWN
    assert result.trend_pct < 0


def test_a_flat_series_is_ranging():
    prices = [100.0] * 30 + [100.0 + (i % 2) * 0.01 for i in range(30)]
    result = regime.classify(_bars(prices))
    assert result.label == regime.RANGING
    assert abs(result.trend_pct) < 0.005


def test_a_noisy_series_is_unstable_even_when_it_goes_up():
    """Volatility has to be able to override direction, or a violent market that
    happens to drift upward gets filed as benign."""
    prices = [100.0 + (3.0 if i % 2 else -3.0) for i in range(60)]
    result = regime.classify(_bars(prices), volatility_threshold_pct=0.01)

    assert result.label == regime.UNSTABLE
    assert "above the" in result.note


def test_too_few_bars_say_unknown_rather_than_guessing():
    result = regime.classify(_bars([100.0, 101.0]))
    assert result.label == regime.UNKNOWN
    assert "only 2 bar" in result.note


def test_no_bars_say_unknown():
    assert regime.classify([]).label == regime.UNKNOWN


# --------------------------------------------------------------------------
# the gap rule
# --------------------------------------------------------------------------

def test_a_window_spanning_an_outage_is_refused():
    """The live journal has a 97-day gap. A "recent volatility" computed across it
    would describe two different months as one market."""
    bars = _bars(_ramp(40)) + [
        regime.Bar(bars_time + timedelta(days=97), 400.0)
        for bars_time in [T0 + timedelta(minutes=5 * 40)]
    ]
    result = regime.classify(bars, window=78)

    assert result.label == regime.UNKNOWN
    assert result.contiguous is False
    assert "describes more than one market" in result.note


def test_a_gap_within_the_sampling_interval_is_not_a_gap():
    """5-minute bars are 5 minutes apart, which is not an outage."""
    result = regime.classify(_bars(_ramp(60, step=0.5), step_minutes=5.0))
    assert result.contiguous is True
    assert result.label == regime.TRENDING_UP


def test_a_long_but_bounded_gap_is_tolerated():
    """25 minutes is 5x the sampling interval, under the 6x limit, so a couple of
    missed cycles must not throw the window away."""
    prices = _ramp(50, step=0.5)
    bars = [
        b if i != 25 else regime.Bar(b.timestamp + timedelta(minutes=20), b.price)
        for i, b in enumerate(_bars(prices))
    ]
    assert regime.classify(bars).contiguous is True


# --------------------------------------------------------------------------
# the point-in-time property
# --------------------------------------------------------------------------

def test_appending_future_bars_cannot_change_an_earlier_label():
    """The property that makes a timeline safe to join onto past decisions. If a
    label depends on anything after its own bar, the attribution is reading the
    future and every conclusion drawn from it is worthless."""
    first_half = _bars(_ramp(40, step=0.5))
    second_half = _bars(
        _ramp(40, start=120.0, step=-3.0), step_minutes=5.0
    )
    for i in (0, 10, 25, 39):
        second_half = [
            regime.Bar(
                T0 + timedelta(minutes=5 * (40 + i)), 120.0 - 3.0 * i
            )
        ] + second_half

    short = regime.regime_timeline(first_half)
    combined = regime.regime_timeline(first_half + second_half)

    for index, (ts, before) in enumerate(short):
        after_ts, after = combined[index]
        assert after_ts == ts
        assert after.label == before.label, f"label at bar {index} moved"
        assert after.volatility_pct == before.volatility_pct
        assert after.trend_pct == before.trend_pct


def test_the_timeline_has_one_entry_per_bar():
    bars = _bars(_ramp(20, step=0.5))
    timeline = regime.regime_timeline(bars)
    assert len(timeline) == len(bars)
    assert [ts for ts, _ in timeline] == [b.timestamp for b in bars]


def test_a_classification_only_ever_sees_its_own_window():
    """`classify(bars)` uses the trailing window, so a very old bar's label is
    computed from the bars after it - which is correct for "what was the market
    then", and is why the timeline calls classify on the prefix."""
    bars = _bars(_ramp(200, step=1.0))
    recent_only = regime.classify(bars)
    assert recent_only.bars <= regime.DEFAULT_WINDOW


# --------------------------------------------------------------------------
# the derived series
# --------------------------------------------------------------------------

def test_repeated_quotes_are_collapsed_so_a_flat_market_is_not_called_volatile():
    class _Snap:
        def __init__(self, ts, price):
            self.symbol, self.timestamp, self.last_price = "SPY", ts, price
    class _Rec:
        def __init__(self, ts, price):
            self.snapshot = _Snap(ts, price)

    records = [_Rec(T0 + timedelta(minutes=i), 100.0) for i in range(20)]
    bars = regime.bars_from_records(records)
    assert len(bars) == 1

    result = regime.classify(bars)
    assert result.volatility_pct is None, "one bar is not a volatility"


def test_volatility_is_reported_as_a_fraction_not_a_percentage():
    """A move and a volatility are different units and the first version of this
    module was one refactor away from printing 0.8 where it meant 80%."""
    result = regime.classify(_bars(_ramp(60, step=0.5)))
    assert result.volatility_pct is None or result.volatility_pct < 10.0
    assert result.trend_pct is not None and abs(result.trend_pct) < 1.0


def test_drawdown_is_measured_within_the_window_and_is_a_positive_number():
    # A gentle decline. The first version of this test fell 2.0 per bar, which is
    # genuinely high volatility, so the label was UNSTABLE rather than
    # TRENDING_DOWN - the classifier was right and the fixture was not.
    prices = _ramp(30, step=1.0) + _ramp(30, start=129.0, step=-0.3)
    # A 30-bar window, so the decline is what is being measured. The default
    # 78-bar window covers all 60 bars of this series and the earlier rise
    # dominates, which is the classifier being correct rather than wrong: a regime
    # describes the window it is given.
    result = regime.classify(_bars(prices), window=30)

    assert result.label == regime.TRENDING_DOWN
    assert result.drawdown_pct is not None
    assert result.drawdown_pct >= 0
    assert result.drawdown_pct < 1.0, "a fraction, not a percentage"


def test_a_steep_fall_is_unstable_rather_than_merely_trending_down():
    """Volatility has to be able to override direction. A violent decline filed as
    a calm downtrend would understate the risk of trading through it."""
    prices = _ramp(30, step=1.0) + _ramp(30, start=129.0, step=-2.0)
    result = regime.classify(_bars(prices))

    assert result.label == regime.UNSTABLE
    assert result.trend_pct < 0, "it is still going down, it is just not calm"
