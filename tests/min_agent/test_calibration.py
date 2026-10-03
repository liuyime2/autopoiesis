"""Does the model's confidence mean anything?

The system records a `confidence` on every decision and uses it as a gate -
`min_confidence` decides whether an order may be sent - while never once testing
whether it predicts anything. So a model that is confidently wrong is
indistinguishable from one that is right, and the threshold is untested.

The tests below pin the scoring rules and, more importantly, pin the refusals. On the
live journal all 67 LLM decisions are PENDING, so the module's job today is to say
"unmeasured" clearly rather than to produce a number.
"""

from datetime import datetime, timezone

import pytest

from min_agent import calibration
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)

TS = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _record(cycle_id, confidence, action="HOLD", source="llm", price=100.0):
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=TS, market_open=True, last_price=price,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action=action, quantity=0, confidence=confidence,
            rationale="r", decision_source=source,
        ),
        guardian=GuardianResult(approved=True, reason="ok"),
        execution=ExecutionResult(
            status="SKIPPED", order_id=None, filled_quantity=0, message="hold",
        ),
    )


def _rows(specs):
    """specs: (cycle_id, confidence, verdict) -> calibration rows."""
    return [
        calibration.CalibrationRow(
            cycle_id=cid, confidence=conf, action="HOLD", verdict=v,
            decision_source="llm",
        )
        for cid, conf, v in specs
    ]


# --------------------------------------------------------------------------
# the refusal, which is the important behaviour today
# --------------------------------------------------------------------------

def test_too_few_scored_decisions_produce_no_accuracy_number():
    report = calibration.calibrate(_rows([
        ("c1", 0.9, calibration.GOOD_HOLD),
        ("c2", 0.9, calibration.MISSED_ALPHA),
    ]))

    assert report.verdict.startswith("INSUFFICIENT")
    assert report.brier is None, "a Brier score on two points is a decoration"
    assert report.base_rate is None
    assert report.buckets == []


def test_pending_decisions_are_reported_as_unmeasured_not_as_wrong():
    """The live state: 67 LLM decisions, none with an outcome yet. The distinction
    between 'the model is bad' and 'we do not know yet' is the whole point."""
    report = calibration.calibrate(_rows([
        (f"c{i}", 0.7, "PENDING") for i in range(67)
    ]))

    assert report.total_decisions == 67
    assert report.pending == 67
    assert report.scored == 0
    assert report.verdict.startswith("INSUFFICIENT")
    assert any("unmeasured" in n for n in report.notes)


def test_no_decisions_at_all_says_so_rather_than_scoring_zero():
    report = calibration.calibrate([])
    assert report.verdict.startswith("NO_DATA")
    assert report.brier is None


# --------------------------------------------------------------------------
# scoring rules
# --------------------------------------------------------------------------

def test_a_good_hold_and_a_winning_trade_both_count_as_correct():
    assert _rows([("a", 0.8, calibration.GOOD_HOLD)])[0].correct is True
    assert _rows([("a", 0.8, calibration.GOOD_TRADE)])[0].correct is True
    assert _rows([("a", 0.8, calibration.MISSED_ALPHA)])[0].correct is False
    assert _rows([("a", 0.8, calibration.FALSE_TRADE)])[0].correct is False


def test_a_missed_rally_is_not_scored_as_correct_caution():
    """Otherwise a model that never trades gets a perfect curve for free, which is
    the easiest way to manufacture good calibration."""
    rows = _rows([(f"c{i}", 0.8, calibration.MISSED_ALPHA) for i in range(40)])
    report = calibration.calibrate(rows)

    assert report.correct == 0
    assert report.base_rate == 0.0


def test_neutral_is_excluded_rather_than_counted_as_either():
    rows = _rows(
        [(f"n{i}", 0.8, calibration.NEUTRAL) for i in range(20)]
        + [(f"g{i}", 0.8, calibration.GOOD_HOLD) for i in range(40)]
    )
    report = calibration.calibrate(rows)

    assert report.neutral == 20
    assert report.scored == 40, "neutral moves say nothing and must not enter the score"


def test_pending_and_gap_are_counted_separately_from_neutral():
    rows = _rows([
        ("p", 0.8, "PENDING"), ("g", 0.8, "GAP"), ("u", 0.8, "UNSCORED"),
        ("n", 0.8, calibration.NEUTRAL),
    ] + [(f"k{i}", 0.8, calibration.GOOD_HOLD) for i in range(40)])
    report = calibration.calibrate(rows)

    assert report.pending == 3, "PENDING, GAP and never-scored are all 'no outcome yet'"
    assert report.neutral == 1


# --------------------------------------------------------------------------
# the metric that decides the question
# --------------------------------------------------------------------------

def _calibrated(confidences_and_correct, min_confidence=0.5):
    rows = [
        calibration.CalibrationRow(
            cycle_id=f"c{i}", confidence=c, action="HOLD",
            verdict=calibration.GOOD_HOLD if ok else calibration.MISSED_ALPHA,
            decision_source="llm",
        )
        for i, (c, ok) in enumerate(confidences_and_correct)
    ]
    return calibration.calibrate(rows, min_confidence=min_confidence)


