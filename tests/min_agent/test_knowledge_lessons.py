"""Five lesson slots must buy five distinct ideas.

The knowledge path is wired end to end - `relevant_lessons` -> `context["lessons"]`
-> the model's prompt - so it is not a write-only loop. But it was slicing before
deduplicating: `lessons[:max_lessons]` took the first five artifacts in library
order, and this library's order is dominated by a single statement. Ten of the
eleven accepted artifacts share one answer, so the decision prompt carried the same
sentence five times and learned one thing from a five-slot budget.

The doctor check that should have caught this counted distinct *summaries* and
warned only when the count collapsed to exactly 1, so 11 artifacts carrying 2
distinct statements reported OK. That is the same defect shape as the model-registry
severity bug - a ratio tested as a boolean - which is why both are fixed here rather
than treated as two coincidences.
"""
from __future__ import annotations

import json

import pytest

from min_agent.doctor import DoctorReport, _check_knowledge_value
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import KnowledgeArtifact
from min_agent.policy_engine import PolicyEngine
from min_agent.strategy_engine import StrategyLibrary


class _Cfg:
    def __init__(self, knowledge_dir):
        self.knowledge_dir = knowledge_dir


def _artifact(artifact_id, answer, summary=None, artifact_type="LESSON"):
    return KnowledgeArtifact(
        artifact_id=artifact_id, artifact_type=artifact_type, answer=answer,
        summary=summary or "No-exploration window detected from real journal cycles.",
        tags=("exploration",), source_kind="journal", source_refs=["c1"],
        created_at="2026-09-29T00:00:00Z", rationale="real journal window",
        status="ACCEPTED",
    )


def _library(tmp_path, artifacts):
    path = tmp_path / "knowledge"
    path.mkdir(exist_ok=True)
    library = KnowledgeLibrary(path)
    for artifact in artifacts:
        library.save(artifact)
    return library


def _value_check(tmp_path, artifacts):
    _library(tmp_path, artifacts)  # the check reads the directory, not a list
    report = DoctorReport()
    _check_knowledge_value(report, _Cfg(tmp_path / "knowledge"))
    return next(c for c in report.checks if c.name == "knowledge value")


def test_five_slots_are_not_filled_with_one_lesson(tmp_path):
    """The defect, reproduced: five slots, one idea."""
    artifacts = [_artifact(f"lesson-{i}", "the same answer") for i in range(5)]
    engine = PolicyEngine(
        strategy_library=StrategyLibrary(tmp_path / "empty"),
        knowledge_library=_library(tmp_path, artifacts),
        max_lessons=5,
    )
    lessons = engine.relevant_lessons("any")
    assert len(lessons) == 1, f"expected 1 distinct lesson, got {len(lessons)}"
    assert lessons[0].answer == "the same answer"


def test_distinct_lessons_each_get_a_slot(tmp_path):
    artifacts = [
        _artifact("a", "hold-only means degenerate"),
        _artifact("b", "probation must not require orders"),
        _artifact("c", "cost must be reported after the fact"),
    ]
    engine = PolicyEngine(
        strategy_library=StrategyLibrary(tmp_path / "empty"),
        knowledge_library=_library(tmp_path, artifacts),
        max_lessons=5,
    )
    assert len(engine.relevant_lessons("any")) == 3


def test_the_slot_cap_still_applies_to_distinct_lessons(tmp_path):
    # Distinct in words: lessons differing only in a figure are one lesson (`lesson_key`).
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india"]
    artifacts = [_artifact(chr(97 + i), f"distinct answer {w}") for i, w in enumerate(words)]
    engine = PolicyEngine(
        strategy_library=StrategyLibrary(tmp_path / "empty"),
        knowledge_library=_library(tmp_path, artifacts),
        max_lessons=5,
    )
    assert len(engine.relevant_lessons("any")) == 5, "the budget is a cap, not a target"


def test_non_lesson_artifacts_are_still_excluded(tmp_path):
    """Dedup must not become a way to smuggle other artifact types in."""
    artifacts = [
        _artifact("lesson-1", "a lesson"),
        _artifact("other-1", "not a lesson", artifact_type="OBSERVATION"),
    ]
    engine = PolicyEngine(
        strategy_library=StrategyLibrary(tmp_path / "empty"),
        knowledge_library=_library(tmp_path, artifacts),
        max_lessons=5,
    )
    assert [a.artifact_id for a in engine.relevant_lessons("any")] == ["lesson-1"]


def test_whitespace_and_case_are_the_same_lesson(tmp_path):
    """Copies differing only in formatting are still copies."""
    artifacts = [
        _artifact("a", "The Same   Answer"),
        _artifact("b", "the same answer"),
    ]
    engine = PolicyEngine(
        strategy_library=StrategyLibrary(tmp_path / "empty"),
        knowledge_library=_library(tmp_path, artifacts),
        max_lessons=5,
    )
    assert len(engine.relevant_lessons("any")) == 1


