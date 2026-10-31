"""Replay the curriculum's real proposals since the ledger-aware build through the new admission rule.

Read-only: the specs are the real strategy files created from 2026-10-25T01:57Z, the ledger is the
real signal ledger, and nothing is written. Prints how many the hard rule would have refused.

Usage: PYTHONPATH=src python admission_replay.py <strategy_dir> <signal_trials.jsonl> <out.json>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from autopoiesis.guardian import Guardian
from autopoiesis.models import StrategySpec
from autopoiesis.strategy_admission import StrategyAdmission
from autopoiesis.strategy_engine import StrategyLibrary

SINCE = datetime(2026, 10, 25, 1, 57, tzinfo=timezone.utc)


def main() -> None:
    strategy_dir, ledger, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    guardian = Guardian(max_position_value=25_000.0, max_daily_loss=500.0, max_trades_per_day=100,
                        allowlist=frozenset({"SPY"}))
    gate = StrategyAdmission(guardian=guardian, strategy_library=StrategyLibrary(Path("/nonexistent-read-only")),
                             signal_ledger=ledger)
    rows = []
    for path in sorted(strategy_dir.glob("*.json")):
        spec = StrategySpec.model_validate_json(path.read_text(encoding="utf-8"))
        if spec.created_at < SINCE:
            continue
        reason = gate._refuted_rule_reason(spec)
        p = spec.parameters
        rows.append({"strategy_id": spec.strategy_id, "kind": spec.kind, "symbols": list(spec.symbols),
                     "signal": p.get("signal"), "lookback": p.get("lookback"), "lifecycle_now": spec.lifecycle,
                     "refused": reason is not None})
    rules = [r for r in rows if r["kind"] == "RULE"]
    refused = [r for r in rules if r["refused"]]
    summary = {
        "proposed_since": SINCE.isoformat(), "strategies": len(rows), "rules": len(rules),
        "rules_refused_by_the_ledger_rule": len(refused),
        "refused_that_are_now_paused_or_retired": sum(r["lifecycle_now"] in {"PAUSED", "RETIRED"} for r in refused),
        "rules_still_admitted": [(r["strategy_id"], r["signal"], r["lookback"]) for r in rules if not r["refused"]],
        "rows": rows,
    }
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print({k: v for k, v in summary.items() if k != "rows"})


if __name__ == "__main__":
    main()
