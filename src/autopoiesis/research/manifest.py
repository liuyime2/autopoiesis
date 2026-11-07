"""Verify that a run manifest still describes the spec and dataset it names.

A manifest is evidence only if the digests in it are recomputable. The runner
writes `spec_sha256` and `data.sha256`; this module reads them back, recomputes
both, and refuses a manifest whose numbers no longer match the files on disk.

Research only: nothing in the live path imports this.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from autopoiesis.replay import ReplayBar, load_bars


class ManifestError(ValueError):
    """The manifest and the files it names do not agree."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def dataset_fingerprint(symbol: str, bars: Sequence[ReplayBar]) -> str:
    """The canonical fingerprint used by the runner and this verifier.

    The canonical form is stated once here because two copies of "hash the bars"
    that drift apart would make every manifest fail for the wrong reason.
    """
    canonical = {"symbol": symbol.upper(), "bars": [bar.to_dict() for bar in bars]}
    return sha256_bytes(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def verify_manifest(
    manifest_path: str | Path,
    *,
    spec_path: str | Path | None = None,
    cache_path: str | Path | None = None,
) -> dict:
    """Recompute the spec and dataset digests and check the bar bounds.

    Returns a summary on success; raises `ManifestError` with the first mismatch.
    The caller decides whether a missing git commit is fatal.
    """
    manifest_file = Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read manifest {manifest_file}: {error}") from error

    spec = Path(spec_path) if spec_path is not None else Path(str(manifest.get("spec", "")))
    if not spec.is_file():
        raise ManifestError(f"spec not found: {spec}")
    expected_spec = manifest.get("spec_sha256")
    if not expected_spec:
        raise ManifestError("manifest has no spec_sha256")
    actual_spec = sha256_file(spec)
    if actual_spec != expected_spec:
        raise ManifestError(
            f"spec digest mismatch: manifest {expected_spec}, file {actual_spec}"
        )

    data = manifest.get("data")
    if not isinstance(data, dict):
        raise ManifestError("manifest has no data block")
    expected_data = data.get("sha256")
    if not expected_data:
        raise ManifestError("manifest data block has no sha256")

    if cache_path is not None:
        cache = Path(cache_path)
    else:
        source = str(data.get("source", ""))
        if not source.startswith("cache:"):
            raise ManifestError(
                "manifest data source is not a cache path; pass cache_path explicitly"
            )
        cache = Path(source[len("cache:") :])
    if not cache.is_file():
        raise ManifestError(f"bar cache not found: {cache}")

    bars = load_bars(cache)
    if not bars:
        raise ManifestError(f"bar cache is empty: {cache}")
    actual_data = dataset_fingerprint(str(data.get("symbol", "")), bars)
    if actual_data != expected_data:
        raise ManifestError(
            f"dataset digest mismatch: manifest {expected_data}, file {actual_data}"
        )

    expected_bars = data.get("bars")
    if expected_bars != len(bars):
        raise ManifestError(f"bar count mismatch: manifest {expected_bars}, file {len(bars)}")
    first = bars[0].timestamp.date().isoformat()
    last = bars[-1].timestamp.date().isoformat()
    if data.get("start") != first:
        raise ManifestError(f"start mismatch: manifest {data.get('start')}, file {first}")
    if data.get("end") != last:
        raise ManifestError(f"end mismatch: manifest {data.get('end')}, file {last}")

    return {
        "manifest": str(manifest_file),
        "spec": str(spec),
        "spec_sha256": actual_spec,
        "data": str(cache),
        "data_sha256": actual_data,
        "bars": len(bars),
        "symbol": data.get("symbol"),
        "start": first,
        "end": last,
        "code_commit": manifest.get("code_commit"),
    }
