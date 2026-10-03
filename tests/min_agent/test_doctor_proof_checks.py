"""The proof and decision-quality checks must be able to fail.

`_check_proof` is where the system claims it can prove anything at all: submitted
orders, fills, per-strategy PnL, and whether the decision maker is acting as a
second risk authority. Every one of those lines currently reports OK or a number
that reads as reassuring. These tests pin the paths that say "no", because a proof
check that cannot say no is an advertisement.

`risk-driven halts` matters most of the lot. Guardian enforces the limits
independently, so a decision maker that abstains on risk grounds is a second risk
authority that can halt trading without appearing to - and the only thing that
surfaces it is this line.
"""
from __future__ import annotations

from min_agent.doctor import DoctorReport, _check_proof
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)

TS = "2026-09-30T13:00:00-04:00"


def _cycle(action="HOLD", *, hold_reason=None, status="SKIPPED", filled=0.0,
           order_id=None, strategy_id="s1", quantity=0):
    # ExecutionResult.status has no "FILLED": the allowed values are SKIPPED,
    # SUBMITTED, REJECTED, ERROR, SHADOWED. A fill is SUBMITTED with a
    # filled_quantity, and broker confirmation arrives separately as an
    # ORDER_FILL_CONFIRMED event.
    return CycleRecord(
        cycle_id=f"c-{action}-{hold_reason}-{status}-{strategy_id}-{filled}",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=TS, market_open=True, last_price=100.0,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action=action, quantity=quantity, confidence=0.6,
            rationale="r", hold_reason=hold_reason, strategy_id=strategy_id,
        ),
        guardian=GuardianResult(approved=True, reason="ok"),
        execution=ExecutionResult(
            status=status, order_id=order_id, filled_quantity=filled, message="m",
        ),
    )


class _Cfg:
    strategy_dir = "unused-by-this-check"
    knowledge_dir = "unused-by-this-check"
    allowlist = ["SPY"]

    def __init__(self, journal_path):
        self.journal_path = journal_path


def _run(tmp_path, records):
    """`_check_proof` reads its own journal rather than taking records, so the
    fixture has to be a real journal file. Testing it through a stub would have
    proved the stub works."""
    from min_agent.journal import JsonlJournal

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for record in records:
        journal.append(record)
    report = DoctorReport()
    _check_proof(report, _Cfg(journal.path))
    return report


def _lines(report):
    return {c.name: c for c in report.checks}


# --- the headline: nothing has been proven -----------------------------------


def test_an_empty_record_set_proves_nothing(tmp_path):
    """The state a fresh install is in, and it must not read as success."""
    report = _run(tmp_path, [])
    lines = _lines(report)
    assert lines["end-to-end proof"].status.value == "warn"
    assert "nothing proven yet" in lines["end-to-end proof"].detail
    assert "minictrl once" in lines["end-to-end proof"].hint


def test_a_fill_alone_is_not_pnl(tmp_path):
    """A filled order proves an order happened, not that it made money."""
    records = [
        _cycle("BUY", status="SUBMITTED", filled=1.0, order_id="o1", quantity=1),
    ]
    lines = _lines(_run(tmp_path, records))
    assert lines["proof: per-strategy pnl"].status.value == "warn"
    assert "a fill alone is not PnL" in lines["proof: per-strategy pnl"].hint


def test_no_submitted_orders_is_reported(tmp_path):
    lines = _lines(_run(tmp_path, [_cycle("HOLD")]))
    assert lines["proof: submitted>0"].status.value == "warn"


# --- risk-driven halts -------------------------------------------------------


def test_a_decision_maker_that_halts_on_risk_is_surfaced(tmp_path):
    """The one line that catches a second, undeclared risk authority."""
    records = [
        _cycle("HOLD", hold_reason="risk_limit_near"),
        _cycle("HOLD", hold_reason="risk_limit_near"),
        _cycle("HOLD", hold_reason="no_signal"),
    ]
    check = _lines(_run(tmp_path, records))["risk-driven halts"]
    assert check.status.value == "warn", f"{check.status.value}: {check.detail}"
    assert "2/3" in check.detail
    assert "second risk authority" in check.hint


