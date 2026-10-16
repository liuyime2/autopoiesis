"""The VIX cache must be refreshable on a schedule, not only by hand.

Measured 2026-10-15: the cache's last point was 2026-10-09, the floor refuses a reading
more than four days old, and the research path that populated the cache is run by hand.
So the floor had been silently disengaged since 2026-10-13.

The doctor now warns. This is the other half: a refresh that keeps it from going stale.

The network is never touched here. `refresh` merges an endpoint response, and that merge
is the part that can be wrong - writing what the endpoint returned would discard the
9,263-point history the six-year quality window reads, on every run.

Usage: pytest tests/autopoiesis/test_vix_refresh.py -q
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from tools import refresh_vix


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "VIX.pkl"
    monkeypatch.setattr(refresh_vix, "CACHE", path)
    return path


def _seed(path: Path, *, last: str, points: int = 300) -> None:
    end = date.fromisoformat(last)
    history = {(end - timedelta(days=i)).isoformat(): 20.0 for i in range(points)}
    path.write_text(json.dumps({"^VIX": history}), encoding="utf-8")


# --- the merge, which is the part that can lose data -------------------------


def test_a_refresh_keeps_the_history_it_did_not_fetch(cache, monkeypatch):
    """The endpoint returns a bounded window; the cache goes back to 1990.

    Replacing rather than merging would throw away the long history on every run, and the
    six-year quality window reads it.
    """
    _seed(cache, last="2026-10-09", points=300)
    monkeypatch.setattr(refresh_vix, "_fetch", lambda symbol, **kw: {"2026-10-15": 18.0})

    refresh_vix.refresh(symbols=("^VIX",))

    history = json.loads(cache.read_text())["^VIX"]
    assert len(history) == 301, f"the merge lost points: {len(history)}"
    assert history["2026-10-15"] == 18.0
    assert history["2026-10-09"] == 20.0, "an existing point was overwritten by absence"


def test_a_refresh_replaces_a_point_it_did_fetch(cache, monkeypatch):
    """A revised close is a correction, not a duplicate."""
    _seed(cache, last="2026-10-09", points=5)
    monkeypatch.setattr(refresh_vix, "_fetch", lambda symbol, **kw: {"2026-10-09": 33.0})

    refresh_vix.refresh(symbols=("^VIX",))

    assert json.loads(cache.read_text())["^VIX"]["2026-10-09"] == 33.0


def test_a_failed_fetch_leaves_the_cache_alone(cache, monkeypatch):
    """A refresh that fails must not corrupt what is there."""
    _seed(cache, last="2026-10-09", points=5)
    before = cache.read_text()

    def broken(symbol, **kw):
        raise ConnectionError("no network")

    monkeypatch.setattr(refresh_vix, "_fetch", broken)
    with pytest.raises(ConnectionError):
        refresh_vix.refresh(symbols=("^VIX",))

    assert cache.read_text() == before


# --- the freshness gate, which is what the timer's value depends on ----------


def test_check_is_non_zero_when_stale(cache):
    _seed(cache, last="2026-10-09")
    assert refresh_vix.check() == 1


def test_check_is_zero_when_fresh(cache):
    _seed(cache, last=date.today().isoformat())
    assert refresh_vix.check() == 0


def test_check_uses_the_floors_own_ceiling(cache):
    """Not a second number. The timer's alarm and the floor must agree on what stale is."""
    from autopoiesis.risk_judgment import VIX_STALE_AFTER_DAYS

    _seed(cache, last=(date.today() - timedelta(days=VIX_STALE_AFTER_DAYS)).isoformat())
    assert refresh_vix.check() == 0
    _seed(cache, last=(date.today() - timedelta(days=VIX_STALE_AFTER_DAYS + 1)).isoformat())
    assert refresh_vix.check() == 1


def test_check_reports_stale_when_there_is_no_cache(cache):
    assert not cache.exists()
    assert refresh_vix.check() == 1


# --- and the timer exists, because a refresh nobody runs is not a refresh ----


def test_a_pre_open_timer_is_declared():
    """The whole finding was that nothing ran on a schedule."""
    root = Path(__file__).resolve().parents[2]
    timer = (root / "tools" / "vix-refresh.timer.in").read_text(encoding="utf-8")
    service = (root / "tools" / "vix-refresh.service.in").read_text(encoding="utf-8")

    assert "OnCalendar=Mon..Fri" in timer, "the refresh must run on trading mornings"
    assert "08:15" in timer, (
        "it must run before the 08:45 preflight, so the floor is engaged for the session "
        "rather than one cycle into it"
    )
    assert "Persistent=true" in timer, (
        "a missed run means a stale reading, which silently disengages the floor"
    )
    assert "vix-refresh" in service
    assert "read-only by contract" in service, (
        "the reason this is not folded into market-preflight, which is read-only"
    )


def test_the_installer_knows_about_the_new_units():
    """A template nobody renders is a file, not a unit."""
    root = Path(__file__).resolve().parents[2]
    source = (root / "tools" / "install_market_readiness.py").read_text(encoding="utf-8")

    assert '"vix-refresh.service"' in source
    assert '"vix-refresh.timer"' in source