def test_duplication_warns_even_when_not_collapsed_to_one(tmp_path):
    """The check's old threshold was `distinct == 1`, which 11 artifacts and 2
    distinct statements sailed past."""
    artifacts = [_artifact(f"l{i}", "the same answer") for i in range(10)]
    artifacts.append(_artifact("odd", "a different answer entirely"))
    check = _value_check(tmp_path, artifacts)
    assert check.status.value == "warn", f"{check.status.value}: {check.detail}"
    assert "2 distinct" in check.detail, check.detail


def test_a_genuinely_informative_library_passes(tmp_path):
    artifacts = [_artifact(f"l{i}", f"a distinct lesson number {i}") for i in range(6)]
    assert _value_check(tmp_path, artifacts).status.value == "ok"


def test_an_empty_library_is_not_a_finding(tmp_path):
    assert _value_check(tmp_path, []).status.value == "ok"


def test_a_lesson_re_derived_with_new_figures_is_one_lesson_showing_the_newest(tmp_path):
    """18 of 20 accepted lessons on 2026-10-07 were one calibration sentence with new numbers.

    Exact-text dedup kept all of them, so the five prompt slots held five copies and the
    other lessons never reached the model.
    """
    from datetime import datetime, timezone

    older = _artifact("a", "the worst bucket is -16%, Brier 0.392 over 292 scored decisions").model_copy(
        update={"created_at": datetime(2026, 10, 1, tzinfo=timezone.utc)})
    newer = _artifact("b", "the worst bucket is -14%, Brier 0.394 over 303 scored decisions").model_copy(
        update={"created_at": datetime(2026, 10, 5, tzinfo=timezone.utc)})
    other = _artifact("c", "no exploration window detected from real journal cycles")
    engine = PolicyEngine(
        strategy_library=StrategyLibrary(tmp_path / "empty"),
        knowledge_library=_library(tmp_path, [older, other, newer]),
        max_lessons=5,
    )
    lessons = engine.relevant_lessons("any")
    assert [a.artifact_id for a in lessons if "bucket" in a.answer] == ["b"]
    assert {a.artifact_id for a in lessons} == {"b", "c"}


def test_admitting_a_newer_figure_retires_the_older_copy_without_deleting_it(tmp_path):
    from datetime import datetime, timezone

    from min_agent.knowledge_admission import KnowledgeAdmission

    library = _library(tmp_path, [_artifact("old", "Brier 0.392 over 292 scored decisions")])
    fresh = _artifact("new", "Brier 0.394 over 303 scored decisions").model_copy(
        update={"status": "PROPOSED", "created_at": datetime(2026, 10, 5, tzinfo=timezone.utc)})
    assert KnowledgeAdmission(knowledge_library=library).admit(fresh).accepted
    assert library.try_load("old").status == "RETIRED", "superseded, and kept with its refs"
    assert library.try_load("new").status == "ACCEPTED"


def _ablated(n_days, shown_value, withheld_value, per_day=4, jitter=0.01):
    """Paired LLM decisions over `n_days`, half showing lesson L and half withholding it."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    records, rows = [], []
    t0 = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    for d in range(n_days):
        for k in range(per_day * 2):
            shown = k % 2 == 0
            cid = f"d{d}k{k}"
            value = (shown_value if shown else withheld_value) + (jitter if k % 4 < 2 else -jitter)
            records.append(SimpleNamespace(
                cycle_id=cid,
                snapshot=SimpleNamespace(timestamp=t0 + timedelta(days=d, minutes=5 * k)),
                decision=SimpleNamespace(decision_source="llm",
                                         lesson_ids=("L",) if shown else (),
                                         lessons_withheld=() if shown else ("L",)),
            ))
            rows.append(SimpleNamespace(cycle_id=cid, override_value_pct=value))
    return records, rows


def test_a_lesson_s_effect_is_the_difference_between_its_two_arms():
    from min_agent.knowledge_library import lesson_effects

    effect = lesson_effects(*_ablated(10, 0.30, 0.10))["L"]
    assert (effect.shown, effect.withheld, effect.days) == (40, 40, 10)
    assert effect.effect == pytest.approx(0.20)
    assert effect.judgeable and not effect.without_effect


def test_no_difference_after_enough_days_is_without_effect():
    from min_agent.knowledge_library import lesson_effects

    effect = lesson_effects(*_ablated(10, 0.10, 0.10, jitter=0.2))["L"]
    assert effect.without_effect


def test_too_few_days_is_never_judged():
    """Inert until the ablation has run its course: no lesson is retired on a week of data."""
    from min_agent.knowledge_library import lesson_effects

    effect = lesson_effects(*_ablated(5, 0.10, 0.10, per_day=10, jitter=0.2))["L"]
    assert not effect.judgeable and not effect.without_effect
