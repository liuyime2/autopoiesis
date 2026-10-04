from datetime import datetime, timezone

from min_agent.guardian import Guardian
from min_agent.models import StrategyResult, StrategySpec
from min_agent.strategy_admission import StrategyAdmission
from min_agent.strategy_engine import StrategyLibrary


def make_spec(*, strategy_id="test-strat", symbols=("SPY",), max_position_value=500, kind="FIXED_SIZE", quantity=1, action="BUY", reference_price=100):
    params = {}
    if kind == "FIXED_SIZE":
        params = {"action": action, "quantity": quantity, "confidence": 0.8}
    if action == "HOLD":
        params["quantity"] = 0
    if kind == "TREND_FOLLOW":
        params = {"reference_price": reference_price, "threshold_pct": 0.02, "quantity": quantity, "confidence": 0.8}
    return StrategySpec(
        strategy_id=strategy_id,
        name="Test Strategy",
        kind=kind,
        symbols=symbols,
        parameters=params,
        max_position_value=max_position_value,
        enabled=True,
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        rationale="test",
    )


def admission_fixture(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    guardian = Guardian(allowlist={"SPY"}, max_position_value=1_000, max_daily_loss=500)
    return library, StrategyAdmission(guardian=guardian, strategy_library=library)


def admit_baseline(library, admission):
    result = admission.admit(make_spec(strategy_id="baseline", max_position_value=1, kind="HOLD_BASELINE"))
    assert result.accepted is True
    assert library.load("baseline").lifecycle == "BASELINE"


def test_strategy_admission_starts_with_hold_baseline(tmp_path):
    library, admission = admission_fixture(tmp_path)

    trading = admission.admit(make_spec(strategy_id="too-early"))
    baseline = admission.admit(make_spec(strategy_id="baseline", max_position_value=1, kind="HOLD_BASELINE"))

    assert trading.accepted is False
    assert "HOLD_BASELINE" in trading.reason
    assert baseline.accepted is True
    assert library.load("baseline").lifecycle == "BASELINE"


def test_strategy_admission_saves_guardian_approved_strategy_after_baseline(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)

    result = admission.admit(make_spec(strategy_id="approved"))

    assert result.accepted is True
    assert result.strategy_id == "approved"
    assert library.load("approved").strategy_id == "approved"
    assert library.load("approved").lifecycle == "PROBATION"


def test_strategy_admission_rejects_non_allowlisted_strategy_after_progression(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)

    result = admission.admit(make_spec(strategy_id="bad-symbol", symbols=("TSLA",)))

    assert result.accepted is False
    assert "allowlist" in result.reason
    assert [strategy.strategy_id for strategy in library.list()] == ["baseline"]


def test_strategy_admission_rejects_strategy_over_hard_limit_after_progression(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)

    result = admission.admit(make_spec(strategy_id="too-large", max_position_value=5_000))

    assert result.accepted is False
    assert "hard limit" in result.reason
    assert [strategy.strategy_id for strategy in library.list()] == ["baseline"]


def test_strategy_admission_rejects_duplicate_strategy_id(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)
    original = make_spec(strategy_id="duplicate", max_position_value=500)
    changed = make_spec(strategy_id="duplicate", max_position_value=900)

    first = admission.admit(original)
    second = admission.admit(changed)

    assert first.accepted is True
    assert second.accepted is False
    assert "already exists" in second.reason
    assert library.load("duplicate").max_position_value == 500


def test_strategy_admission_requires_tiny_fixed_size_first(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)

    trend = admission.admit(make_spec(strategy_id="trend", kind="TREND_FOLLOW"))
    large_fixed = admission.admit(make_spec(strategy_id="large-fixed", kind="FIXED_SIZE", quantity=2))
    tiny_fixed = admission.admit(make_spec(strategy_id="tiny-fixed", kind="FIXED_SIZE", quantity=1))

    assert trend.accepted is False
    assert "first trading skill" in trend.reason
    assert large_fixed.accepted is False
    assert "tiny exposure" in large_fixed.reason
    assert tiny_fixed.accepted is True
    assert library.load("tiny-fixed").lifecycle == "PROBATION"


def test_strategy_admission_allows_trend_after_fixed_size(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)
    fixed = admission.admit(make_spec(strategy_id="tiny-fixed", kind="FIXED_SIZE", quantity=1))

    trend = admission.admit(make_spec(strategy_id="trend", kind="TREND_FOLLOW", quantity=1))

    assert fixed.accepted is True
    assert trend.accepted is True
    assert library.load("trend").lifecycle == "PROBATION"


def test_strategy_admission_rejects_non_exploratory_first_fixed_size(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)

    result = admission.admit(make_spec(strategy_id="hold-fixed", kind="FIXED_SIZE", action="HOLD", quantity=0))

    assert result.accepted is False
    assert "tiny exposure" in result.reason


def test_strategy_admission_rejects_duplicate_trend_follow_params(tmp_path):
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)
    assert admission.admit(make_spec(strategy_id="tiny-fixed", kind="FIXED_SIZE", quantity=1)).accepted is True
    assert admission.admit(make_spec(strategy_id="trend-a", kind="TREND_FOLLOW", quantity=1, reference_price=100)).accepted is True

    duplicate = admission.admit(make_spec(strategy_id="trend-b", kind="TREND_FOLLOW", quantity=1, reference_price=100))
    distinct = admission.admit(make_spec(strategy_id="trend-c", kind="TREND_FOLLOW", quantity=1, reference_price=101))

    assert duplicate.accepted is False
    assert "duplicate TREND_FOLLOW" in duplicate.reason
    assert distinct.accepted is True