def test_confidence_that_does_not_separate_good_from_bad_reports_no_signal():
    """The model states 0.9 and 0.6 and is right equally often. The threshold is
    then a coin flip wearing a number."""
    specs = [(0.9, i % 2 == 0) for i in range(20)] + \
            [(0.6, i % 2 == 0) for i in range(20)]
    report = _calibrated(specs)

    # Claiming 0.9 and 0.6 while being right half the time is BOTH true at once:
    # confidence does not separate good from bad, and it is worse than a constant
    # 0.5 claim. The Brier is checked first because it is the stronger and more
    # actionable statement. This test previously asserted NO_SIGNAL, which was true
    # but incomplete - it ignored that the confidences were also lying about the
    # level, not just the ordering.
    assert report.verdict.startswith("MIS-CALIBRATED")
    assert report.top_bucket_accuracy == pytest.approx(report.base_rate)
    assert report.brier > calibration.BRIER_UNINFORMATIVE


def test_confidence_that_actually_separates_reports_a_signal():
    specs = [(0.9, True) for _ in range(20)] + \
            [(0.6, False) for _ in range(20)]
    report = _calibrated(specs)

    assert report.verdict.startswith("SIGNAL")
    assert report.top_bucket_accuracy == pytest.approx(1.0)
    assert report.base_rate == pytest.approx(0.5)
    assert "hypothesis, not a result" in report.verdict


def test_the_top_bucket_is_what_is_compared_not_the_whole_gate():
    """min_confidence is 0.5, so a 0.6 and a 0.9 both clear the gate and gate
    accuracy ends up measuring almost every decision. The first version compared
    that and could not tell a model that separates good from bad from one that does
    not, so the top bucket is the comparison."""
    specs = [(0.9, True) for _ in range(20)] + \
            [(0.6, False) for _ in range(20)]
    report = _calibrated(specs)

    assert report.passed_gate_n == 40, "both buckets clear a 0.5 gate"
    assert report.passed_gate_accuracy == pytest.approx(0.5)
    assert report.top_bucket["range"] == "0.8-1.0"
    assert report.top_bucket_accuracy == pytest.approx(1.0)


def test_too_few_gate_clearing_decisions_refuses_rather_than_scoring_them():
    """Twenty confident decisions and one hundred unsure ones is not enough to say
    the confident ones are better."""
    specs = [(0.9, True) for _ in range(8)] + \
            [(0.1, False) for _ in range(100)]
    report = _calibrated(specs)

    assert report.verdict.startswith("INSUFFICIENT_AT_GATE")
    assert report.passed_gate_n == 8


def test_brier_score_penalises_confidence_that_is_merely_decorative():
    """Stating 1.0 and being right half the time must score worse than stating 0.5
    and being right half the time."""
    overconfident = _calibrated([(1.0, i % 2 == 0) for i in range(40)])
    honest = _calibrated([(0.5, i % 2 == 0) for i in range(40)])

    assert overconfident.brier == pytest.approx(0.5)
    assert honest.brier == pytest.approx(0.25)
    assert honest.brier < overconfident.brier


def test_a_perfectly_calibrated_model_scores_near_zero_brier():
    report = _calibrated([(1.0, True) for _ in range(20)] +
                         [(0.0, False) for _ in range(20)])
    assert report.brier == pytest.approx(0.0)


def test_buckets_span_the_whole_range_and_do_not_leave_a_one_member_stratum():
    """0.95 and 1.0 belong in the same bucket. Splitting them produced a bucket with
    one member that read as a 100% accurate stratum."""
    rows = _rows([(f"c{i}", 1.0, calibration.GOOD_HOLD) for i in range(40)])
    report = calibration.calibrate(rows)

    populated = [b for b in report.buckets if b.n > 0]
    assert len(populated) == 1, "all at 1.0 should land in one bucket"
    assert populated[0].high == pytest.approx(1.0)


# --------------------------------------------------------------------------
# joining the real journal
# --------------------------------------------------------------------------

def test_rows_are_built_by_pairing_decisions_with_their_scored_outcome():
    records = [
        _record("c1", 0.9),
        _record("c2", 0.3, source="fallback_policy_engine"),
    ]
    rows = calibration.build_rows(
        records, {"c1": calibration.GOOD_HOLD, "c2": calibration.GOOD_HOLD},
        source="llm",
    )

    assert [r.cycle_id for r in rows] == ["c1"], (
        "a decision from another source must not be scored as the model's"
    )
    assert rows[0].verdict == calibration.GOOD_HOLD


def test_a_decision_with_no_verdict_at_all_is_marked_unscored():
    records = [_record("c1", 0.9)]
    rows = calibration.build_rows(records, {}, source="llm")
    assert rows[0].verdict == "UNSCORED"
    assert rows[0].informative is False


# --------------------------------------------------------------------------
# the first real verdict, and the two ways it could have lied
# --------------------------------------------------------------------------

