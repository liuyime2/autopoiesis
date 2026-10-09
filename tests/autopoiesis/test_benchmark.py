"""`tools/benchmark.py` - the falsifiable target's own exit contract and parse.

The benchmark's exit code is the contract (0 met, 1 behind, 2 nothing to measure), so a
fresh clone with no journal must say "nothing to measure" rather than print a number for a run
that never happened. The parse is tested against the sentence shape `doctor` emits, because a
previous version split it on "; " and silently rendered no table at all.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

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
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "src"),
        "MIN_AGENT_JOURNAL": str(journal),
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


def test_a_detail_without_rows_is_not_measurable():
    assert _load_benchmark()._parse_comparison("no closed lots on record") is None


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
    assert code == 1
    # The worst-case naming survives, but as a labelled diagnostic: the verdict itself is now the
    # agent-level claim the target sentence makes, and a single weak strategy failing must not be
    # reported as the agent failing.
    assert "worst b by 4.29" in verdict
    assert "diagnostic" in verdict
    assert "the agent returned +1.95%" in verdict and "SPY +2.80%" in verdict
