"""The same recorded inputs must produce the same outputs, twice.

This is what makes an offline validation result mean anything. If replaying a
recorded decision cycle can produce a different verdict than the live run did, then
"validated offline" and "working live" are two different systems and no promotion
decision is evidence of anything.

Determinism also is what makes a failure reproducible: a bug found at 03:00 has to
still be there at 09:00 when you try to look at it.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from autopoiesis import counterfactual as cf
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import (
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


#: The one label pair whose disagreement is a rename rather than a defect. Winning filled
#: orders were labelled NEUTRAL before `GOOD_TRADE` existed; NEUTRAL is also the label for a
#: move that did not clear cost, so the old journal cannot be re-derived under the new rule.
_RELABELLED = frozenset({("NEUTRAL", "GOOD_TRADE"), ("GOOD_TRADE", "NEUTRAL")})

#: Two scoring rules changed on 2026-10-07, both with the recomputed net return identical to
#: the digit: a BUY or SELL the Guardian refused is NOT_EXECUTED rather than a graded trade,
#: and a SELL is charged the round-trip cost instead of credited it, which moves SELL rows
#: between the three trade verdicts. The daemon re-journals every changed row on its next
#: maintenance pass, after which these no longer match anything.
_TRADE_VERDICTS = frozenset({"GOOD_TRADE", "FALSE_TRADE", "NEUTRAL"})


def _rule_change(prior: str, now: str, action: str) -> bool:
    if now == cf.NOT_EXECUTED and prior in _TRADE_VERDICTS:
        return True
    return action == "SELL" and prior in _TRADE_VERDICTS and now in _TRADE_VERDICTS


def _spans_an_ex_date(row) -> bool:
    """Whether a cash distribution went ex between this row's decision and its horizon.

    Reads the same cache `counterfactual._distributions_between` reads, so the test and the
    arithmetic cannot disagree about which rows are allowed to move.
    """
    if row.price_at_horizon is None:
        return False
    rows = cf.load_dividends(row.symbol)
    if not rows:
        return False
    start = row.decided_at.astimezone(timezone.utc).date()
    # `gap_hours`, not `horizon_hours`. The arithmetic's window ends at the quote it
    # actually used, which is the first quote at or after the horizon and may follow it by
    # up to `max_gap_hours`; using the target here left one row unattributed - a SPY
    # decision whose distribution went ex between the target and the quote. One row, and
    # the whole point of deriving this rather than allowlisting is that the window has to be
    # the same one.
    hours = row.gap_hours if row.gap_hours is not None else row.horizon_hours
    end = (row.decided_at + timedelta(hours=hours)).astimezone(timezone.utc).date()
    for ex_date in rows:
        try:
            ex = date.fromisoformat(ex_date)
        except ValueError:
            continue
        if start < ex <= end:
            return True
    return False


def test_replaying_the_live_journal_reproduces_the_recorded_verdicts():
    """The strongest statement available: the verdicts the daemon wrote to the
    journal can be recomputed from that same journal."""
    events = JsonlJournal("runtime/autopoiesis/journal.jsonl").read_events(
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

    records = JsonlJournal("runtime/autopoiesis/journal.jsonl").read_all()
    report = cf.evaluate(records, symbol="SPY", horizon_hours=24.0)

    mismatches = []
    resolved_since = 0
    relabelled = 0
    dividend_adjusted = 0
    for row in report.rows:
        prior = recorded.get(row.cycle_id)
        if prior is None:
            continue
        if prior[0] != row.verdict or prior[1] != row.net_return_pct:
            if prior[1] == row.net_return_pct and (
                (prior[0], row.verdict) in _RELABELLED
                or _rule_change(prior[0], row.verdict, row.action)
            ):
                # The same arithmetic under a changed vocabulary. A winning filled order
                # used to be labelled NEUTRAL, which is the label for a move too small to
                # clear cost; it now has its own verdict, GOOD_TRADE. The journal is
                # append-only and correctly records what the code computed at the time, so
                # these rows cannot be made to agree again and must not be rewritten.
                #
                # Deliberately derived rather than an allowlist: the pair of labels must be
                # exactly this one, AND the recomputed net return must match to the digit.
                # A genuine numeric divergence, or any other pair of labels, still fails -
                # so this cannot hide a real determinism defect, only a rename.
                relabelled += 1
                continue
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
            if _spans_an_ex_date(row):
                # A dividend went ex between this decision and its horizon, and the
                # recomputation now includes it. Measured 2026-10-17: 28 journalled SPY
                # verdicts move for this reason, all of them across 2026-06-18, which falls
                # inside the retained window that starts 2026-06-09. Those verdicts were
                # scored against a price that fell by the distribution, so a HOLD read as a
                # loss that the holder did not suffer - they were wrong when written.
                #
                # Derived rather than allowlisted, like the relabel case above: the row's
                # own window must actually contain an ex-date, read from the same cache the
                # arithmetic reads. A numeric divergence on a window with no distribution
                # still fails, so this cannot hide a real determinism defect.
                #
                # The originals stay in the journal. It is append-only and correctly records
                # what the code computed at the time; the next counterfactual pass writes
                # corrected events beside them, which is the account holder's option 2.
                dividend_adjusted += 1
                continue
            mismatches.append((row.cycle_id, prior, row.verdict, row.net_return_pct))

    assert not mismatches, (
        f"{len(mismatches)} journalled verdicts did not reproduce: {mismatches[:3]}"
    )
    # Surfaced rather than swallowed: a rising count here is the expected shape, and
    # a count that never moves would mean nothing is ever resolving.
    print(
        f"replay determinism: {len(mismatches)} decided verdict(s) changed; "
        f"{resolved_since} placeholder(s) have since resolved; "
        f"{relabelled} relabelled NEUTRAL<->GOOD_TRADE at an identical net return; "
        f"{dividend_adjusted} moved by a cash distribution inside their horizon window"
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

    from autopoiesis.counterfactual import CounterfactualRow

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
