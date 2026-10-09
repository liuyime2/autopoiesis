"""The instrument the agent is being asked about: named by the broker, measured by its own tape.

Everything here is against a fake client, so it runs in CI with no credentials. The live-broker
check of the same code is recorded in `docs/superpowers/plans/2026-10-08-name-the-instrument-and-let-the-bar-be-a-family.md`.
"""

from __future__ import annotations

import math
from itertools import pairwise

from autopoiesis.data_gateway import (
    BEHAVIOUR_BARS,
    MIN_BEHAVIOUR_BARS,
    AlpacaDataGateway,
    _instrument_flags,
    _pearson,
    _vol_pct,
)
from autopoiesis.models import InstrumentProfile, NewsItem


class _Frame:
    def __init__(self, frame):
        self.df = frame


class _FakeBars:
    def __init__(self, series: dict[str, list[float]]):
        self.series = series

    def get_bars(self, symbol, timeframe, start, end, adjustment="raw"):
        closes = self.series.get(symbol)
        if not closes:
            raise AssertionError(f"no series for {symbol}")
        import pandas as pd

        index = pd.date_range("2026-01-01", periods=len(closes), freq="B")
        frame = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1e6] * len(closes)},
            index=index,
        )
        return _Frame(frame)


class _FakeAsset:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeClient:
    """Just enough client for `instrument_profile` and `recent_news`."""

    def __init__(self, asset=None, bars=None, news=None):
        self._asset = asset
        self._bars = _FakeBars(bars or {})
        self._news = news or {}

    def get_asset(self, symbol):
        return self._asset

    def get_bars(self, symbol, timeframe, start, end, adjustment="raw"):
        return self._bars.get_bars(symbol, timeframe, start, end, adjustment)

    def data_get(self, path, params=None, api_version="v1beta1"):
        if path == "/news":
            return {"news": self._news.get(str(params["symbols"]), [])}
        raise AssertionError(f"unexpected data_get {path}")


def _series(n=BEHAVIOUR_BARS + 20, drift=0.0004, vol=0.01, seed=7):
    import random

    rng = random.Random(seed)
    out, px = [], 100.0
    for _ in range(n):
        px *= 1 + drift + rng.gauss(0, vol)
        out.append(px)
    return out


def _gateway(asset=None, bars=None, news=None) -> AlpacaDataGateway:
    bars = bars or {"SPY": _series(), "QQQ": _series(drift=0.0005, seed=11), "TLT": _series(drift=0.0002, seed=13), "GLD": _series(drift=0.0003, seed=17)}
    return AlpacaDataGateway(client=_FakeClient(asset or _FakeAsset(symbol="SPY", name="Test Trust", **{"class": "us_equity"}, exchange="ARCA", tradable=True, shortable=True), bars, news))


def test_the_brokers_own_name_is_the_profile_and_nothing_is_invented():
    gw = _gateway(_FakeAsset(symbol="SOXS", name="Direxion Daily Semiconductor Bear 3X ETF", **{"class": "us_equity"}, exchange="ARCA", tradable=True, shortable=False))
    profile = gw.instrument_profile("SOXS")
    assert profile is not None
    assert profile.name == "Direxion Daily Semiconductor Bear 3X ETF"
    assert profile.asset_class == "us_equity"
    assert profile.exchange == "ARCA"
    assert profile.shortable is False
    # Fields the asset record did not carry must stay absent, not be defaulted into a claim.
    assert profile.easy_to_borrow is None
    assert profile.fractionable is None


def test_a_symbol_the_broker_cannot_name_is_refused_and_says_why():
    gw = _gateway(_FakeAsset(symbol="ZZZZ"))
    assert gw.instrument_profile("ZZZZ") is None
    assert gw.last_profile_error


def test_behaviour_is_measured_from_the_instruments_own_bars_and_matches_a_direct_computation():
    """The measured numbers must equal the same computation done independently, not approximately."""
    series = _series(n=BEHAVIOUR_BARS + 20, seed=23)
    spy = _series(n=BEHAVIOUR_BARS + 20, seed=29)
    gw = _gateway(bars={"INTC": series, "SPY": spy, "QQQ": _series(seed=31), "TLT": _series(seed=37), "GLD": _series(seed=41)})
    profile = gw.instrument_profile("INTC")
    assert profile is not None
    closes = series[-(BEHAVIOUR_BARS + 1) :]
    rets = [b / a - 1.0 for a, b in pairwise(closes)]
    assert abs(profile.annualized_vol_pct - _vol_pct(rets)) < 0.5
    spy_rets = [b / a - 1.0 for a, b in pairwise(spy[-(BEHAVIOUR_BARS + 1) :])]
    assert abs(profile.corr_spy - _pearson(rets, spy_rets)) < 0.02
    # A fresh profile for SPY itself is the reference, so it measures exactly 1 against itself.
    own = gw.instrument_profile("SPY")
    assert own is not None and own.corr_spy == 1.0 and own.vol_ratio_spy == 1.0


def test_a_flat_series_flags_nothing_and_reports_no_volatility():
    gw = _gateway(bars={"SPY": [100.0] * 90})
    profile = gw.instrument_profile("SPY")
    assert profile is not None
    assert profile.annualized_vol_pct == 0.0
    assert profile.flags == ()
    assert profile.corr_spy is None


