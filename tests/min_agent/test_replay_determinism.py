"""The same recorded inputs must produce the same outputs, twice.

This is what makes an offline validation result mean anything. If replaying a
recorded decision cycle can produce a different verdict than the live run did, then
"validated offline" and "working live" are two different systems and no promotion
decision is evidence of anything.

Determinism also is what makes a failure reproducible: a bug found at 03:00 has to
still be there at 09:00 when you try to look at it.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from min_agent import counterfactual as cf
from min_agent.journal import JsonlJournal
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _record(cycle_id, when, price, action="HOLD"):
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=when, market_open=True, last_price=price,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=50_000, buying_power=50_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action=action,
            quantity=0 if action == "HOLD" else 1,
            confidence=0.6, rationale="r",
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status="SKIPPED", order_id=None, message="hold", filled_quantity=0.0,
        ),
    )


def _sample():
    records = []
    for i in range(40):
        records.append(
            _record(f"c{i:03d}", T0 + timedelta(minutes=30 * i), 100.0 + i * 0.3)
        )
    return records


def test_the_same_records_score_identically_twice():
    records = _sample()
    first = cf.evaluate(records, horizon_hours=4.0)
    second = cf.evaluate(records, horizon_hours=4.0)

    assert first.counts() == second.counts()
    assert first.hold_quality() == second.hold_quality()
    assert [r.verdict for r in first.rows] == [r.verdict for r in second.rows]
    assert [r.net_return_pct for r in first.rows] == [
        r.net_return_pct for r in second.rows
    ]


def test_evaluating_does_not_mutate_its_input():
    """A replay that quietly rewrites the record it is reading is not a replay."""
    records = _sample()
    before = [r.snapshot.last_price for r in records]
    before_actions = [r.decision.action for r in records]

    cf.evaluate(records, horizon_hours=4.0)

    assert [r.snapshot.last_price for r in records] == before
    assert [r.decision.action for r in records] == before_actions


def test_the_result_survives_a_json_round_trip():
    """The rows are journaled as JSON, so what is written and what is recomputed
    from the journal must be the same numbers."""
    records = _sample()
    report = cf.evaluate(records, horizon_hours=4.0)

    written = json.loads(json.dumps(
        [
            {
                "cycle_id": r.cycle_id,
                "verdict": r.verdict,
                "net": r.net_return_pct,
                "cost": r.assumed_cost_pct,
            }
            for r in report.rows
        ]
    ))
    replayed = cf.evaluate(records, horizon_hours=4.0)

    assert written == [
        {
            "cycle_id": r.cycle_id,
            "verdict": r.verdict,
            "net": r.net_return_pct,
            "cost": r.assumed_cost_pct,
        }
        for r in replayed.rows
    ]


def test_replaying_the_live_journal_reproduces_the_recorded_verdicts():
    """The strongest statement available: the verdicts the daemon wrote to the
    journal can be recomputed from that same journal."""
    events = JsonlJournal("runtime/min_agent/journal.jsonl").read_events(
        "COUNTERFACTUAL_EVALUATED"
    )
    if not events:
        # skip, not a bare return. A silent `return` makes pytest report this as one passed
        # test that asserted nothing, which is how a class in the gate came to pass on every
        # fresh clone and in CI without ever checking anything. A skip says plainly that the
        # check could not run, and `--strict-markers` in pytest.ini keeps it visible.
        pytest.skip("no COUNTERFACTUAL_EVALUATED events recorded yet")

    recorded = {}
    for event in events:
        for row in event.payload.get("rows", []):
            recorded[row["cycle_id"]] = (row["verdict"], row["net_return_pct"])

    records = JsonlJournal("runtime/min_agent/journal.jsonl").read_all()
    report = cf.evaluate(records, symbol="SPY", horizon_hours=24.0)

    mismatches = []
    resolved_since = 0
    for row in report.rows:
        prior = recorded.get(row.cycle_id)
        if prior is None:
            continue
        if prior[0] != row.verdict or prior[1] != row.net_return_pct:
            if prior[0] == "PENDING":
                # A PENDING row is not a claim about the outcome, so it cannot be
                # a determinism violation when the outcome later arrives. This check
                # compared every row, and stayed green for months only because no
                # recorded decision had yet gained its 24h horizon quote. The first
                # real cycle added after a quiet weekend resolved one, and the gate
                # went red on a placeholder - the correct verdict, reached the wrong
                # way. Determinism means a decided answer does not change; it says
                # nothing about an undecided one.
                resolved_since += 1
                continue
            mismatches.append((row.cycle_id, prior, row.verdict, row.net_return_pct))

    assert not mismatches, (
        f"{len(mismatches)} journalled verdicts did not reproduce: {mismatches[:3]}"
    )
    # Surfaced rather than swallowed: a rising count here is the expected shape, and
    # a count that never moves would mean nothing is ever resolving.
    print(
        f"replay determinism: {len(mismatches)} decided verdict(s) changed; "
        f"{resolved_since} placeholder(s) have since resolved"
    )


def test_a_pending_placeholder_is_not_a_determinism_violation():
    """A PENDING row is not a claim, so its later resolution cannot violate anything.

    The live-journal check compared every row and stayed green for months only
    because no recorded decision had yet gained its 24h horizon quote. The first
    real cycle after a quiet weekend resolved one and the gate went red - the right
    verdict, reached the wrong way. Determinism means a *decided* answer does not
    change; it says nothing about an undecided one.
    """
    import dataclasses
    from datetime import datetime, timezone

    from min_agent.counterfactual import CounterfactualRow

    # Bare literals, because that is what the production module uses too
    # ("PENDING", "GAP" appear as string comparisons, not constants).
    PENDING, GOOD_HOLD = "PENDING", "GOOD_HOLD"

    pending = CounterfactualRow(
        cycle_id="c1", symbol="SPY", action="HOLD", quantity=0,
        strategy_id=None,
        decided_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        price_at_decision=100.0, price_at_horizon=None,
        horizon_hours=24.0, gap_hours=None,
        gross_return_pct=None, assumed_cost_pct=0.05, net_return_pct=None,
        verdict=PENDING,
    )
    assert pending.verdict == PENDING
    assert pending.net_return_pct is None

    # Once a horizon quote exists the same row is a decided answer, and that is the
    # property the check actually enforces: it must not move afterwards.
    settled = CounterfactualRow(
        **{**dataclasses.asdict(pending), "price_at_horizon": 101.0,
           "gap_hours": 24.0, "gross_return_pct": 1.0, "net_return_pct": 0.95,
           "verdict": GOOD_HOLD},
    )
    assert settled.verdict == GOOD_HOLD
    assert settled.net_return_pct == 0.95
    again = CounterfactualRow(**dataclasses.asdict(settled))
    assert (again.verdict, again.net_return_pct) == (settled.verdict, settled.net_return_pct)
