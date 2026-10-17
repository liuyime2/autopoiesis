"""A retirement the evidence no longer supports must be reversible through the gate.

Measured 2026-10-13, the first open session after deploying source `9fb9066d820f27c9`.
`fixed-size-bil-sell-001` was retired at 09:39:38 EDT with reason
`severe operational failure rate 1.00`, charged against a single cycle: the 09:34:40 BIL
cycle, whose price quote was 1546 seconds old against the 900-second limit, which Guardian
refused with `'the price quote is 1611s stale, beyond the 900s limit'`. BIL is a bond ETF
whose prints run 20 to 34 minutes apart after an overnight close, so no strategy
specification could have changed the outcome. The refusal was correct and it destroyed a
working SELL strategy.

Recovery from that was impossible, by structure rather than policy. `admit` refused any id the
library already held without inspecting the lifecycle, and `_behavioural_duplicate` iterates
the whole library with no lifecycle filter, so a different id could not take the behavioural
slot either.

The decisive measurement is at 14:55:54 EDT, after the staleness fix was deployed: the
curriculum proposed `fixed-size-bil-sell-001` by name, rediscovering on its own that BIL
needed an exit, and the gate refused it with `strategy_id already exists; duplicate admission
rejected`. The self-repair was blocked by the component responsible for it.

This file pins the repaired behaviour and, just as importantly, the limits on it. A
re-admission path that restores anything the gate has ever retired is not a repair, it is a
retirement with extra steps.

Usage: pytest tests/autopoiesis/test_retired_strategy_readmission.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autopoiesis.guardian import Guardian
from autopoiesis.models import StrategySpec
from autopoiesis.strategy_admission import (
    MAX_READMISSIONS,
    NON_EXPIRING_RETIREMENT_REASONS,
    StrategyAdmission,
)
from autopoiesis.strategy_engine import StrategyLibrary

NOW = datetime(2026, 10, 13, 14, 55, 54, tzinfo=timezone.utc)


def _spec(
    strategy_id: str = "fixed-size-bil-sell-001",
    *,
    lifecycle: str = "RETIRED",
    lifecycle_reason: str = "severe operational failure rate 1.00",
    readmissions: int = 0,
    symbols: tuple[str, ...] = ("BIL",),
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        name="BIL sell",
        # TREND_FOLLOW, not FIXED_SIZE. Admission refuses a SELL carrying no market
        # condition, because one that cannot decline to fire sold 188 TLT shares and
        # emptied XLE and XLF on 2026-10-14. This fixture is about the *lifecycle*
        # re-admission path - that a retired id can come back and keeps its old reason -
        # so it uses a spec the gate would accept, and the refusal of the unconditional
        # shape is asserted separately below.
        kind="TREND_FOLLOW",
        symbols=symbols,
        # Anchored at 95.0 with a 1% band, i.e. [94.05, 95.95]. That is outside the band
        # of a BIL near 91.5 and of the AAPL at 100.0 one test supplies, so neither is
        # refused as permanently degenerate, and it stays inside the +/-50% deviation
        # tolerance of both.
        parameters={
            "reference_price": 95.0,
            "threshold_pct": 0.01,
            "quantity": 1, "confidence": 0.5,
        },
        max_position_value=25000.0,
        enabled=True,
        lifecycle=lifecycle,
        lifecycle_reason=lifecycle_reason,
        probation_restarts=1,
        readmissions=readmissions,
        created_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
        rationale="t",
    )


def _gate(library: StrategyLibrary, *, prices: dict[str, float] | None = None) -> StrategyAdmission:
    return StrategyAdmission(
        guardian=Guardian(
            allowlist=frozenset({"BIL", "SPY", "TLT"}),
            max_position_value=25000.0,
            max_daily_loss=500.0,
            max_total_exposure=90000.0,
            max_trades_per_day=100,
            min_confidence=0.5,
            max_snapshot_age_seconds=900,
        ),
        strategy_library=library,
        market_prices=prices or {"BIL": 91.505},
    )


def _with_baseline(library: StrategyLibrary, admission: StrategyAdmission) -> StrategyAdmission:
    """Put the library in the shape a real one is in: a baseline and an established skill.

    `admit` refuses a trading skill before any HOLD_BASELINE exists, and the *first*
    FIXED_SIZE must use tiny exposure. A live library has long passed both, and a fixture
    that did not would be testing the progression rule instead of re-admission.
    """
    assert admission.admit(StrategySpec(
        strategy_id="hold-everything", name="Baseline", kind="HOLD_BASELINE", symbols=("SPY",),
        parameters={}, max_position_value=1.0, created_at=NOW, rationale="t",
    )).accepted
    assert admission.admit(StrategySpec(
        strategy_id="tiny-probe-0001", name="First skill", kind="FIXED_SIZE", symbols=("SPY",),
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.5}, max_position_value=1000.0,
        created_at=NOW, rationale="t",
    )).accepted
    return admission


# --- the defect --------------------------------------------------------------


def test_a_retired_strategy_the_curriculum_reproposes_is_re_admitted(tmp_path):
    """The live case at 14:55:54 EDT, as the gate now answers it.

    Before the change this returned `strategy_id already exists; duplicate admission rejected`.
    The returned reason has to name the retirement it is undoing, because that reason is the
    only record of why the strategy ever left.
    """
    library = StrategyLibrary(tmp_path)
    library.save(_spec())
    assert library.load("fixed-size-bil-sell-001").lifecycle == "RETIRED"

    result = _with_baseline(library, _gate(library)).admit(_spec())

    assert result.accepted is True
    assert "re-admitted after retirement" in result.reason
    assert "severe operational failure rate 1.00" in result.reason

    restored = library.load("fixed-size-bil-sell-001")
    assert restored.lifecycle == "PROBATION"
    assert restored.readmissions == 1


def test_the_original_retirement_reason_survives_the_re_admission(tmp_path):
    """A later reader must be able to see both the mistake and its correction.

    Writing only the new verdict into `lifecycle_reason` would leave a RETIRED -> PROBATION
    transition in the journal whose cause has been overwritten out of the only field that
    keeps it.
    """
    library = StrategyLibrary(tmp_path)
    library.save(_spec())

    _with_baseline(library, _gate(library)).admit(_spec())
    restored = library.load("fixed-size-bil-sell-001")

    assert restored.lifecycle_reason.startswith("re-admitted after retirement:")
    assert "severe operational failure rate 1.00" in restored.lifecycle_reason


def test_a_re_admitted_strategy_starts_on_probation_and_earns_its_place_again(tmp_path):
    """It is not restored straight to ACTIVE.

    The strategy's recorded behaviour is unchanged and its evidence does not improve by being
    forgiven, so it goes back through the same probation the admission gate applies to a new
    candidate.
    """
    library = StrategyLibrary(tmp_path)
    library.save(_spec())

    _with_baseline(library, _gate(library)).admit(_spec())

    assert library.load("fixed-size-bil-sell-001").lifecycle == "PROBATION"


# --- the limits --------------------------------------------------------------


def test_a_retired_strategy_re_admitted_at_the_limit_is_refused(tmp_path):
    """One repair. A second means the gate and the proposal disagree, which is not repair.

    The limit is only reachable through a second *retirement*, which is the loop it exists to
    stop: once re-admitted the strategy is PROBATION and a further proposal for the same id is
    an ordinary duplicate again, refused by the branch that was always there.
    """
    library = StrategyLibrary(tmp_path)
    library.save(_spec())

    admission = _with_baseline(library, _gate(library))
    first = admission.admit(_spec())
    assert first.accepted is True
    assert library.load("fixed-size-bil-sell-001").readmissions == 1

    # The same proposal while the strategy is back in play is still just a duplicate.
    while_in_play = admission.admit(_spec())
    assert while_in_play.reason == "strategy_id already exists; duplicate admission rejected"

    # Retired a second time, the gate refuses to reverse itself again.
    library.save(library.load("fixed-size-bil-sell-001").model_copy(update={
        "lifecycle": "RETIRED",
        "lifecycle_reason": "severe operational failure rate 1.00 (second time)",
    }))
    second = admission.admit(_spec())

    assert second.accepted is False
    assert str(MAX_READMISSIONS) in second.reason
    assert "disagreement with the gate" in second.reason
    assert library.load("fixed-size-bil-sell-001").lifecycle == "RETIRED"
    assert library.load("fixed-size-bil-sell-001").readmissions == 1


@pytest.mark.parametrize("reason", NON_EXPIRING_RETIREMENT_REASONS)
def test_a_retirement_the_evidence_still_supports_is_refused(tmp_path, reason):
    """Money that has already been lost broker-confirmed does not become untrue.

    Allowing this would make re-admission a way to re-run a strategy the account has already
    been charged for losing on.
    """
    library = StrategyLibrary(tmp_path)
    library.save(_spec(lifecycle_reason=f"{reason} (-128.44)"))

    result = _gate(library).admit(_spec())

    assert result.accepted is False
    assert "still supports" in result.reason
    assert library.load("fixed-size-bil-sell-001").lifecycle == "RETIRED"


@pytest.mark.parametrize("lifecycle", ["PROBATION", "ACTIVE", "PAUSED", "BASELINE"])
def test_a_strategy_still_in_play_is_still_a_duplicate(tmp_path, lifecycle):
    """The repair is narrow on purpose: only RETIRED is reversible.

    Every other lifecycle still holds a slot the gate is protecting, so an id collision is a
    genuine duplicate and the original refusal stands unchanged.
    """
    library = StrategyLibrary(tmp_path)
    library.save(_spec(lifecycle=lifecycle, lifecycle_reason=""))

    result = _gate(library).admit(_spec(lifecycle=lifecycle, lifecycle_reason=""))

    assert result.accepted is False
    assert result.reason == "strategy_id already exists; duplicate admission rejected"
    assert library.load("fixed-size-bil-sell-001").lifecycle == lifecycle


def test_a_fresh_candidate_is_unaffected(tmp_path):
    """The ordinary path must not gain a field or a branch's side effects."""
    library = StrategyLibrary(tmp_path)

    result = _with_baseline(library, _gate(library)).admit(_spec(strategy_id="fixed-size-tlt-sell-009"))

    assert result.accepted is True
    assert result.reason == "approved"
    assert library.load("fixed-size-tlt-sell-009").readmissions == 0
    assert library.load("fixed-size-tlt-sell-009").lifecycle_reason == "approved"


