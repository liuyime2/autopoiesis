"""The daily validation report's summary of one New York trading day."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import daily_report


def _cycle(hour_utc, action="HOLD", rule=None, reason=None, status="SKIPPED", source="llm",
           day=7, guardian="approved", rationale="", intervention=None):
    return SimpleNamespace(
        snapshot=SimpleNamespace(timestamp=datetime(2026, 10, day, hour_utc, tzinfo=timezone.utc), symbol="SPY"),
        decision=SimpleNamespace(action=action, rule_action=rule, override_reason=reason,
                                 decision_source=source, rationale=rationale),
        execution=SimpleNamespace(status=status),
        guardian=SimpleNamespace(reason=guardian),
        error=None,
        intervention=intervention,
    )


def test_an_intervention_is_not_counted_as_the_model_choosing_to_hold():
    """A refused addition and a chosen hold look identical in `decision` alone.

    Measured 2026-10-20: 33 cycles carried a HOLD whose rationale was the accumulation gate's, and
    nothing in the record said the model had proposed a BUY. The report must count those separately,
    or its decision table is a table of the system's output attributed to the model.
    """
    records = [
        _cycle(13, day=7),
        _cycle(14, day=7, intervention=SimpleNamespace(
            original_action="BUY", reason="the strategy has not passed the screen")),
    ]

    summary = daily_report.day_summary(records, date(2026, 10, 7))

    assert len(summary["interventions"]) == 1
    text = daily_report.render(date(2026, 10, 7), summary, [], "unproven")
    assert "intervened   1 decision" in text
    assert "BUY -> HOLD" in text


def test_a_day_is_the_new_york_session_and_overrides_carry_their_reason():
    records = [
        _cycle(13, day=7),                        # 09:00 New York, Oct 7
        _cycle(14, rule="BUY", reason="no room"),  # 10:00 an override
        _cycle(19, action="SHORT", rule="SHORT", status="SHADOWED",
               guardian="the account holds 6 SPY long"),
        _cycle(2, day=8),                         # 22:00 New York on Oct 7
        _cycle(14, day=8),                        # the next day
    ]
    summary = daily_report.day_summary(records, date(2026, 10, 7))
    assert summary["cycles"] == 4
    assert (summary["first"], summary["last"]) == ("09:00", "22:00")
    assert summary["paired"] == 2
    assert summary["overrides"] == [("10:00", "BUY", "HOLD", "no room")]
    assert summary["shorts"] == [("15:00", "SHORT", "the account holds 6 SPY long", "SHADOWED")]


def test_a_day_with_no_cycles_says_so():
    summary = daily_report.day_summary([], date(2026, 10, 8))
    text = daily_report.render(date(2026, 10, 8), summary, [], "NOT MEASURABLE")
    assert "NO CYCLES today" in text




def test_decisions_that_cite_the_risk_block_are_counted_and_the_trades_among_them():
    records = [
        _cycle(14, action="BUY", rationale="risk is ACTIVE with vol_scaled_fraction 0.65 supporting a small add"),
        _cycle(15, action="HOLD", rationale="vol_scaled_fraction is 1.0 so no size reduction is warranted"),
        _cycle(16, action="BUY", rationale="the rule says BUY and the position cap leaves room"),
    ]
    summary = daily_report.day_summary(records, date(2026, 10, 7))
    assert (summary["risk_cited"], summary["risk_cited_trades"]) == (2, 1)
    assert "cite it in their reasons" in daily_report.render(date(2026, 10, 7), summary, [], "x")


def test_a_system_abstention_is_not_counted_as_the_model_overruling_the_rule():
    """Measured 2026-10-21: 62 departures, 26 of them with no reason because the system made them.

    The accumulation gate writes `selection_unavailable: ... has not passed the screen` and the
    in-flight guard writes `skip: an identical order ...`. Counting those as the model's departures
    charged the system's refusals to the model - the same collapse the record fixed and the doctor
    now excludes.
    """
    records = [
        _cycle(13, day=7, action="HOLD", rule="BUY", reason="volatility too high"),
        _cycle(14, day=7, action="HOLD", rule="BUY", reason=""),
        _cycle(15, day=7, action="HOLD", rule="SELL", reason="",
               intervention=SimpleNamespace(original_action="BUY", reason="not passed the screen")),
    ]

    summary = daily_report.day_summary(records, date(2026, 10, 7))

    assert len(summary["overrides"]) == 1, summary["overrides"]
    assert len(summary["abstentions"]) == 2, summary["abstentions"]
    text = daily_report.render(date(2026, 10, 7), summary, [], "unproven")
    assert "1 departure(s) from the rule by the model, with a reason" in text
    assert "2 difference(s) the system produced" in text
