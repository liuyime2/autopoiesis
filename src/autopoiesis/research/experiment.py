"""Four-arm experiment orchestration (research only).

Step 4 of docs/EXECUTION_PLAN_2026-11-05.md. Arms:
  A: core sleeve only (risk baseline)
  B: core sleeve + fixed rules
  C: core sleeve + non-LLM automated search
  D: core sleeve + LLM weekly proposals + critic review

Primary comparison is D minus C on daily net return, paired block bootstrap.
Conclusion may be PROVEN / NOT PROVEN / INSUFFICIENT.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .simulation.runner import ReplayResult, run_replay
from .statistics import daily_returns, paired_block_bootstrap


@dataclass
class ArmResult:
    arm: str
    result: ReplayResult
    seed: int = 0


@dataclass
class ExperimentReport:
    arms: list[ArmResult] = field(default_factory=list)
    primary_comparison: str = "D-C"
    conclusion: str = "INSUFFICIENT"
    notes: list[str] = field(default_factory=list)


ARMS = ("A", "B", "C", "D")


def run_experiment(
    *,
    bars: Iterable[dict],
    decision_factories: dict[str, Callable[[], Callable[[dict], str]]],
    seeds: Iterable[int] = (0,),
    llm_arms: Iterable[str] = ("D",),
) -> ExperimentReport:
    """Run each arm x seed and assemble a report.

    decision_factories maps arm label -> callable returning a decision_fn.
    """
    report = ExperimentReport()
    bars_list = list(bars)
    for arm in ARMS:
        if arm not in decision_factories:
            continue
        for seed in seeds:
            factory = decision_factories[arm]
            decision_fn = factory()
            llm_used = arm in llm_arms
            result = run_replay(bars=bars_list, decision_fn=decision_fn, llm_used=llm_used)
            report.arms.append(ArmResult(arm=arm, result=result, seed=seed))
    # Statistical comparison: D minus C (primary), and B minus A.
    by_arm: dict[str, ReplayResult] = {}
    for ar in report.arms:
        by_arm[ar.arm] = ar.result
    if "D" in by_arm and "C" in by_arm:
        ra = daily_returns(by_arm["C"].equity_curve)
        rb = daily_returns(by_arm["D"].equity_curve)
        boot = paired_block_bootstrap(ra, rb)
        report.notes.append(f"D-C: mean_diff={boot['mean_diff']:.6f} CI=[{boot['ci_low']:.6f},{boot['ci_high']:.6f}]")
        if boot["ci_low"] > 0:
            report.conclusion = "PROVEN"
        elif boot["ci_high"] < 0:
            report.conclusion = "NOT PROVEN"
        else:
            report.conclusion = "INSUFFICIENT"
    elif len(report.arms) < 4:
        report.conclusion = "INSUFFICIENT"
        report.notes.append("not all arms ran")
    else:
        report.conclusion = "INSUFFICIENT"
    return report
