from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from min_agent.atomicio import write_text_atomic
from min_agent.counterfactual import CounterfactualRow
from min_agent.models import CycleRecord, KnowledgeArtifact

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?%?")


def lesson_key(artifact: KnowledgeArtifact) -> str:
    """What a lesson says, with its figures masked.

    The calibration lesson is re-derived every reflection with fresh numbers - "the worst
    bucket is -16% ..., Brier 0.392 over 292 scored decisions" - and exact-text dedup saw each
    re-derivation as new. 18 of the 20 accepted lessons on 2026-10-07 were that one sentence,
    so the prompt's five lesson slots held five copies of it and the two other lessons never
    reached the model. Two lessons with the same key are the same lesson at different times.
    """
    text = artifact.answer or artifact.summary or ""
    return " ".join(_NUMBER.sub("#", text.lower()).split())


#: A lesson is judged only after it has been both shown and withheld on this many distinct
#: trading days - the plan's "two weeks", stated in market days because that is when cycles
#: exist - and on at least this many paired decisions in each arm.
LESSON_EFFECT_MIN_DAYS = 10
LESSON_EFFECT_MIN_PER_ARM = 30


@dataclass(frozen=True)
class LessonEffect:
    """What showing one lesson did to the model's departures from the rule.

    `shown_mean` and `withheld_mean` are the mean `override_value_pct` (probe value of the
    action taken minus the rule's) over paired LLM decisions that did and did not see the
    lesson. Their difference is the lesson's effect; `se` its standard error.
    """

    lesson_id: str
    shown: int
    withheld: int
    days: int
    shown_mean: float | None
    withheld_mean: float | None
    effect: float | None
    se: float | None

    @property
    def judgeable(self) -> bool:
        return (
            self.days >= LESSON_EFFECT_MIN_DAYS
            and self.shown >= LESSON_EFFECT_MIN_PER_ARM
            and self.withheld >= LESSON_EFFECT_MIN_PER_ARM
            and self.effect is not None
            and self.se is not None
        )

    @property
    def without_effect(self) -> bool:
        """Judgeable, and the effect is inside one standard error of zero."""
        return self.judgeable and abs(self.effect or 0.0) < (self.se or 0.0)


def lesson_effects(
    records: Sequence[CycleRecord], rows: Sequence[CounterfactualRow]
) -> dict[str, LessonEffect]:
    """Per lesson, the paired override value on cycles that showed it against those that did not.

    Only LLM decisions that recorded both the rule's action and the lesson split count:
    anything earlier saw every lesson, so it cannot be compared with anything.
    """
    value = {
        row.cycle_id: row.override_value_pct
        for row in rows
        if row.override_value_pct is not None
    }
    shown_by: dict[str, list[float]] = {}
    withheld_by: dict[str, list[float]] = {}
    days_by: dict[str, set[str]] = {}
    for record in records:
        decision = record.decision
        if decision.decision_source != "llm" or decision.lesson_ids is None:
            continue
        v = value.get(record.cycle_id)
        if v is None:
            continue
        day = record.snapshot.timestamp.date().isoformat()
        for arm, ids in ((shown_by, decision.lesson_ids), (withheld_by, decision.lessons_withheld or ())):
            for lesson_id in ids:
                arm.setdefault(lesson_id, []).append(v)
                days_by.setdefault(lesson_id, set()).add(day)
    out: dict[str, LessonEffect] = {}
    for lesson_id, days in days_by.items():
        shown, withheld = shown_by.get(lesson_id, []), withheld_by.get(lesson_id, [])
        def mean(xs: list[float]) -> float | None:
            return sum(xs) / len(xs) if xs else None

        def var(xs: list[float]) -> float:
            m = mean(xs) or 0.0
            return sum((x - m) ** 2 for x in xs) / (len(xs) - 1) if len(xs) > 1 else 0.0

        a, b = mean(shown), mean(withheld)
        effect = None if a is None or b is None else a - b
        se = (
            (var(shown) / len(shown) + var(withheld) / len(withheld)) ** 0.5
            if len(shown) > 1 and len(withheld) > 1 else None
        )
        out[lesson_id] = LessonEffect(lesson_id, len(shown), len(withheld), len(days), a, b, effect, se)
    return out


class KnowledgeLibrary:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def save(self, artifact: KnowledgeArtifact) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self._path(artifact.artifact_id), artifact.model_dump_json(indent=2) + "\n")

    def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).exists()

    def try_load(self, artifact_id: str) -> KnowledgeArtifact | None:
        path = self._path(artifact_id)
        if not path.exists():
            return None
        return KnowledgeArtifact.model_validate_json(path.read_text(encoding="utf-8"))

    def load(self, artifact_id: str) -> KnowledgeArtifact:
        return KnowledgeArtifact.model_validate_json(self._path(artifact_id).read_text(encoding="utf-8"))

    def list(self, status: str | None = None, tags: tuple[str, ...] | None = None) -> list[KnowledgeArtifact]:
        if not self.directory.exists():
            return []
        normalized_status = status.strip().upper() if status is not None else None
        normalized_tags = {tag.strip().lower() for tag in tags or ()}
        artifacts = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                artifact = KnowledgeArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if normalized_status is not None and artifact.status != normalized_status:
                continue
            if normalized_tags and not normalized_tags.issubset(set(artifact.tags)):
                continue
            artifacts.append(artifact)
        return artifacts

    def _path(self, artifact_id: str) -> Path:
        return self.directory / f"{artifact_id}.json"
