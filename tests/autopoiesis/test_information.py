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