def test_too_short_a_history_suppresses_the_flags_rather_than_guessing():
    """Below `MIN_BEHAVIOUR_BARS` there is nothing to measure, so the flags stay empty rather than
    being computed from a handful of bars of a new listing."""
    gw = _gateway(bars={"NEW": _series(n=MIN_BEHAVIOUR_BARS - 20, seed=53), "QQQ": _series(seed=59), "TLT": _series(seed=61), "GLD": _series(seed=67)})
    profile = gw.instrument_profile("NEW")
    assert profile is not None
    assert profile.bars_used < MIN_BEHAVIOUR_BARS
    assert profile.annualized_vol_pct is None
    assert profile.flags == ()


def test_correlation_to_the_index_alone_does_not_detect_leverage_so_the_volatility_ratio_does():
    """A 3x fund on a non-index underlying measures near-zero correlation with the index while moving
    several times as far. SOXS on this broker: corr -0.04, beta -4.7, vol ratio 118x. A correlation
    test alone calls that nothing, so the flag has to come from the ratio."""
    measured = {"bars_used": 121, "annualized_vol_pct": 1422.7, "vol_ratio_spy": 118.0, "corr_spy": -0.04, "beta_spy": -5.03}
    flags = _instrument_flags(measured)
    assert any("118.0x" in f for f in flags)
    assert any("does_not_track_the_index" in f for f in flags)


def test_an_inverse_fund_is_flagged_from_its_correlation_and_a_bond_etf_from_its_reference():
    inverse = _instrument_flags({"bars_used": 121, "corr_spy": -0.82, "vol_ratio_spy": 3.0})
    assert any("moves_against_equities" in f for f in inverse)
    bonds = _instrument_flags({"bars_used": 121, "corr_spy": 0.45, "corr_tlt": 1.0, "vol_ratio_spy": 0.8})
    assert any("tracks_bonds_more_than_equities" in f for f in bonds)


def test_every_flag_carries_the_number_it_was_derived_from():
    flags = _instrument_flags({"bars_used": 121, "corr_spy": 0.2, "vol_ratio_spy": 4.4, "annualized_vol_pct": 85.0})
    assert flags
    for flag in flags:
        assert any(c.isdigit() for c in flag), flag


def test_news_is_returned_shortest_first_and_truncated_to_the_stated_budget():
    raw = {
        "news": [
            {"headline": "a" * 500, "source": "benzinga", "created_at": "2026-10-08T09:00:00Z", "summary": "s" * 900, "symbols": ["SOXS"]},
            {"headline": "second", "symbols": ["SOXS"]},
        ]
    }
    gw = _gateway(news={"SOXS": raw["news"]})
    items = gw.recent_news("SOXS", limit=5)
    assert len(items) == 2
    assert items[0].headline == "a" * 200
    assert items[0].summary == "s" * 200
    assert items[0].created_at is not None
    assert items[1].headline == "second"
    assert items[1].source == "alpaca_news"


def test_a_broken_news_call_returns_nothing_and_records_why_rather_than_raising():
    class _Exploding(_FakeClient):
        def data_get(self, path, params=None, api_version="v1beta1"):
            raise RuntimeError("news endpoint down")

    gw = AlpacaDataGateway(client=_Exploding())
    assert gw.recent_news("SPY") == ()
    assert gw.last_news_error


def test_the_profile_is_cached_per_day_and_the_bars_are_fetched_once_per_symbol():
    calls: list[str] = []

    class _Counting(_FakeClient):
        def get_bars(self, symbol, timeframe, start, end, adjustment="raw"):
            calls.append(f"{symbol}:{adjustment}")
            return super().get_bars(symbol, timeframe, start, end, adjustment)

    gw = AlpacaDataGateway(client=_Counting(_FakeAsset(symbol="SPY", name="t", **{"class": "us_equity"}), {"SPY": _series(n=BEHAVIOUR_BARS + 20), "QQQ": _series(seed=3), "TLT": _series(seed=5), "GLD": _series(seed=9)}))
    gw.instrument_profile("SPY")
    gw.instrument_profile("SPY")
    assert calls.count("SPY:raw") == 1


def test_a_failed_bar_fetch_leaves_the_profile_available_without_its_behaviour():
    """An instrument the broker names but has no history for is still an instrument."""

    class _NoHistory(_FakeClient):
        def get_bars(self, symbol, timeframe, start, end, adjustment="raw"):
            raise AssertionError("no bars")

    gw = AlpacaDataGateway(client=_NoHistory(_FakeAsset(symbol="NEW", name="New Listing Inc", **{"class": "us_equity"})))
    profile = gw.instrument_profile("NEW")
    assert profile is not None
    assert profile.name == "New Listing Inc"
    assert profile.bars_used == 0
    assert profile.flags == ()


def test_the_model_serialises_with_the_unmeasured_fields_absent():
    profile = InstrumentProfile(symbol="SPY", name="State Street SPDR S&P 500 ETF Trust", bars_used=121, annualized_vol_pct=12.1)
    dumped = profile.model_dump(exclude_none=True)
    assert dumped["name"] == "State Street SPDR S&P 500 ETF Trust"
    assert "beta_spy" not in dumped
    assert "corr_gld" not in dumped
    assert dumped["flags"] == ()


def test_news_items_are_frozen_and_a_headline_is_mandatory():
    item = NewsItem(symbol="aapl", headline="x")
    assert item.symbol == "AAPL"
    import pytest

    with pytest.raises(Exception):
        item.headline = "y"
