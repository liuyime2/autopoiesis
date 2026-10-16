"""A floor that disengages itself must say so.

Measured 2026-10-15: the VIX cache's last point was 2026-10-09 and `vix_floor` refuses a
reading more than four days old - correctly, and silently. Nothing reported it. The floor
had been off since 2026-10-13, the doctor said `RESULT: OK`, and a risk input that P6
measured as a 12.4% forecast-error improvement was absent from every decision for two days
without one line saying so.

The staleness guard is not touched by this. What was missing is the alarm.

Usage: pytest tests/autopoiesis/test_vix_freshness_alarm.py -q
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from autopoiesis import risk_judgment
from autopoiesis.doctor import DoctorReport, Status, _check_vix_freshness
from autopoiesis.risk_judgment import VIX_STALE_AFTER_DAYS


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Point the module's own constant at a tmp file, which is what the check reads.

    A path rebuilt from the config was tried and removed: it could point somewhere
    `vix_floor` never looks and report on a file that does not matter.
    """
    path = tmp_path / "VIX.pkl"
    monkeypatch.setattr(risk_judgment, "VIX_CACHE", path)
    return path


def _write(path: Path, *, last: str | None, points: int = 260) -> None:
    if last is None:
        path.write_text("{}", encoding="utf-8")
        return
    end = date.fromisoformat(last)
    history = {(end - timedelta(days=i)).isoformat(): 18.0 for i in range(points)}
    path.write_text(json.dumps({"^VIX": history}), encoding="utf-8")


def _check() -> object:
    report = DoctorReport()
    _check_vix_freshness(report, object())
    checks = [c for c in report.checks if c.name == "vix freshness"]
    assert checks, report.checks
    return checks[0]


def test_a_stale_cache_warns_and_names_the_disengagement(cache):
    """The measured case: seven days old, floor off, nothing said so."""
    _write(cache, last="2026-10-09")
    check = _check()

    assert check.status is Status.WARN, check.detail
    assert "2026-10-09" in check.detail
    assert "disengaged" in check.detail, check.detail
    assert "nothing refreshes it on a schedule" in check.detail, check.detail


def test_a_fresh_cache_is_ok(cache):
    """And the alarm must not cry wolf, or it joins the warnings nobody reads."""
    _write(cache, last=date.today().isoformat())
    check = _check()

    assert check.status is Status.OK, check.detail
    assert "within the" in check.detail


def test_the_boundary_is_the_one_the_floor_uses(cache):
    """Not a second number. Two answers to "is this stale" is how they start disagreeing."""
    _write(cache, last=(date.today() - timedelta(days=VIX_STALE_AFTER_DAYS)).isoformat())
    assert _check().status is Status.OK

    _write(cache, last=(date.today() - timedelta(days=VIX_STALE_AFTER_DAYS + 1)).isoformat())
    assert _check().status is Status.WARN


def test_a_missing_cache_warns_rather_than_passing(cache):
    """No cache means no floor. A check that passes on absence is the defect one level up."""
    assert not cache.exists()
    check = _check()

    assert check.status is Status.WARN, check.detail
    assert "cannot engage" in check.detail


def test_an_empty_series_warns(cache):
    _write(cache, last=None)
    check = _check()

    assert check.status is Status.WARN
    assert "no ^VIX points" in check.detail


def test_the_floor_reads_the_same_constant():
    """The guard and the alarm must not be able to drift apart.

    They were: the floor had a literal 4 and the doctor had no check at all, so the only
    way to learn the floor was off was to read the cache's last date by hand.
    """
    import inspect

    assert "VIX_STALE_AFTER_DAYS" in inspect.getsource(risk_judgment.vix_floor), (
        "vix_floor must read the shared constant, not a literal of its own"
    )
    assert VIX_STALE_AFTER_DAYS == 4
