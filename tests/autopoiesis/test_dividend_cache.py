"""The dividend cache and its refresh tool, which are in the tree.

The adjustment that consumes this cache was attempted on 2026-10-17 and reverted: applying
it inside `counterfactual._price_series` corrupted `price_at_horizon`, which
`test_point_in_time` asserts is a real observed quote, and it moved real verdicts because
SPY's 2026-06-18 ex-date falls inside the retained window. The adjustment belongs on the
return expression, not on the series. See the plan's section 3e.

So this file covers what exists: the provider, the merge, and the coverage measurement.

Usage: pytest tests/autopoiesis/test_dividend_cache.py -q
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from tools import refresh_dividends as rd


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "DIVIDENDS.pkl"
    monkeypatch.setattr(rd, "CACHE", path)
    return path


def test_a_refresh_merges_rather_than_replaces(cache, monkeypatch):
    """A short provider window must not discard history the series still needs."""
    cache.write_text(json.dumps({"TLT": {"2026-08-03": 0.33}}), encoding="utf-8")
    monkeypatch.setattr(rd, "_fetch", lambda symbol, **kw: {"2026-11-02": 0.31})

    rd.refresh(symbols=("TLT",))

    rows = json.loads(cache.read_text())["TLT"]
    assert rows == {"2026-08-03": 0.33, "2026-11-02": 0.31}


def test_a_revised_rate_replaces_the_old_one(cache, monkeypatch):
    cache.write_text(json.dumps({"TLT": {"2026-08-03": 0.30}}), encoding="utf-8")
    monkeypatch.setattr(rd, "_fetch", lambda symbol, **kw: {"2026-08-03": 0.33})

    rd.refresh(symbols=("TLT",))

    assert json.loads(cache.read_text())["TLT"]["2026-08-03"] == 0.33


def test_a_failed_fetch_leaves_the_cache_alone(cache, monkeypatch):
    cache.write_text(json.dumps({"TLT": {"2026-08-03": 0.33}}), encoding="utf-8")
    before = cache.read_text()

    def broken(symbol, **kw):
        raise ConnectionError("no network")

    monkeypatch.setattr(rd, "_fetch", broken)
    with pytest.raises(ConnectionError):
        rd.refresh(symbols=("TLT",))

    assert cache.read_text() == before


def test_check_reports_stale_when_there_is_no_cache(cache):
    assert not cache.exists()
    assert rd.check() == 1


def test_check_passes_on_a_recent_cache(cache):
    recent = (date.today() - timedelta(days=5)).isoformat()
    cache.write_text(json.dumps({s: {recent: 0.3} for s in rd.SYMBOLS}), encoding="utf-8")
    assert rd.check() == 0


def test_check_reports_stale_when_the_newest_ex_date_is_old(cache):
    """Monthly payers, so a gap of more than six weeks means a refresh was missed."""
    old = (date.today() - timedelta(days=60)).isoformat()
    cache.write_text(json.dumps({s: {old: 0.3} for s in rd.SYMBOLS}), encoding="utf-8")
    assert rd.check() == 1


def test_the_cycled_symbols_are_all_requested():
    """The set a dividend matters for is the set that can be held."""
    for symbol in ("SPY", "BIL", "TLT", "XLB", "XLE", "XLF"):
        assert symbol in rd.SYMBOLS


def test_the_live_cache_covers_every_cycled_symbol():
    """Measured, and it corrects an earlier finding.

    BIL was reported as having no records on 2026-10-17. That was a date-range artifact in
    the query - `start=2026-06-01` against a provider that returns what it returns - not a
    gap in the provider. With the tool's 400-day lookback BIL has 13, as does TLT.
    """
    if not rd.CACHE.exists():
        pytest.skip("no dividend cache on this host")
    data = json.loads(rd.CACHE.read_text(encoding="utf-8"))
    missing = [s for s in rd.SYMBOLS if not data.get(s)]
    assert not missing, f"no dividend records for {missing}"


def test_the_live_cache_has_a_recent_ex_date():
    """The hard deadline: TLT distributes monthly and is cycled every session."""
    if not rd.CACHE.exists():
        pytest.skip("no dividend cache on this host")
    data = json.loads(rd.CACHE.read_text(encoding="utf-8"))
    newest = max(ex for rows in data.values() for ex in rows)
    age = (date.today() - date.fromisoformat(newest)).days
    assert age < 60, f"newest ex-date {newest} is {age} days old"
