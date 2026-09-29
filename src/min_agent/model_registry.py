"""Which models exist, which produced which decisions, and what they are worth.

Phase 3 asks for a strategy **and model** registry. The strategy half exists. The
model half did not: every decision recorded `decision_source` - "llm",
"fallback_policy_engine", "baseline" - but not *which* model, so the 67 LLM
decisions could not be separated by prompt or by model version, and no outcome could
ever be attributed to a model change. The model was upgraded during this project, and
without provenance that upgrade is invisible in the record and its effect is
unmeasurable forever.

This is a **view over the journal**, like the experiment registry and the lineage
module. A separate persisted store of model performance would be a second source of
truth about numbers that are already recorded, and would drift from them.

The 920 cycles already on the journal predate the `model` field and carry `None`.
They are reported as `UNATTRIBUTED` rather than back-filled with the currently
configured model, which would assert a fact about the past that is not known.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

UNATTRIBUTED = "UNATTRIBUTED"


@dataclass
class ModelRecord:
    name: str
    decisions: int = 0
    actions: dict[str, int] = field(default_factory=dict)
    submitted: int = 0
    rejected: int = 0
    mean_confidence: float | None = None
    _confidence_sum: float = field(default=0.0, repr=False)
    first_seen: str | None = None
    last_seen: str | None = None

    def to_payload(self) -> dict:
        return {
            "name": self.name,
            "decisions": self.decisions,
            "actions": dict(sorted(self.actions.items())),
            "submitted_orders": self.submitted,
            "rejected_orders": self.rejected,
            "mean_confidence": (
                None if self.mean_confidence is None
                else round(self.mean_confidence, 4)
            ),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class ModelRegistry:
    models: dict[str, ModelRecord] = field(default_factory=dict)
    total_decisions: int = 0

    def payloads(self) -> list[dict]:
        return [m.to_payload() for m in self.models.values()]

    def summary(self) -> str:
        named = [m for m in self.models if m != UNATTRIBUTED]
        unattributed = self.models[UNATTRIBUTED].decisions if UNATTRIBUTED in self.models else 0
        if not named:
            return (
                f"{self.total_decisions} decision(s), none of which records a model; "
                "provenance was not captured, so none can be attributed to a model"
            )
        detail = ", ".join(
            f"{name} {rec.decisions}" for name, rec in sorted(self.models.items())
            if name != UNATTRIBUTED
        )
        note = (
            f"; {unattributed} predate provenance and are unattributable"
            if unattributed else ""
        )
        return f"{self.total_decisions} decision(s): {detail}{note}"


def build(records: list) -> ModelRegistry:
    """Group decisions by the model that produced them."""
    registry = ModelRegistry()
    for record in records:
        decision = getattr(record, "decision", None)
        if decision is None:
            continue
        name = getattr(decision, "model", None) or UNATTRIBUTED
        entry = registry.models.setdefault(name, ModelRecord(name=name))
        registry.total_decisions += 1
        entry.decisions += 1
        action = getattr(decision, "action", None)
        if action:
            entry.actions[action] = entry.actions.get(action, 0) + 1
        entry._confidence_sum += float(getattr(decision, "confidence", 0.0) or 0.0)
        entry.mean_confidence = entry._confidence_sum / entry.decisions
        execution = getattr(record, "execution", None)
        status = getattr(execution, "status", None) if execution else None
        if status == "SUBMITTED":
            entry.submitted += 1
        elif status == "REJECTED":
            entry.rejected += 1
        stamp = getattr(getattr(record, "snapshot", None), "timestamp", None)
        if stamp is not None:
            text = stamp.isoformat()
            entry.first_seen = entry.first_seen or text
            entry.last_seen = text
    return registry
