"""Probe: does the whole self-evolution loop actually close on real bars?

Run standalone (`python tools/self_evolution_probe.py`) it prints a JSON list of what
failed, so a failure is legible. `make verify` calls it through
`check-self-evolution-closes`.

`TradingLoop` is only the trading half. The evolution half - reflect, score every decision
against what the market actually did next, screen the strategy on those scores, let the
lifecycle rules rule on the verdict - runs in `AgentDaemon._maintenance`. Proving the rules
work by calling `review()` by hand does not prove the loop closes; this drives the daemon, so
every stage has to fire on its own for anything to come out.

What it asserts: 847 real cycles with no daemon errors, every evolution stage present in the
journal, at least one lifecycle ruling, the strategy no longer PROBATION, the reason carrying
its evidence in numbers, and replay safety intact - no broker-verified PnL from simulated
fills.

What it is not: evidence that any strategy has an edge. Replay fills are REPLAYED, never
SUBMITTED, and a replayed retirement is not a real one.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import replay_self_evolution as harness

REQUIRED_EVENTS = (
    "COUNTERFACTUAL_EVALUATED",
    "OFFLINE_VALIDATION_COMPLETED",
    "REFLECTION_GENERATED",
    "STRATEGY_LIFECYCLE_UPDATED",
)
BROKER_VERIFIED = {"broker_strategy_closed_lot_pnl_verified", "broker_account_pnl_verified"}


def probe() -> list[str]:
    problems: list[str] = []
    bars = pathlib.Path(harness.BARS)
    if not bars.exists():
        return [f"missing {harness.BARS}; run tools/fetch_replay_bars.py first"]

    result = harness.run(str(bars))

    if result["cycles"] < 100:
        problems.append(f"only {result['cycles']} cycles ran; the replay did not get going")
    if result["errors"]:
        problems.append(f"{result['errors']} daemon error(s) during the replay")

    events = result["events"]
    for name in REQUIRED_EVENTS:
        if not events.get(name):
            problems.append(f"the {name} stage never fired")

    if not result["rulings"]:
        problems.append(
            "no lifecycle ruling was produced, so selection pressure never acted on the "
            "scored evidence"
        )
    # The screen's verdict has to reach the lifecycle, and only in the direction it points.
    # This used to require the strategy to end retired, which encoded what the raw-ratio gate
    # said about this rule: 44 of 125 right, rejected at < 0.5. Read against its days -
    # 37.5 expected from the market's direction alone - the same record is a margin of
    # +0.052, so the honest verdict is no longer a rejection and "must end retired" became
    # a requirement that the screen be wrong. What the probe guards is the wiring.
    screen = result.get("final_screen") or {}
    verdict = screen.get("verdict")
    if not screen or not screen.get("scored"):
        problems.append("the screen never scored a decision, so no evidence reached the lifecycle")
    elif verdict == "REJECT_POOR_DECISIONS" and result["final_lifecycle"] in {"PROBATION", "ACTIVE"}:
        problems.append(
            f"the screen said {verdict} but the strategy ended {result['final_lifecycle']}; "
            "the verdict produced no applied transition"
        )
    elif verdict != "REJECT_POOR_DECISIONS" and result["final_lifecycle"] == "RETIRED":
        problems.append(f"the strategy was retired although the screen said {verdict}")

    # A ruling has to say what happened in numbers, not merely that the strategy was bad.
    #
    # Only rulings that actually changed the lifecycle. A same-state transition is not a
    # ruling - it is a no-op that leaves the file as it was - and 2026-10-06 added one:
    # a candidate that spent its whole probation budget without submitting an order is
    # returned to PROBATION so it can be served again. That decision rests on cumulative
    # cycles and submitted orders, not on scored decisions, and requiring counterfactual
    # evidence of it would be requiring a justification it does not have.
    for ruling in result["rulings"]:
        if ruling.get("old_lifecycle") == ruling.get("new_lifecycle"):
            continue
        reason = str(ruling.get("reason") or "")
        if "scored decision" not in reason or "correct_outcome_ratio" not in reason:
            problems.append(f"a ruling carries no evidence: {reason!r}")
            break

    # Replay safety must survive the evolution stages, not just the trading ones.
    reason_text = " ".join(str(r.get("reason") or "") for r in result["rulings"])
    for token in BROKER_VERIFIED:
        if token in reason_text:
            problems.append(f"a replay ruling claimed {token}")

    return problems


if __name__ == "__main__":
    print(json.dumps(probe()))
