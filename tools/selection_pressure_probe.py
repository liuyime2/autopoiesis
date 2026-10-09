"""Probe: does the lifecycle rule set actually react to scored evidence?

`make verify` calls this through `check-selection-pressure-reaches-the-rules`.

The failure it exists to catch is a real one that shipped: the REJECT veto lived in
`AgentDaemon._apply_offline_rejection` rather than in `StrategyLifecycleManager`, so the
promotion gate was reachable from the rule set and the rejection was not. Measured over 847
real bars, a strategy with 580 scored decisions and 304 losing trades - verdict
REJECT_POOR_DECISIONS - produced no ruling at all from `review()`.

So this asserts three things from the rule set alone, with no daemon in the picture:

1. REJECT retires, carrying the evidence in the reason;
2. INCONCLUSIVE and absent evidence do not - silence must never be read as failure;
3. the daemon's own rejection helper is no longer the only path, i.e. the rule exists in
   `_review_one` itself rather than only being called from the daemon.

Run standalone it prints a JSON list of whatever failed.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from autopoiesis.models import StrategySpec
from autopoiesis.offline_validation import OfflineValidationResult
from autopoiesis.strategy_engine import StrategyLifecycleManager, StrategyResult

BROKER_VERIFIED = {"broker_strategy_closed_lot_pnl_verified"}


def _spec(lifecycle: str = "PROBATION") -> StrategySpec:
    return StrategySpec(
        strategy_id="probe", name="probe", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        max_position_value=5000.0, enabled=True, lifecycle=lifecycle,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), rationale="probe",
    )


def _result() -> StrategyResult:
    return StrategyResult(
        strategy_id="probe", cycles=60, submitted_orders=45, rejected_orders=0,
        errors=0, score=0.5, trade_attempts=45,
        evaluated_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
    )


def _evidence(verdict: str, scored: int, correct: int) -> OfflineValidationResult:
    return OfflineValidationResult(
        strategy_id="probe", verdict=verdict, reason="probe", decisions=scored,
        scored=scored, good_holds=correct, false_trades=scored - correct,
    )


def probe() -> list[str]:
    import inspect

    problems: list[str] = []
    manager = StrategyLifecycleManager()

    # 1. REJECT retires, with the numbers in the reason.
    decided = manager.review([_spec()], [_result()], {"probe": _evidence("REJECT_POOR_DECISIONS", 580, 276)})
    retired = [d for d in decided if d.new_lifecycle == "RETIRED"]
    if not retired:
        problems.append(
            "a REJECT_POOR_DECISIONS verdict produced no retirement from the rule set; "
            f"decisions were {[(d.new_lifecycle, d.reason) for d in decided] or 'none'}"
        )
    else:
        reason = retired[0].reason
        if "580" not in reason or "0.476" not in reason:
            problems.append(f"the retirement does not carry its evidence: {reason!r}")

    # 2. Silence must never retire.
    for label, evidence in (
        ("INCONCLUSIVE", {"probe": _evidence("INCONCLUSIVE", 3, 0)}),
        ("absent", {}),
    ):
        out = manager.review([_spec()], [_result()], evidence)
        if [d for d in out if d.new_lifecycle == "RETIRED"]:
            problems.append(f"{label} evidence retired a strategy; silence is not failure")

    # 3. The veto is in the rules, not only reachable through the daemon.
    source = inspect.getsource(StrategyLifecycleManager._review_one)
    if "REJECT_POOR_DECISIONS" not in source:
        problems.append(
            "_review_one does not itself mention REJECT_POOR_DECISIONS, so the veto is "
            "still only a daemon side effect"
        )

    # A retirement must never claim broker verification it does not have.
    for decided in manager.review([_spec()], [_result()], {"probe": _evidence("REJECT_POOR_DECISIONS", 580, 276)}):
        if BROKER_VERIFIED & set(decided.reason.split()):
            problems.append("a screen-based retirement claimed broker verification")

    return problems


if __name__ == "__main__":
    print(json.dumps(probe()))
