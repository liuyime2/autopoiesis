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


def test_acquirees_come_from_mergers_only_and_follow_the_page_token():
    pages = {
        None: {"corporate_actions": {"cash_mergers": [{"acquiree_symbol": "ATVI", "effective_date": "2023-10-13"}],
                                     "name_changes": [{"old_symbol": "FB", "new_symbol": "META"}]},
               "next_page_token": "p2"},
        "p2": {"corporate_actions": {"stock_mergers": [{"acquiree_symbol": "PXD", "effective_date": "2024-05-03"},
                                                       {"acquiree_symbol": "123CVR456"}]},
               "next_page_token": None},
    }

    class Client:
        def data_get(self, path, query, api_version):
            assert path == "/corporate-actions" and api_version == "v1beta1"
            assert "name_change" not in query["types"]
            return pages[query.get("page_token")]

    assert fetch.acquired_symbols(Client(), "2023-01-01", "2023-12-31") == {"ATVI": "2023-10-13", "PXD": "2024-05-03"}


def test_an_acquiree_is_added_once_and_a_reused_ticker_is_dropped():
    from collections import Counter

    listed = [{"symbol": "CERN", "status": "inactive"}, {"symbol": "MSFT", "status": "active"}]
    dropped: Counter = Counter()
    # QQQ is held by an active listing that the exchange filter left out of `listed`.
    out, added, already = fetch.add_acquirees(listed, {"CERN": "2022-06-08", "ATVI": "2023-10-13",
                                                       "MSFT": "x", "QQQ": "y"}, {"MSFT", "QQQ"}, dropped)
    assert [a["symbol"] for a in out] == ["ATVI", "CERN", "MSFT"]
    assert (added, already) == (1, 2)
    assert next(a for a in out if a["symbol"] == "ATVI")["status"] == "acquired"
    assert dropped["acquired: ticker reused by an active listing"] == 1


# Synthetic excerpts in the two Nasdaq Trader directory layouts (column order differs by file).
NASDAQ_DIR = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. Common Stock|Q|N|N|100|N|N
MUU|Direxion Daily MU Bull 2X ETF|G|N|N|100|Y|N
ZETFT|A test ETF|G|Y|N|100|Y|N
File Creation Time: 1124202621:32|||||||
"""
OTHER_DIR = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK.B|N|40|N|BRK.B
GLD|SPDR Gold Shares|P|GLD|Y|100|N|GLD
File Creation Time: 1124202621:32||||||
"""


def test_etp_symbols_reads_both_layouts_by_header_and_skips_test_issues():
    import pytest

    assert fetch.etp_symbols(NASDAQ_DIR, OTHER_DIR) == {"MUU", "GLD"}
    with pytest.raises(ValueError):
        fetch.etp_symbols("Symbol|Name\nAAA|x\n")


def test_the_etp_filter_drops_active_products_only_and_is_off_by_default():
    active = [{"symbol": s, "class": "us_equity", "exchange": "NASDAQ", "name": s} for s in ("AAPL", "MUU")]
    inactive = [{"symbol": "OLDE", "class": "us_equity", "exchange": "NYSE", "name": "gone"}]
    default, dropped_default = fetch.select_assets(active, inactive)
    assert [a["symbol"] for a in default] == ["AAPL", "MUU", "OLDE"]
    assert fetch.ETP_DROP not in dropped_default
    kept, dropped = fetch.select_assets(active, inactive, {"MUU", "OLDE"})
    assert [a["symbol"] for a in kept] == ["AAPL", "OLDE"]  # inactive names are not classified
    assert dropped[fetch.ETP_DROP] == 1


def test_the_unfiltered_digest_is_unchanged_and_a_filtered_one_differs():
    import hashlib
    import json

    entries = {"X": {"fingerprint": "1"}, "Y": {"fingerprint": "2"}}
    # The v5/v6 definition, written out: sha256 of the sorted symbol -> fingerprint JSON.
    v6 = hashlib.sha256(json.dumps({"X": "1", "Y": "2"}, sort_keys=True).encode("utf-8")).hexdigest()
    assert fetch.set_digest(entries) == v6
    filtered = fetch.set_digest(entries, [{"sha256": "a"}, {"sha256": "b"}])
    assert filtered != v6
    assert filtered == fetch.set_digest(entries, [{"sha256": "b"}, {"sha256": "a"}])
    assert filtered != fetch.set_digest(entries, [{"sha256": "a"}, {"sha256": "c"}])
