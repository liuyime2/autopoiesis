"""Verify a run manifest against its spec, bar cache and git history.

The runner writes the digests; this command recomputes them. A manifest that was
edited after the run fails with a non-zero exit and the first mismatch, rather
than being quoted as evidence.

Usage:
    python tools/verify_run_manifest.py \
        --manifest reports/20261106/v2/run_manifest.json \
        --spec configs/experiment_4arm_v2.spec
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.research.manifest import ManifestError, verify_manifest


def _commit_exists(commit: str) -> bool:
    if not commit or commit == "unknown":
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _is_ancestor(commit: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", default="reports/20261106/v2/run_manifest.json"
    )
    parser.add_argument("--spec", default="configs/experiment_4arm_v2.spec")
    parser.add_argument(
        "--cache",
        default=None,
        help="bar cache; defaults to the manifest's cache: source",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="skip the code-commit existence and ancestry checks",
    )
    args = parser.parse_args(argv)

    try:
        summary = verify_manifest(
            args.manifest, spec_path=args.spec, cache_path=args.cache
        )
    except ManifestError as error:
        print(f"FAIL: {error}")
        return 1

    commit = str(summary.get("code_commit") or "")
    if not args.no_git:
        if not _commit_exists(commit):
            print(f"FAIL: code commit {commit!r} is not in this repository")
            return 1
        if not _is_ancestor(commit):
            print(f"FAIL: code commit {commit} is not an ancestor of HEAD")
            return 1

    print(
        "OK: spec and dataset digests match; "
        f"{summary['bars']} {summary['symbol']} bars "
        f"{summary['start']}..{summary['end']}; code commit {commit}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
