"""Answer, for one strategy id, why it is where it is.

Step 1.11 of docs/EXECUTION_PLAN_2026-11-05.md. Read-only: it inspects the
strategy library, the experiment registry and the journal, and prints what is known
about the strategy and what evidence it still needs. It never mutates anything.

Usage:
    python3 tools/why.py STRATEGY=<id>
    make why STRATEGY=<id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.strategy_engine import StrategyLibrary


def _strategy_dir() -> Path:
    env = os.environ.get("PAUSE_STRATEGY_DIR")
    if env:
        return Path(env)
    return ROOT / "runtime" / "autopoiesis" / "strategies"


def explain(strategy_id: str) -> int:
    lib = StrategyLibrary(_strategy_dir())
    spec = lib.try_load(strategy_id)
    if spec is None:
        print(f"strategy {strategy_id!r}: not found in {_strategy_dir()}")
        # Maybe it exists under a different casing; list close matches.
        if _strategy_dir().exists():
            close = [p.stem for p in _strategy_dir().glob("*.json")
                     if strategy_id.lower() in p.stem.lower()]
            if close:
                print("close matches:", ", ".join(close[:10]))
        return 1

    print(f"strategy_id: {spec.strategy_id}")
    print(f"name:        {spec.name}")
    print(f"kind:        {spec.kind}")
    print(f"symbols:     {', '.join(spec.symbols)}")
    print(f"lifecycle:   {spec.lifecycle}")
    print(f"enabled:     {spec.enabled}")
    print(f"created_at:  {spec.created_at}")
    print(f"max_position_value: {spec.max_position_value}")
    print(f"parameters:  {json.dumps(spec.parameters, sort_keys=True)}")
    if spec.lifecycle_reason:
        print(f"lifecycle_reason: {spec.lifecycle_reason}")
    print(f"probation_restarts: {spec.probation_restarts}")

    # Experiment registry (if present).
    registry_path = ROOT / "runtime" / "autopoiesis" / "experiment_registry.json"
    if registry_path.exists():
        try:
            reg = json.loads(registry_path.read_text(encoding="utf-8"))
            entries = reg.get("strategies", {}) if isinstance(reg, dict) else {}
            info = entries.get(spec.strategy_id)
            if info:
                print("experiment_registry:")
                print(json.dumps(info, indent=2, sort_keys=True))
        except (OSError, ValueError, json.JSONDecodeError):
            print("experiment_registry: unreadable")

    # Journal: recent rejections / evidence for this strategy.
    journal_path = ROOT / "runtime" / "autopoiesis" / "journal.jsonl"
    if journal_path.exists():
        try:
            from autopoiesis.journal import JsonlJournal

            journal = JsonlJournal(journal_path, strict=False, lock_timeout=5.0)
            events = journal.read_events()
            related = [e for e in events if getattr(e, "strategy_id", None) == spec.strategy_id]
            print(f"journal events naming this strategy: {len(related)}")
            last = related[-3:]
            for e in last:
                print(f"  - {e.timestamp} {e.event_type} {e.status}: {e.message}")
        except Exception as exc:
            print(f"journal: unreadable ({type(exc).__name__}: {exc})")

    print()
    print("next: a strategy that is PAUSED/RETIRED needs an admission decision;")
    print("      PROBATION needs PROBATION_CYCLES served before screening.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strategy", nargs="?", help="STRATEGY=<id> or bare id")
    args = parser.parse_args(argv)

    raw = args.strategy or os.environ.get("STRATEGY")
    if raw is None and len(sys.argv) > 1:
        # allow `make why STRATEGY=x` passing through as env already; also handle key=value
        for a in sys.argv[1:]:
            if a.startswith("STRATEGY="):
                raw = a.split("=", 1)[1]
    if raw is None:
        parser.error("STRATEGY=<id> is required")
    if raw.startswith("STRATEGY="):
        raw = raw.split("=", 1)[1]
    return explain(raw.strip())


if __name__ == "__main__":
    raise SystemExit(main())