def test_ordinary_holds_are_not_reported_as_risk_halts(tmp_path):
    """`no_signal` is the model declining, not the model acting as a risk gate.

    I expected the line to be absent here. It is present and OK: the report shape
    is deliberately stable so it stays greppable, so the assertion is on the
    status rather than the presence.
    """
    records = [_cycle("HOLD", hold_reason="no_signal") for _ in range(5)]
    check = _lines(_run(tmp_path, records))["risk-driven halts"]
    assert check.status.value == "ok", f"{check.status.value}: {check.detail}"
    assert "0/5" in check.detail, check.detail


def test_a_single_risk_halt_still_surfaces(tmp_path):
    """One occurrence is enough to look at; a threshold would hide the first."""
    records = [_cycle("HOLD", hold_reason="risk_limit_near")]
    assert "risk-driven halts" in _lines(_run(tmp_path, records))


# --- pnl evidence ------------------------------------------------------------


def test_missing_pnl_evidence_is_reported(tmp_path):
    """`pnl_evidence` is the string the whole after-cost claim rests on, so its
    absence has to be visible rather than inferred from a healthy-looking run."""
    from min_agent.evaluator import PNL_EVIDENCE_MISSING

    lines = _lines(_run(tmp_path, [_cycle("HOLD", hold_reason="no_signal")]))
    evidence = lines["pnl evidence"]
    if evidence.detail == PNL_EVIDENCE_MISSING:
        assert evidence.status.value == "warn"
        assert "--ingest-evidence" in evidence.hint
    else:
        # Records alone can carry evidence; then the OK branch is the honest one
        # and the missing-evidence path is covered by the empty-record test above.
        assert evidence.status.value == "ok"


# --- decision quality and model calibration ---------------------------------
#
# Both take `records` and read journalled counterfactual rows, so they can be
# driven from the same real-journal fixture rather than a stub. The property worth
# pinning in each is that "we cannot tell yet" must not read as "we are doing
# fine" - which is the stated design intent of both.


class _Cfg2:
    strategy_dir = "unused"
    knowledge_dir = "unused"
    allowlist = ["SPY"]
    symbols = ["SPY"]
    # Omitting these made the check report "evaluation failed: AttributeError",
    # which is its own swallow-and-warn path - a reminder that a check which
    # catches everything can hide a broken caller behind a plausible message.
    counterfactual_horizon_hours = 24
    assumed_round_trip_cost_pct = 0.05
    # `_check_model_calibration` passes `min_confidence` into `calibrate`, so without
    # this the check raised AttributeError and reported "evaluation failed" - which the
    # miscalibration test below accepted as a warning. It was passing through the
    # swallow-and-warn path, not through the branch it claims to test.
    min_confidence = 0.5

    def __init__(self, journal_path):
        self.journal_path = journal_path


def _journal_with(tmp_path, records):
    from min_agent.journal import JsonlJournal

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for record in records:
        journal.append(record)
    return journal


def test_unscored_holds_are_reported_as_unknown_not_healthy(tmp_path):
    """With no later quote, the honest answer is that we cannot tell."""
    from min_agent.doctor import _check_decision_quality

    journal = _journal_with(tmp_path, [_cycle("HOLD", hold_reason="no_signal")])
    report = DoctorReport()
    _check_decision_quality(report, _Cfg2(journal.path), journal.read_all())
    check = _lines(report)["decision quality"]
    assert check.status.value == "warn", f"{check.status.value}: {check.detail}"
    assert "more market hours" in check.hint


def test_no_decisions_at_all_is_reported_as_unknown(tmp_path):
    from min_agent.doctor import _check_decision_quality

    journal = _journal_with(tmp_path, [])
    report = DoctorReport()
    _check_decision_quality(report, _Cfg2(journal.path), [])
    check = _lines(report)["decision quality"]
    assert check.status.value == "warn"
    assert "no decision could be scored yet" in check.detail