def test_a_re_admission_still_goes_through_guardian_review(tmp_path):
    """Same standard as a new candidate, not a quieter door.

    `admit` falls through to the same capacity, reference-price, progression and
    `guardian.review_strategy` checks rather than short-circuiting them.
    """
    library = StrategyLibrary(tmp_path)
    library.save(_spec(symbols=("AAPL",)))

    result = _with_baseline(library, _gate(library, prices={"AAPL": 100.0})).admit(_spec(symbols=("AAPL",)))

    assert result.accepted is False
    assert "allowlist" in result.reason
    assert library.load("fixed-size-bil-sell-001").lifecycle == "RETIRED"


def test_a_retired_unconditional_sell_is_not_readmitted(tmp_path):
    """Re-admission is a way back from retirement, not a way around the gate.

    Measured 2026-10-14: `fixed-size-bil-sell-001` is the live shape this describes -
    FIXED_SIZE, action SELL, no market condition. Re-admitting it would put back the
    exact strategy that sold 188 TLT shares and emptied XLE and XLF, under a new name
    and with a clean lifecycle story. The readmission path exists because retirement can
    be wrong; it does not exist to route around a refusal that still applies.
    """
    unconditional = StrategySpec(
        strategy_id="fixed-size-bil-sell-002", name="BIL sell", kind="FIXED_SIZE",
        symbols=("BIL",), parameters={"action": "SELL", "quantity": 1, "confidence": 0.5},
        max_position_value=25000.0, enabled=True, lifecycle="RETIRED",
        lifecycle_reason="severe operational failure rate 1.00", probation_restarts=1,
        created_at=datetime(2026, 10, 4, tzinfo=timezone.utc), rationale="t",
    )
    library = StrategyLibrary(tmp_path)
    library.save(unconditional)

    result = _gate(library).admit(unconditional)

    assert result.accepted is False, "an unconditional sell must not come back"
    assert "no market condition" in result.reason, result.reason
    assert library.try_load("fixed-size-bil-sell-002").lifecycle == "RETIRED", (
        "a refusal must leave the retirement standing"
    )
