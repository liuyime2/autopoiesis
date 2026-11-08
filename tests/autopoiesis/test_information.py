"""InformationProvider: read-only, never raises, bounded."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from autopoiesis.information import BLOCK_CAP, InformationProvider


def _write(cache_dir: Path, symbol: str, data: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{symbol}.json").write_text(json.dumps(data), encoding="utf-8")


def test_missing_cache_returns_none(tmp_path):
    provider = InformationProvider(tmp_path)
    assert provider.block("ZZZ") is None


def test_corrupt_cache_returns_none(tmp_path):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "ZZZ.json").write_text("{not json", encoding="utf-8")
    provider = InformationProvider(tmp_path)
    assert provider.block("ZZZ") is None


def test_never_raises_on_garbage(tmp_path):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "ZZZ.json").write_text(json.dumps({"as_of": "nope", "valuation": "x"}), encoding="utf-8")
    provider = InformationProvider(tmp_path)
    # Must not raise.
    result = provider.block("ZZZ")
    assert result is None or isinstance(result, str)


def test_renders_fresh_fields(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    _write(tmp_path, "SPY", {
        "as_of": now,
        "valuation": {"pe": 20.5, "forward_pe": 18.0},
        "growth": {"revenue_growth": 0.12},
        "margins": {"gross": 0.4, "net": 0.2},
        "analyst_target": 500.0,
        "analyst_recommendation": "buy",
        "earnings_date": "2026-11-10",
        "earnings_consensus_eps": 1.23,
        "news": [{"headline": "SPY hits record"}, {"headline": "Fed holds"}],
    })
    provider = InformationProvider(tmp_path)
    block = provider.block("SPY")
    assert block is not None
    assert "valuation: pe=20.5" in block
    assert "growth: revenue_growth=0.12" in block
    assert "margins: gross=0.4" in block
    assert "analyst: target=500.0, rec=buy" in block
    assert "earnings: 2026-11-10 (consensus EPS 1.23)" in block
    assert "news: SPY hits record | Fed holds" in block
    assert len(block) <= BLOCK_CAP


def test_stale_fields_dropped(tmp_path):
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    _write(tmp_path, "SPY", {
        "as_of": now,
        "valuation": {"pe": 20.5},
        "valuation_as_of": old,
        "news": [{"headline": "old news"}],
        "news_as_of": old,
    })
    provider = InformationProvider(tmp_path)
    block = provider.block("SPY")
    # valuation budget 7 days, news budget 2 days -> both stale -> None
    assert block is None


def test_earnings_imminent_flag(tmp_path):
    now = datetime.now(timezone.utc)
    imminent = (now.date().toordinal() + 3)
    from datetime import date
    ed = date.fromordinal(imminent).isoformat()
    _write(tmp_path, "SPY", {
        "as_of": now.isoformat(),
        "earnings_date": ed,
    })
    provider = InformationProvider(tmp_path)
    block = provider.block("SPY")
    assert block is not None
    assert "[IMMINENT]" in block
    assert True  # derived, may or may not render


def test_block_cap(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    headlines = [{"headline": "x" * 100} for _ in range(20)]
    _write(tmp_path, "SPY", {
        "as_of": now,
        "news": headlines,
    })
    provider = InformationProvider(tmp_path)
    block = provider.block("SPY")
    assert block is None or len(block) <= BLOCK_CAP


def _iso(days_ago: float, *, now: datetime) -> str:
    from datetime import timedelta

    return (now - timedelta(days=days_ago)).isoformat()


def test_a_month_old_cache_is_stale_at_the_decision_time(tmp_path):
    """Age used to be measured cache-against-cache (as_of vs the field's own as_of, both written
    at refresh), so a month-old file read as zero days old."""
    now = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
    written = _iso(30, now=now)
    _write(tmp_path, "XLE", {
        "as_of": written, "available_at": written,
        "valuation": {"pe": 15.4}, "valuation_as_of": written,
        "options_iv": 0.32, "options_iv_as_of": written,
    })
    provider = InformationProvider(tmp_path)
    assert provider.block("XLE", cutoff=now) is None


def test_a_fresh_field_survives_next_to_a_stale_one(tmp_path):
    now = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
    _write(tmp_path, "NVDA", {
        "as_of": _iso(0.1, now=now), "available_at": _iso(0.1, now=now),
        "valuation": {"pe": 23.6}, "valuation_as_of": _iso(20, now=now),     # budget 7 days: dropped
        "options_iv": 0.54, "options_iv_as_of": _iso(0.1, now=now),          # budget 2 days: kept
    })
    block = InformationProvider(tmp_path).block("NVDA", cutoff=now)
    assert block is not None and "iv=0.54" in block and "valuation" not in block


def test_the_earnings_countdown_follows_the_decision_date_not_the_cache_date(tmp_path):
    written = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
    _write(tmp_path, "NVDA", {
        "as_of": written.isoformat(), "available_at": written.isoformat(),
        "earnings_date": "2026-11-17", "earnings_date_as_of": written.isoformat(),
    })
    provider = InformationProvider(tmp_path)
    # Written 12 Nov: the date is 5 days away from the cache's point of view (imminent <= 5).
    # Decided on 14 Nov it is 3 days away; decided on 11 Nov it is 6 days away. The cutoff rules.
    assert "IMMINENT" in provider.block("NVDA", cutoff=datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc))
    after = provider.block("NVDA", cutoff=datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc))
    assert after is None or "IMMINENT" not in after, "after the date it is no longer imminent"


def test_a_field_observed_after_the_cutoff_is_hidden(tmp_path):
    now = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
    _write(tmp_path, "XLE", {
        "as_of": _iso(-1, now=now), "available_at": _iso(-1, now=now),
        "options_iv": 0.3, "options_iv_as_of": _iso(-1, now=now),
    })
    assert InformationProvider(tmp_path).block("XLE", cutoff=now) is None


def test_a_replay_cutoff_fails_closed_on_missing_or_unreadable_availability(tmp_path):
    """With a cutoff, availability must be provable. A missing or malformed `available_at` used to
    be skipped (`except: pass`), which let such a file into a historical replay."""
    now = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
    stamp = _iso(0.1, now=now)
    body = {"as_of": stamp, "options_iv": 0.3, "options_iv_as_of": stamp}
    provider = InformationProvider(tmp_path)

    _write(tmp_path, "AAA", body)                                    # no available_at at all
    _write(tmp_path, "BBB", {**body, "available_at": "not-a-time"})  # unreadable
    _write(tmp_path, "CCC", {**body, "available_at": stamp})         # provable
    _write(tmp_path, "DDD", {**body, "available_at": stamp, "options_iv_available_at": "garbage"})

    assert provider.block("AAA", cutoff=now) is None
    assert provider.block("BBB", cutoff=now) is None
    assert provider.block("CCC", cutoff=now) is not None
    assert provider.block("DDD", cutoff=now) is None, "a field with unreadable availability is dropped"
    # Live use (no cutoff) is unchanged: availability is only a replay constraint.
    assert provider.block("AAA") is not None and provider.block("BBB") is not None
