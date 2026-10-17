"""`tools/benchmark.py` - the falsifiable target's own exit contract and parse.

The benchmark's exit code is the contract (0 met, 1 behind, 2 nothing to measure), so a
fresh clone with no journal must say "nothing to measure" rather than print a number for a run
that never happened. The parse is tested against the sentence shape `doctor` emits, because a
previous version split it on "; " and silently rendered no table at all.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
BENCHMARK = REPO / "tools" / "benchmark.py"


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("benchmark_under_test", BENCHMARK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_empty_journal_exits_nothing_to_measure(tmp_path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text("")
    # `src` is *prepended*, not substituted. Replacing whatever the caller had drops every other
    # site-packages directory the interpreter needs, which is how this test fails on an
    # interpreter whose dependencies are not installed into the default path - it asserted exit
    # 2 and got exit 1, and the traceback it printed was `ModuleNotFoundError: pydantic`, not
    # anything about the benchmark.
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(REPO / "src"), *os.environ.get("PYTHONPATH", "").split(os.pathsep)]
        ),
        "AUTOPOIESIS_JOURNAL": str(journal),
    }
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == _load_benchmark().EXIT_NOTHING_TO_MEASURE == 2, proc.stdout + proc.stderr
    assert "nothing to measure" in proc.stdout


def test_comparison_rows_are_comma_separated_inside_one_sentence():
    detail = (
        "2 of 2 strategy/ies behind the market: "
        "tiny-fixed-size-001=+3.94% vs market +5.41% (-1.47), "
        "fixed-size-buy-001=+1.92% vs market +5.41% (-3.48)"
    )
    rows = _load_benchmark()._parse_comparison(detail)
    assert rows == [
        ("tiny-fixed-size-001", "+3.94%", "+5.41%", "-1.47"),
        ("fixed-size-buy-001", "+1.92%", "+5.41%", "-3.48"),
    ]


def test_empty_json_exposes_unknown_target_without_private_host_path(tmp_path):
    journal = tmp_path / "synthetic-private-journal.jsonl"
    journal.write_text("")
    proc = subprocess.run([sys.executable, str(BENCHMARK), "--json"], cwd=tmp_path,
        env={**os.environ, "AUTOPOIESIS_JOURNAL": str(journal)},
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["target"]["status"] == "UNKNOWN"
    assert payload["target"]["met"] is False
    assert str(tmp_path) not in proc.stdout


def test_a_detail_without_rows_is_not_measurable():
    assert _load_benchmark()._parse_comparison("no closed lots on record") is None


@pytest.mark.parametrize("output", ["", "not JSON", '{"exit_code": 2}'])
def test_collector_rejects_failed_or_inconsistent_measurement(monkeypatch, capsys, output):
    import runpy

    collector = runpy.run_path(str(BENCHMARK.with_name("benchmark_json.py")))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=1, stdout=output, stderr="synthetic execution failure"))
    assert collector["main"]() != 0
    assert capsys.readouterr().out == ""


def test_collector_preserves_valid_unknown_measurement(monkeypatch, capsys):
    import runpy

    collector = runpy.run_path(str(BENCHMARK.with_name("benchmark_json.py")))
    output = json.dumps({"exit_code": 2, "target": {"status": "UNKNOWN", "met": False}})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=2, stdout=output, stderr=""))
    assert collector["main"]() == 0
    assert capsys.readouterr().out == output


def test_the_verdict_names_the_strategy_furthest_behind():
    """`max` over negative excesses picked the least-bad strategy and called it the worst.

    The verdict on 2026-10-06 read "worst tiny-fixed-size-001 by 1.47" while
    fixed-size-probe-0001 was 4.29 behind.
    """

    class _Check:
        name, status = "pnl vs holding", type("S", (), {"value": "warn"})()
        detail = (
            "2 of 2 strategy/ies behind the market: "
            "a=+3.94% vs market +5.41% (-1.47), b=+1.11% vs market +5.41% (-4.29)"
        )

    class _Report:
        checks = [_Check()]

    agent = {"return_pct": 1.95, "spy_pct": 2.80, "excess": -0.85, "pnl": 867.73,
             "realized": 757.95, "unrealized": 110.16, "capital": 44455.08}
    verdict, code = _load_benchmark()._headline(_Report(), agent)
    assert code == 2
    # The worst-case naming survives, but as a labelled diagnostic: the verdict itself is now the
    # agent-level claim the target sentence makes, and a single weak strategy failing must not be
    # reported as the agent failing.
    assert "worst b by 4.29" in verdict
    assert "diagnostic" in verdict
    assert "NOT MEASURABLE" in verdict


def test_legacy_positive_ratio_cannot_pass_the_portfolio_target():
    check = SimpleNamespace(name="pnl vs holding", status=SimpleNamespace(value="ok"),
        detail="every strategy beat the market: a=+10% vs market +5% (+5)")
    report = SimpleNamespace(checks=[check])
    verdict, code = _load_benchmark()._headline(report,
        {"return_pct": 10.0, "spy_pct": 5.0, "excess": 5.0})
    assert code == 2
    assert "NOT MEASURABLE" in verdict


@pytest.mark.parametrize("unrealized,net,linked", [(0.0, 28.0, 1), (None, None, 1), (0.0, 0.0, 0)])
def test_synthetic_raw_pnl_never_invents_allocated_capital(monkeypatch, tmp_path, unrealized, net, linked):
    from autopoiesis import broker_evidence, evaluator, journal
    from autopoiesis.config import AgentConfig

    config = replace(AgentConfig.documented_defaults(),
        journal_path=tmp_path / "journal.jsonl", assumed_round_trip_cost_pct=0.07)
    monkeypatch.setattr(AgentConfig, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(journal.JsonlJournal, "read_all", lambda self: [object()])
    monkeypatch.setattr(broker_evidence, "latest_evidence_batch", lambda j: object())
    monkeypatch.setattr(evaluator, "confirmed_fill_activities", lambda *args: [])
    monkeypatch.setattr(evaluator, "owner_fill_activities", lambda *args: [])
    pnl = SimpleNamespace(status="verified", linked_fill_count=linked,
        strategy_peak_exposure={"a": 100.0, "b": 200.0} if linked else {},
        strategy_realized_pnl={"a": 10.0, "b": 20.0} if linked else {}, strategy_fees={}, unrealized_pnl=unrealized,
        assumed_cost={"a": 2.0}, net_after_assumed_cost=net, market_return_pct=0.0)
    called = {}

    def compute(*args, **kwargs):
        called.update(kwargs)
        return pnl

    monkeypatch.setattr(evaluator, "_pnl_evidence", compute)
    result = _load_benchmark()._agent_level()
    if not linked:
        assert result is None
        return
    assert result is not None
    assert result["capital"] is None
    assert result["return_pct"] is None and result["excess"] is None
    assert result["unrealized"] == unrealized
    assert result["pnl"] == net
    assert result["diagnostic_market_return_pct"] == 0.0
    assert called["assumed_round_trip_cost_pct"] == 0.07
