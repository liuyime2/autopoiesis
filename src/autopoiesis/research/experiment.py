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
    # Conclusion placeholder: requires statistical comparison (bootstrap) not yet wired.
    if len(report.arms) < 4:
        report.conclusion = "INSUFFICIENT"
        report.notes.append("not all arms ran")
    else:
        report.conclusion = "INSUFFICIENT"
        report.notes.append("statistical comparison (paired block bootstrap) pending")
    return report