def test_a_brier_worse_than_a_constant_claim_is_reported_as_mis_calibrated():
    """Right almost always, and still badly calibrated.

    The first non-INSUFFICIENT verdict this system ever produced was "SIGNAL": the
    most confident bucket was 97.1% against a 96.8% base rate, and the test passed
    because the bucket was higher. The Brier was 0.589 - 2.4x worse than always
    claiming 0.5 - which says the stated confidence is worse than useless. Accuracy
    said nothing because the base rate was that high; the Brier said everything.
    """
    rows = [
        calibration.CalibrationRow(
            cycle_id=f"c{i}", confidence=0.5, action="HOLD",
            verdict=calibration.GOOD_HOLD, decision_source="llm",
        )
        for i in range(100)
    ]
    rows.append(calibration.CalibrationRow(
        cycle_id="bad", confidence=0.9, action="HOLD",
        verdict=calibration.MISSED_ALPHA, decision_source="llm",
    ))
    report = calibration.calibrate(rows)

    assert report.base_rate > 0.95
    assert report.brier > calibration.BRIER_UNINFORMATIVE
    assert report.verdict.startswith("MIS-CALIBRATED")
    assert "worse than useless" in report.verdict


def test_a_margin_below_noise_is_not_a_signal():
    """0.3 points on a 96.8% base rate is rounding, not evidence."""
    # Well calibrated - confidence tracks the 97% accuracy - so the Brier is fine
    # and the margin is the only thing left to judge. An earlier fixture used
    # confidence 0.5 for a 97%-accurate model, which is genuinely mis-calibrated and
    # correctly tripped that branch first, so it never reached this one.
    rows = [
        calibration.CalibrationRow(
            cycle_id=f"c{i}", confidence=0.97, action="HOLD",
            verdict=calibration.GOOD_HOLD, decision_source="llm",
        )
        for i in range(97)
    ]
    rows += [
        calibration.CalibrationRow(
            cycle_id=f"m{i}", confidence=0.03, action="HOLD",
            verdict=calibration.MISSED_ALPHA, decision_source="llm",
        )
        for i in range(3)
    ]
    report = calibration.calibrate(rows)

    assert report.brier < calibration.BRIER_UNINFORMATIVE
    margin = report.top_bucket_accuracy - report.base_rate
    assert 0 < margin < calibration.MIN_MATERIAL_MARGIN
    assert report.verdict.startswith("NO_SIGNAL")
    assert "distinguishable from noise" in report.verdict


def test_a_genuinely_separating_model_still_reports_a_signal():
    """Tightening must not make a real signal unreachable, or the fix is just a
    refusal with a better vocabulary."""
    specs = [(0.9, True) for _ in range(20)] + [(0.1, False) for _ in range(20)]
    report = _calibrated(specs)

    assert report.verdict.startswith("SIGNAL")
    assert report.brier < calibration.BRIER_UNINFORMATIVE


def test_a_winning_trade_is_scored_not_discarded():
    """Regression: `calibration` dropped GOOD_TRADE from INFORMATIVE entirely.

    Measured on the live journal: 49 of 607 scored decisions were winning trades, and
    this module excluded all of them. Because `correct` also required GOOD_HOLD, they
    then counted as wrong. The visible symptom was a "most confident bucket is 5.6%
    accurate" reading - the model is most confident when it trades, so refusing to
    credit a trade scored the confident bucket as the inaccurate one, and the Brier
    score and MIS-CALIBRATED verdict described this function rather than the model.
    """
    # Above MIN_SCORED_DECISIONS, below which the report deliberately prints no
    # accuracy figure at all.
    rows = _rows([(f"t{i}", 0.95, calibration.GOOD_TRADE) for i in range(40)])

    report = calibration.calibrate(rows)

    assert all(row.informative for row in rows), "winning trades must count as evidence"
    assert all(row.correct for row in rows)
    assert report.scored == 40
    assert report.correct == 40
    assert report.base_rate == 1.0
    assert report.brier is not None and report.brier < calibration.BRIER_UNINFORMATIVE


def test_the_three_verdict_sets_cannot_drift_apart_again():
    """They were hand-declared in three modules and one copy had already drifted.

    `counterfactual` produces the verdicts so it owns them; `calibration` and
    `offline_validation` import them. A verdict added in one place and forgotten in
    another is exactly how 49 winning trades were scored incorrect for the life of this
    project, so the identity is asserted rather than trusted.
    """
    from min_agent import counterfactual, offline_validation

    assert calibration.INFORMATIVE is counterfactual.INFORMATIVE
    assert offline_validation.INFORMATIVE is counterfactual.INFORMATIVE
    assert calibration.CORRECT is counterfactual.CORRECT
    for name in ("GOOD_HOLD", "MISSED_ALPHA", "FALSE_TRADE", "GOOD_TRADE", "NEUTRAL"):
        assert getattr(calibration, name) is getattr(counterfactual, name)
        assert getattr(offline_validation, name) is getattr(counterfactual, name)