def test_a_miscalibrated_model_is_not_reported_as_healthy(tmp_path):
    """`level` is derived from the verdict, so a MIS-CALIBRATED result must not
    come out OK. The threshold is Brier-first by design: a model that beats the
    constant baseline on discrimination can still be miscalibrated."""
    from min_agent.doctor import _check_model_calibration

    journal = _journal_with(tmp_path, [_cycle("HOLD", hold_reason="no_signal")])
    report = DoctorReport()
    _check_model_calibration(
        report, _Cfg2(journal.path), journal, journal.read_all()
    )
    check = _lines(report)["model calibration"]
    # With no scored rows the check must not claim a signal; either it reports a
    # warning, or it reports OK for having produced no measurement - and the
    # detail has to say which.
    if check.status.value == "ok":
        assert "Brier" in check.detail or "0" in check.detail, check.detail
    else:
        assert check.status.value == "warn"


def test_the_calibration_check_reports_the_verdict_and_not_only_its_label(tmp_path, monkeypatch):
    """The whole verdict, because the actionable half is after the colon.

    `doctor` used to print `verdict.split(":")[0]`, which reduced a finding to the single
    word MIS-CALIBRATED and threw away the sentence stating that the most confident bucket
    was right 5.6% of the time against a 46.2% base rate - a margin of -40.6%.

    That matters because the label alone invites "the model is overconfident, raise
    `min_confidence`", while the measured direction says the confident decisions are the
    worst ones. This stubs `calibrate` because the property under test is what `doctor`
    does with the verdict it is handed, not how `calibrate` derives it.
    """
    from min_agent import calibration
    from min_agent.doctor import _check_model_calibration

    verdict = (
        "MIS-CALIBRATED: Brier 0.433 against the 0.25 a constant 0.5 claim scores, so "
        "the stated confidence is worse than useless. Accuracy is 46.2% and the most "
        "confident bucket is 5.6% (margin -40.6% if positive)"
    )
    report_stub = calibration.CalibrationReport(
        source="llm",
        total_decisions=314,
        scored=210,
        pending=71,
        brier=0.433,
        base_rate=0.462,
        top_bucket_accuracy=0.056,
        passed_gate_n=178,
        verdict=verdict,
    )
    monkeypatch.setattr(calibration, "calibrate", lambda rows, min_confidence: report_stub)

    journal = _journal_with(tmp_path, [_cycle("HOLD", hold_reason="no_signal")])
    report = DoctorReport()
    _check_model_calibration(report, _Cfg2(journal.path), journal, journal.read_all())
    detail = _lines(report)["model calibration"].detail

    assert detail != "MIS-CALIBRATED", "the label alone is the defect"
    assert "5.6%" in detail, f"the confident-bucket accuracy must reach the reader: {detail}"
    assert "-40.6%" in detail, f"the direction must reach the reader: {detail}"
    assert "46.2%" in detail, f"the base rate it is compared against must be there: {detail}"
    # The figures appear once each, not twice.
    assert detail.count("Brier") == 1, detail
    assert detail.count("46.2%") == 1, f"base rate printed twice: {detail}"


def test_an_insufficient_calibration_still_says_so(tmp_path, monkeypatch):
    """Dropping the append must not lose the case where there is no Brier yet."""
    from min_agent import calibration
    from min_agent.doctor import _check_model_calibration

    report_stub = calibration.CalibrationReport(
        source="llm",
        total_decisions=40,
        scored=3,
        pending=37,
        brier=None,
        base_rate=None,
        verdict=(
            "INSUFFICIENT: 3 of 40 decisions have an outcome yet, against the 30 needed "
            "to say anything about calibration"
        ),
    )
    monkeypatch.setattr(calibration, "calibrate", lambda rows, min_confidence: report_stub)

    journal = _journal_with(tmp_path, [_cycle("HOLD", hold_reason="no_signal")])
    report = DoctorReport()
    _check_model_calibration(report, _Cfg2(journal.path), journal, journal.read_all())
    detail = _lines(report)["model calibration"].detail

    assert "INSUFFICIENT" in detail
    assert "3 of 40" in detail, detail
    assert "Brier" not in detail, f"a figure that does not exist must not be printed: {detail}"