def _queued_candidate(library, strategy_id, *, lifecycle="PROBATION", quantity=1,
                      created_at="2026-06-10T12:00:00+00:00"):
    """Write a strategy straight into the library, as a previous admission would have.

    `quantity` varies so a newcomer under test is not refused as a behavioural twin
    first, which would mask the rule being tested.
    """
    library.save(
        make_spec(strategy_id=strategy_id, quantity=quantity).model_copy(
            update={
                "lifecycle": lifecycle,
                "created_at": datetime.fromisoformat(created_at),
            }
        )
    )


def _served(strategy_id, cumulative):
    return StrategyResult(
        strategy_id=strategy_id,
        cycles=3,
        cumulative_cycles=cumulative,
        submitted_orders=1,
        rejected_orders=0,
        errors=0,
        score=0.5,
        evaluated_at=datetime.now(tz=timezone.utc),
        trade_attempts=1,
    )


def test_a_candidate_is_refused_when_the_probation_queue_is_already_full(tmp_path):
    """The gate's other refusals are all candidate-intrinsic; this one is about capacity.

    716 market-open cycles could serve about 51 candidates and 68 were admitted, so the
    queue grew by 17 - and 7 were admitted across a weekend, when there is no market
    and therefore no capacity at all. Because `select` serves probation first and only
    considers the incumbent otherwise, a candidate that cannot be served is a claim on
    the cycles the one ACTIVE strategy needs.
    """
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)
    admission.max_probation_queue = 3
    for i in range(3):
        _queued_candidate(library, f"queued-{i}")
    results = [_served(f"queued-{i}", 0) for i in range(3)]

    result = admission.admit(make_spec(strategy_id="newcomer", quantity=7), results)

    assert result.accepted is False
    assert "probation" in result.reason
    assert "3 candidate(s)" in result.reason
    assert library.list() == sorted(library.list(), key=lambda s: s.strategy_id)
    assert not library.exists("newcomer")


def test_a_candidate_is_admitted_one_below_the_cap(tmp_path):
    """So the rule cannot pass by refusing everything."""
    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)
    admission.max_probation_queue = 3
    for i in range(2):
        _queued_candidate(library, f"queued-{i}")
    results = [_served(f"queued-{i}", 0) for i in range(2)]

    result = admission.admit(make_spec(strategy_id="newcomer", quantity=7), results)

    assert result.accepted is True


def test_a_candidate_that_has_served_its_budget_does_not_hold_the_queue(tmp_path):
    """The backlog is the population still owed service, not the count of PROBATION files.

    A candidate that has served `PROBATION_CYCLES` leaves the queue while its lifecycle
    still reads PROBATION, so counting files would pin the backlog at every candidate
    ever admitted and the gate could never reopen.
    """
    from min_agent.strategy_engine import PROBATION_CYCLES

    library, admission = admission_fixture(tmp_path)
    admit_baseline(library, admission)
    admission.max_probation_queue = 1
    _queued_candidate(library, "served-its-budget")
    _queued_candidate(library, "paused-not-queued", lifecycle="PAUSED")
    _queued_candidate(library, "retired-not-queued", lifecycle="RETIRED")

    served = [_served("served-its-budget", PROBATION_CYCLES)]

    assert admission.probation_backlog(served) == 0
    result = admission.admit(make_spec(strategy_id="newcomer", quantity=7), served)
    assert result.accepted is True


def test_a_fresh_library_refuses_nothing_for_capacity(tmp_path):
    _library, admission = admission_fixture(tmp_path)

    result = admission.admit(make_spec(strategy_id="first", max_position_value=1, kind="HOLD_BASELINE"))

    assert result.accepted is True
    assert "probation" not in result.reason
