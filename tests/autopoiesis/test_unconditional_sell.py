"""A sell the library cannot make conditional must not reach the library.

Measured 2026-10-14 on the live account. The curriculum read "this position has no
strategy that can sell it" as a gap, and `FIXED_SIZE` - whose entire parameter set is
`action`, `quantity`, `confidence` - was the kind it reached for. The result was not a
sell that rarely fires but a sell that cannot not fire: broker-verified, TLT 188 shares
sold, XLE and XLF emptied, one order every ~32 minutes on 2026-10-14 alone.

The gap was real; `trend-follow-sell-001` shows the library expresses a conditional
sell. What was wrong was filling a real gap with an unconditional spec.

All fixtures are synthetic. Usage: pytest tests/autopoiesis/test_unconditional_sell.py -q
"""

from __future__ import annotations

import pytest

from autopoiesis.guardian import Guardian
from autopoiesis.models import StrategySpec
from autopoiesis.strategy_admission import (
    MARKET_TRIGGER_FIELDS,
    StrategyAdmission,
    _unconditional_sell_reason,
)
from autopoiesis.strategy_engine import StrategyLibrary

SPY_LAST = 780.0


def _guardian() -> Guardian:
    return Guardian(
        max_position_value=25_000.0,
        max_daily_loss=500.0,
        max_trades_per_day=100,
        allowlist=frozenset({"SPY", "TLT", "XLE", "XLF", "BIL", "XLB"}),
    )


def _admission(tmp_path) -> StrategyAdmission:
    return StrategyAdmission(guardian=_guardian(), strategy_library=StrategyLibrary(tmp_path))


def _past_first_phase(tmp_path) -> StrategyAdmission:
    """A library that has cleared both curriculum progression phases.

    Admission refuses any trading skill before a HOLD_BASELINE exists, and then requires
    the first one to be a tiny FIXED_SIZE. Both rules are correct and unrelated to this
    check; without seeding them, a test of whether a *conditional* sell survives would
    be measuring the progression rule instead.
    """
    admission = _admission(tmp_path)
    baseline = admission.admit(_spec("HOLD_BASELINE", {}, strategy_id="hold-baseline-001"))
    assert baseline.accepted is True, baseline.reason
    first = admission.admit(
        _spec(
            "FIXED_SIZE", {"action": "BUY", "quantity": 1, "confidence": 0.7},
            strategy_id="fixed-size-buy-000",
        )
    )
    assert first.accepted is True, first.reason
    return admission


def _spec(kind: str, parameters: dict, strategy_id: str = "probe-001") -> StrategySpec:
    return StrategySpec.model_validate(
        {
            "strategy_id": strategy_id,
            "name": strategy_id,
            "symbols": ["TLT"],
            "created_at": "2026-10-14T12:00:00Z",
            "rationale": "synthetic fixture",
            "enabled": True,
            "kind": kind,
            "max_position_value": 25_000.0,
            "parameters": parameters,
        }
    )


def _unconditional_probe() -> StrategySpec:
    """The exact shape that drained the account, byte for byte in behaviour."""
    return _spec("FIXED_SIZE", {"action": "SELL", "quantity": 1, "confidence": 0.5})


# --- the shape that caused the loss is refused -------------------------------


def test_an_unconditional_sell_is_refused_by_the_gate(tmp_path):
    admission = _admission(tmp_path)
    result = admission.admit(_unconditional_probe())

    assert result.accepted is False, "this is the spec that sold 188 TLT shares"
    assert "no market condition" in result.reason
    assert "until the position is empty" in result.reason, (
        "the refusal has to say what the consequence would have been: " + result.reason
    )


def test_the_refusal_names_a_way_out(tmp_path):
    """A refusal the model cannot act on is a dead end. It must say what to propose."""
    result = _admission(tmp_path).admit(_unconditional_probe())

    assert "TREND_FOLLOW" in result.reason and "RULE" in result.reason, result.reason


def test_a_refused_probe_is_not_written_to_the_library(tmp_path):
    admission = _admission(tmp_path)
    admission.admit(_unconditional_probe())

    assert StrategyLibrary(tmp_path).try_load("probe-001") is None, (
        "a refusal must leave nothing behind"
    )


# --- conditional sells are unaffected ----------------------------------------


def test_a_trend_follow_sell_is_still_admitted(tmp_path):
    """`trend-follow-sell-001` is the shape the library should have produced instead."""
    spec = _spec(
        "TREND_FOLLOW",
        {"reference_price": SPY_LAST, "threshold_pct": 0.01, "quantity": 1, "confidence": 0.5},
        strategy_id="trend-follow-sell-002",
    )
    assert _past_first_phase(tmp_path).admit(spec).accepted is True


def test_a_rule_sell_is_still_admitted(tmp_path):
    spec = _spec(
        "RULE",
        {
            "signal": "return_over_n", "lookback": 5, "threshold_pct": 0.02,
            "when_above": "BUY", "when_below": "SELL", "quantity": 1, "confidence": 0.6,
        },
        strategy_id="rule-sell-001",
    )
    assert _past_first_phase(tmp_path).admit(spec).accepted is True


