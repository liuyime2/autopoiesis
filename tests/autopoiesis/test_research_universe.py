"""Point-in-time universe membership and the asset filter. All data here is synthetic."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from autopoiesis.replay import ReplayBar
from autopoiesis.research.universe import members_at

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import fetch_universe_bars as fetch

START = date(2024, 1, 1)


def _bars(days: int, close: float, volume: float, start: date = START) -> list[ReplayBar]:
    out = []
    for i in range(days):
        stamp = datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc)
        out.append(ReplayBar(timestamp=stamp, open=close, high=close, low=close, close=close, volume=volume))
    return out


def test_the_filter_keeps_listed_well_formed_equities_and_drops_reused_tickers():
    active = [
        {"symbol": "AAA", "class": "us_equity", "exchange": "NYSE", "name": "A"},
        {"symbol": "BBB", "class": "us_equity", "exchange": "ARCA", "name": "B fund"},
        {"symbol": "BTC/USD", "class": "crypto", "exchange": "CRYPTO"},
    ]
    inactive = [
        {"symbol": "AAA", "class": "us_equity", "exchange": "NYSE", "name": "an older A"},
        {"symbol": "DEAD", "class": "us_equity", "exchange": "NASDAQ", "name": "failed"},
        {"symbol": "0029900E0", "class": "us_equity", "exchange": "NASDAQ"},
        {"symbol": "PINK", "class": "us_equity", "exchange": "OTC"},
    ]
    kept, dropped = fetch.select_assets(active, inactive)
    assert [(a["symbol"], a["status"]) for a in kept] == [("AAA", "active"), ("DEAD", "inactive")]
    assert dropped["inactive: ticker reused by an active listing"] == 1
    assert dropped["inactive: ticker not well formed"] == 1
    assert dropped["active: not a listed exchange"] == 1 and dropped["inactive: not a listed exchange"] == 1
    assert dropped["active: not us_equity"] == 1


def test_membership_never_reads_a_bar_on_or_after_the_day():
    day = START + timedelta(days=200)
    panel = {"AAA": _bars(300, 10.0, 1_000), "BBB": _bars(300, 10.0, 2_000)}
    assert members_at(day, panel, top_n=1) == ["BBB"]
    # Make AAA enormous from `day` on: a member list that looked ahead would change.
    for i, bar in enumerate(panel["AAA"]):
        if bar.timestamp.date() >= day:
            panel["AAA"][i] = ReplayBar(timestamp=bar.timestamp, open=10, high=10, low=10, close=10, volume=1e12)
    assert members_at(day, panel, top_n=1) == ["BBB"]


def test_a_name_whose_data_ended_stops_being_a_member():
    panel = {"LIVE": _bars(300, 10.0, 1_000), "GONE": _bars(150, 10.0, 9_000)}
    assert members_at(START + timedelta(days=149), panel, top_n=2) == ["GONE", "LIVE"]
    assert members_at(START + timedelta(days=200), panel, top_n=2) == ["LIVE"]


def test_short_history_and_low_price_are_excluded_and_ties_are_deterministic():
    day = START + timedelta(days=200)
    panel = {"NEW": _bars(60, 50.0, 1e9, start=day - timedelta(days=60)),
             "PENNY": _bars(300, 1.0, 1e9), "XB": _bars(300, 10.0, 100), "XA": _bars(300, 10.0, 100)}
    assert members_at(day, panel, top_n=5) == ["XA", "XB"]


def test_the_set_digest_depends_only_on_the_fingerprints():
    a = {"X": {"fingerprint": "1", "bars": 3}, "Y": {"fingerprint": "2", "bars": 4}}
    b = {"Y": {"fingerprint": "2", "bars": 9}, "X": {"fingerprint": "1", "bars": 0}}
    assert fetch.set_digest(a) == fetch.set_digest(b)
    assert fetch.set_digest(a) != fetch.set_digest({"X": {"fingerprint": "1"}})


def test_only_the_rate_limit_is_retried_and_it_still_fails_when_it_lasts():
    class Client:
        def __init__(self, errors):
            self.errors = list(errors)

        def get_bars(self, *a, **k):
            if self.errors:
                raise RuntimeError(self.errors.pop(0))
            return type("R", (), {"df": "frame"})()

    slept = []
    assert fetch.get_bars_with_backoff(Client(["too many requests"]), ["X"], "s", "e", sleep=slept.append) == "frame"
    assert slept == [60.0]
    import pytest
    with pytest.raises(RuntimeError, match="invalid symbol"):
        fetch.get_bars_with_backoff(Client(["invalid symbol: X"]), ["X"], "s", "e", sleep=slept.append)
    with pytest.raises(RuntimeError, match="too many"):
        fetch.get_bars_with_backoff(Client(["too many requests"] * 5), ["X"], "s", "e", sleep=lambda s: None)
