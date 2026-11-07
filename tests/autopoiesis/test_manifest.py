"""The manifest verifier must recompute the digests it claims to check."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from autopoiesis.replay import ReplayBar, save_bars
from autopoiesis.research.manifest import (
    ManifestError,
    dataset_fingerprint,
    sha256_file,
    verify_manifest,
)


def _bars() -> list[ReplayBar]:
    return [
        ReplayBar(
            timestamp=datetime(2020, 1, 2, 5, tzinfo=timezone.utc),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1_000.0,
        ),
        ReplayBar(
            timestamp=datetime(2020, 1, 3, 5, tzinfo=timezone.utc),
            open=100.5,
            high=102.0,
            low=100.0,
            close=101.5,
            volume=1_100.0,
        ),
    ]


def _manifest(tmp_path, bars, spec, **overrides) -> dict:
    manifest = {
        "spec": str(spec),
        "spec_sha256": sha256_file(spec),
        "data": {
            "symbol": "SPY",
            "start": "2020-01-02",
            "end": "2020-01-03",
            "bars": len(bars),
            "sha256": dataset_fingerprint("SPY", bars),
            "source": f"cache:{tmp_path / 'bars.json'}",
        },
        "code_commit": "deadbeef",
    }
    manifest.update(overrides)
    return manifest


def test_dataset_fingerprint_is_symbol_case_insensitive() -> None:
    bars = _bars()
    assert dataset_fingerprint("spy", bars) == dataset_fingerprint("SPY", bars)


def test_fingerprint_changes_when_a_bar_changes() -> None:
    bars = _bars()
    changed = list(bars)
    changed[1] = replace(changed[1], close=999.0)
    assert dataset_fingerprint("SPY", bars) != dataset_fingerprint("SPY", changed)


def test_verify_manifest_round_trip(tmp_path) -> None:
    spec = tmp_path / "spec.txt"
    spec.write_text("instrument: SPY\n", encoding="utf-8")
    bars = _bars()
    save_bars(tmp_path / "bars.json", "SPY", bars)
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path, bars, spec)), encoding="utf-8")

    summary = verify_manifest(manifest_path)
    assert summary["bars"] == 2
    assert summary["start"] == "2020-01-02"
    assert summary["end"] == "2020-01-03"
    assert summary["spec_sha256"] == sha256_file(spec)


def test_verify_manifest_rejects_an_edited_dataset_digest(tmp_path) -> None:
    spec = tmp_path / "spec.txt"
    spec.write_text("instrument: SPY\n", encoding="utf-8")
    bars = _bars()
    save_bars(tmp_path / "bars.json", "SPY", bars)
    manifest = _manifest(tmp_path, bars, spec)
    manifest["data"]["sha256"] = "0" * 64
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestError, match="dataset digest mismatch"):
        verify_manifest(manifest_path)


def test_verify_manifest_rejects_an_edited_spec(tmp_path) -> None:
    spec = tmp_path / "spec.txt"
    spec.write_text("instrument: SPY\n", encoding="utf-8")
    bars = _bars()
    save_bars(tmp_path / "bars.json", "SPY", bars)
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path, bars, spec)), encoding="utf-8")
    spec.write_text("instrument: QQQ\n", encoding="utf-8")

    with pytest.raises(ManifestError, match="spec digest mismatch"):
        verify_manifest(manifest_path)


def test_verify_manifest_rejects_a_bar_count_mismatch(tmp_path) -> None:
    spec = tmp_path / "spec.txt"
    spec.write_text("instrument: SPY\n", encoding="utf-8")
    bars = _bars()
    save_bars(tmp_path / "bars.json", "SPY", bars)
    manifest = _manifest(tmp_path, bars, spec)
    manifest["data"]["bars"] = 99
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestError, match="bar count mismatch"):
        verify_manifest(manifest_path)