def test_an_unconditional_buy_is_still_admitted(tmp_path):
    """A buy cannot drain a position that was never opened.

    Guardian caps notional and daily loss, and the position cap is what stopped the
    SPY buys at 24,929 of 25,000. Refusing unconditional buys would remove the system's
    only way to build a position, to fix a defect that does not exist here.
    """
    spec = _spec(
        "FIXED_SIZE", {"action": "BUY", "quantity": 1, "confidence": 0.7},
        strategy_id="fixed-size-buy-001",
    )
    admission = _past_first_phase(tmp_path)
    result = admission.admit(spec)
    assert "no market condition" not in result.reason, (
        f"an unconditional buy is not what this rule refuses, but it was: {result.reason}"
    )


def test_a_hold_baseline_is_unaffected(tmp_path):
    result = _admission(tmp_path).admit(
        _spec("HOLD_BASELINE", {}, strategy_id="hold-baseline-001")
    )
    assert result.accepted is True


# --- the derivation cannot drift away from the schema ------------------------


def test_the_trigger_set_is_derived_and_excludes_the_non_market_fields():
    """Derived from `STRATEGY_PARAMETER_SCHEMAS` so a new kind is recognised.

    Written after measuring the derivation itself: an earlier version subtracted only
    three fields and `action` survived, which made every `FIXED_SIZE` look conditional
    and would have silently disabled this entire check.
    """
    from autopoiesis.models import STRATEGY_PARAMETER_SCHEMAS

    allowed: set[str] = set()
    for schema in STRATEGY_PARAMETER_SCHEMAS.values():
        allowed.update(schema.get("allowed", ()))

    assert allowed >= MARKET_TRIGGER_FIELDS, "a trigger field must exist in some schema"
    for excluded in ("action", "quantity", "confidence", "reference_price"):
        assert excluded not in MARKET_TRIGGER_FIELDS, (
            f"{excluded} is not derived from market state and would make a probe look "
            f"conditional: {sorted(MARKET_TRIGGER_FIELDS)}"
        )


def test_fixed_size_cannot_express_a_trigger_so_no_fixed_size_sell_can_ever_pass():
    """The structural statement behind the rule, asserted directly."""
    from autopoiesis.models import STRATEGY_PARAMETER_SCHEMAS

    fixed = set(STRATEGY_PARAMETER_SCHEMAS["FIXED_SIZE"]["allowed"])
    assert not fixed & MARKET_TRIGGER_FIELDS, (
        "if FIXED_SIZE ever gains a trigger, this whole rule stops applying to it and "
        "the check needs re-deriving"
    )
    for quantity in (1, 2, 100):
        assert _unconditional_sell_reason(
            _spec("FIXED_SIZE", {"action": "SELL", "quantity": quantity, "confidence": 0.1})
        ) is not None


@pytest.mark.parametrize(
    ("action", "quantity"), [("HOLD", 0), ("BUY", 1), ("buy", 1), ("sell", 1)]
)
def test_the_check_only_claims_sell(action, quantity):
    """Case-insensitive on the action, and silent on everything that is not a sell.

    `HOLD` carries zero quantity because the schema requires it, which is itself the
    clearest statement of the asymmetry: the schema will not let an unconditional HOLD
    hold a size, and it will happily let an unconditional SELL sell one.
    """
    reason = _unconditional_sell_reason(
        _spec("FIXED_SIZE", {"action": action, "quantity": quantity, "confidence": 0.5})
    )
    assert (reason is None) == (action.upper() != "SELL"), action


def test_the_schema_already_forbids_a_sell_with_no_quantity():
    """The check is not the only thing standing between a probe and a position.

    `FIXED_SIZE` refuses to validate without `action`, `confidence` and `quantity`, so
    there is no way to construct a SELL that carries nothing at all. What the schema
    does permit is a SELL that carries a quantity and nothing that could stop it, which
    is precisely the shape the gate now refuses.
    """
    with pytest.raises(Exception):
        _spec("FIXED_SIZE", {"action": "SELL", "confidence": 0.5})

# --- the curriculum must not ask for the spec the gate now refuses ------------


def test_the_prompt_demands_a_conditional_exit_not_just_a_sell():
    """`required_action` was added because the model would not propose a sell at all.

    Measured 2026-10-14: it demanded SELL and got `FIXED_SIZE SELL 1`, which drained the
    account. Removing the demand would restore the bug the field was added to fix, so the
    demand stays and gains the constraint that makes it satisfiable.
    """
    from autopoiesis.curriculum import StructuredCurriculumAgent

    captured: dict = {}

    def transport(context):
        captured["context"] = context
        return (
            '{"task_id":"t-1","task_type":"EVALUATE","summary":"s",'
            '"strategy_spec":null,"parameters":{},"rationale":"r"}'
        )

    agent = StructuredCurriculumAgent(transport=transport)
    agent.propose(
        reflection=_reflection(), current_strategies=[],
        recent_exploration_summary={"window_cycles": 50},
        guardian_max_position_value=25_000.0,
        open_positions=[{"symbol": "TLT", "quantity": 210}],
        last_price=SPY_LAST, symbol="SPY",
    )

    context = captured["context"]
    policy = " ".join(context["exploration_policy"])
    assert "decline to fire" in policy, policy
    assert "TREND_FOLLOW" in policy and "RULE" in policy, policy


def _reflection():
    from autopoiesis.models import ReflectionRecord

    return ReflectionRecord.model_validate(
        {
            "summary": "50 cycles", "window_cycles": 50, "submitted_orders": 10,
            "rejected_orders": 9, "skipped_orders": 31, "error_count": 0,
            "evaluation": {"pnl_evidence": "broker_strategy_closed_lot_pnl_verified"},
            "generated_at": "2026-10-14T12:00:00Z", "cycle_ids": [],
        }
    )
