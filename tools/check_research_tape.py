"""Verify the same registered execution contract and tape the research driver consumes."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("cache", nargs="?", type=Path)
    parser.add_argument("--symbol")
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--fetch-arguments", action="store_true",
                        help="print verified symbol/start/end/interval before any fetch")
    args = parser.parse_args(argv)
    from autopoiesis.research import driver

    try:
        spec, _settings, _digest, _registration = driver.registered_contract(args.spec, args.symbol, args.registry)
        if args.fetch_arguments:
            for key in ("instrument", "window_start_utc", "window_end_utc", "interval"):
                print(spec[key])
            return 0
        if args.cache is None:
            raise ValueError("a cache directory is required for tape validation")
        prepared = driver.prepare_research(args.cache, args.symbol or spec["instrument"],
                                           args.spec, args.registry)
    except (OSError, ValueError) as exc:
        print(f"research refused: {exc}", file=sys.stderr)
        return 2
    print(f"symbol: {prepared.symbol}")
    print(f"tape provenance: {prepared.source}")
    print(f"spec_sha256: {prepared.spec_digest}")
    print(f"registered_at: {prepared.registration['registered_at']}")
    print(f"preregistration_scope: {prepared.preregistration_scope}")
    print(f"evaluator_sha256: {prepared.evaluator_sha256}")
    print(f"evaluation_identity: {prepared.evaluation_identity}")
    print(f"dataset_fingerprint: {driver.dataset_fingerprint(prepared.bars)}")
    if prepared.ignored:
        print(f"ignored: {', '.join(prepared.ignored)}")
    from autopoiesis.research.prereg import check_tape

    print(check_tape(prepared.bars, prepared.contract).render())
    print("integrity gate passed; this does not establish scientific preregistration or an edge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
