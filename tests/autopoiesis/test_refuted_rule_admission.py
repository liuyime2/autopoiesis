"""A RULE that repeats a test the signal ledger already ran and found nothing in is not admitted.

The ledger rows below are synthetic and labelled so; they copy the shape of the real ledger
(`intraday:SPY:M<n>->f<h>` return over n bars, `S<n>` distance from the n-bar average).

Usage: pytest tests/autopoiesis/test_refuted_rule_admission.py -q
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from autopoiesis.guardian import Guardian
from autopoiesis.models import StrategySpec
from autopoiesis.signal_ledger import ledger_summary, read_rows, refuted_lookbacks
from autopoiesis.strategy_admission import StrategyAdmission
from autopoiesis.strategy_engine import StrategyLibrary

REFUTED = [
    {"name": f"intraday:SPY:M{n}->f{h}", "verdict": "NOT_SIGNIFICANT"} for n in (1, 3, 6, 12, 24, 48) for h in (12, 48)
] + [{"name": f"intraday:SPY:S{n}->f{h}", "verdict": "NOT_SIGNIFICANT"} for n in (12, 48) for h in (12, 48)] + [
    {"name": "long:13etf:RV21->F21", "verdict": "SURVIVES"},
]


def _ledger(tmp_path, rows):
    path = tmp_path / "signal_trials.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _rule(lookback, signal="price_vs_sma", strategy_id="rule-x-001") -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id, name="x", kind="RULE", symbols=("SPY",),
        parameters={"signal": signal, "lookback": lookback, "threshold_pct": 0.5, "when_above": "SELL",
                    "when_below": "BUY", "quantity": 1, "confidence": 0.6},
        max_position_value=5_000.0, created_at=datetime(2026, 10, 31, tzinfo=timezone.utc), rationale="x",
    )


def _admission(tmp_path, ledger):
    guardian = Guardian(max_position_value=25_000.0, max_daily_loss=500.0, max_trades_per_day=100,
                        allowlist=frozenset({"SPY"}))
    gate = StrategyAdmission(guardian=guardian, strategy_library=StrategyLibrary(tmp_path / "lib"), signal_ledger=ledger)
    base = StrategySpec(strategy_id="hold-baseline-001", name="b", kind="HOLD_BASELINE", symbols=("SPY",),
                        parameters={}, max_position_value=1.0,
                        created_at=datetime(2026, 10, 31, tzinfo=timezone.utc), rationale="b")
    assert gate.admit(base).accepted
    first = StrategySpec(strategy_id="fixed-size-buy-000", name="f", kind="FIXED_SIZE", symbols=("SPY",),
                         parameters={"action": "BUY", "quantity": 1, "confidence": 0.7},
                         max_position_value=5_000.0, created_at=datetime(2026, 10, 31, tzinfo=timezone.utc), rationale="f")
    assert gate.admit(first).accepted
    return gate


def test_the_longest_refuted_lookback_is_read_from_the_ledger_per_signal():
    got = refuted_lookbacks(REFUTED)
    assert got["return_over_n"][0] == 48 and got["price_vs_sma"][0] == 48
    assert "intraday:SPY:M1->f12" in got["return_over_n"][1]


def test_a_family_with_a_survivor_is_not_refused():
    rows = REFUTED + [{"name": "intraday:SPY:M5->f12", "verdict": "SURVIVES"}]
    assert "return_over_n" not in refuted_lookbacks(rows)
    assert "price_vs_sma" in refuted_lookbacks(rows)


def test_underpowered_or_exploratory_rows_do_not_count_as_refutations():
    rows = [{"name": "intraday:SPY:M24->f12", "verdict": "UNDERPOWERED"}, {"name": "intraday:SPY:M12->f12", "verdict": "EXPLORATORY"}]
    assert refuted_lookbacks(rows) == {}


def test_a_short_lookback_rule_is_refused_with_the_ledger_entries_named(tmp_path):
    gate = _admission(tmp_path, _ledger(tmp_path, REFUTED))
    for lookback in (5, 6, 21, 48):
        result = gate.admit(_rule(lookback, strategy_id=f"rule-short-{lookback}"))
        assert result.accepted is False and "repeats a signal-ledger test" in result.reason, lookback
    assert "intraday:SPY:M1->f12" in gate.admit(_rule(5, "return_over_n", "rule-m-5")).reason


def test_a_lookback_beyond_the_tested_range_is_admitted(tmp_path):
    gate = _admission(tmp_path, _ledger(tmp_path, REFUTED))
    assert gate.admit(_rule(60)).accepted is True


def test_without_a_ledger_nothing_changes(tmp_path):
    assert _admission(tmp_path, None).admit(_rule(5)).accepted is True
    assert _admission(tmp_path / "x", tmp_path / "absent.jsonl").admit(_rule(5)).accepted is True


def test_the_summary_still_reads_the_same_ledger(tmp_path):
    path = _ledger(tmp_path, REFUTED)
    assert len(read_rows(path)) == len(REFUTED)
    assert ledger_summary(path)["survives"] == ["long:13etf:RV21->F21"]
