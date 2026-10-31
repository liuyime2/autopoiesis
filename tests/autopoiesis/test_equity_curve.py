"""Whole-account Sharpe, drawdown and the 60-day cohort counter, on synthetic series with known answers.

All series here are synthetic and labelled so; none comes from the broker.

Usage: pytest tests/autopoiesis/test_equity_curve.py -q
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

from autopoiesis.equity_curve import (
    DayMark,
    cohort_days,
    cohort_start,
    compare_with_spy,
    daily_marks,
    max_drawdown,
    returns,
    sharpe,
)
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)


def _cycle(at, equity, symbol="SPY", price=500.0, source="alpaca") -> CycleRecord:
    return CycleRecord(
        cycle_id="c", snapshot=DataSnapshot(
            symbol=symbol, timestamp=at, market_open=True, last_price=price, source=source,
            account=AccountSnapshot(equity=equity, cash=0, buying_power=0, portfolio_value=equity, daily_loss=0)),
        decision=TradeDecision(symbol=symbol, action="HOLD", quantity=0, confidence=0.5, rationale="t"),
        guardian=GuardianResult(approved=True, reason="ok"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold"),
    )


def test_max_drawdown_is_peak_to_trough():
    assert max_drawdown([100, 120, 90, 110]) == -0.25
    assert max_drawdown([100, 101, 102]) == 0.0


def test_sharpe_of_a_known_series_and_none_when_too_short():
    values = [100.0]
    for i in range(30):
        values.append(values[-1] * (1.01 if i % 2 == 0 else 1.0))
    r = returns(values)
    mean = sum(r) / len(r)
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1))
    assert math.isclose(sharpe(values), mean / sd * math.sqrt(252))
    assert sharpe(values[:10]) is None
    assert sharpe([100.0] * 40) is None, "no variance, no ratio"


def test_marks_take_the_last_live_snapshot_of_each_new_york_day_and_ignore_replay():
    day1 = datetime(2026, 11, 2, 14, 31, tzinfo=timezone.utc)
    records = [
        _cycle(day1, 100_000), _cycle(day1 + timedelta(hours=5), 100_500, price=510.0),
        _cycle(day1 + timedelta(hours=6), 1.0, source="alpaca_replay"),
        _cycle(day1 + timedelta(days=1), 101_000, symbol="TLT", price=80.0),
    ]
    marks = daily_marks(records)
    assert [(m.day, m.equity, m.spy) for m in marks] == [
        (date(2026, 11, 2), 100_500, 510.0), (date(2026, 11, 3), 101_000, None)]


def test_the_comparison_is_not_judged_before_sixty_days():
    marks = [DayMark(date(2026, 11, 2) + timedelta(days=i), 100_000 + i, 500 + i) for i in range(30)]
    result = compare_with_spy(marks)
    assert result["days"] == 30
    assert result["risk_adjusted_not_below_spy"] is None and result["drawdown_smaller_than_spy"] is None


def test_at_sixty_days_both_questions_are_answered():
    marks = []
    equity = spy = 100.0
    for i in range(60):
        spy *= 1.01 if i % 2 == 0 else 0.985
        equity *= 1.004 if i % 2 == 0 else 0.998
        marks.append(DayMark(date(2026, 11, 2) + timedelta(days=i), equity, spy))
    result = compare_with_spy(marks)
    assert result["days"] == 60
    assert result["drawdown_smaller_than_spy"] is True
    assert result["risk_adjusted_not_below_spy"] is True


def test_the_cohort_restarts_on_a_new_source_fingerprint_but_not_on_a_redeploy_of_the_same_build(tmp_path):
    path = tmp_path / "deployments.jsonl"
    rows = [
        {"fingerprint": "aaa", "commit": "1", "recorded_at": "2026-10-20T10:00:00-04:00"},
        {"fingerprint": "bbb", "commit": "2", "recorded_at": "2026-10-24T21:00:00-04:00"},
        {"fingerprint": "bbb", "commit": "3", "recorded_at": "2026-10-29T23:56:00-04:00"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    started, fingerprint = cohort_start(path)
    assert fingerprint == "bbb" and started.isoformat().startswith("2026-10-24T21:00")
    marks = [DayMark(date(2026, 10, 24) + timedelta(days=i), 1.0, 1.0) for i in range(10)]
    assert cohort_days(marks, started) == 9, "the install day itself is not a day under the build"
    assert cohort_start(tmp_path / "missing.jsonl") is None
