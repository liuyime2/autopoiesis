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
    #: Decisions where the model was consulted, i.e. `decision_source == "llm"`.
    asked: int = 0
    #: Of those, the ones with no model recorded. This is the provenance gap: the model
    #: answered and the answer was not attributed.
    unattributed_asked: int = 0
    #: Decisions the model was never asked about - the rule path. `model` is None by
    #: definition here, and counting them as an attribution failure is the same conflation
    #: `_post_cycle_model_flag` already had to fix for `last_model_ok`: "the model was not
    #: asked" and "the model was asked and not recorded" are two different facts, and a
    #: monitor that merges them reports a failure where there is none. Measured 2026-10-14:
    #: 1824 of 3221 decisions were reported as "cannot be attributed to a model" while only
    #: 67 of them were decisions the model actually made.
    not_asked: int = 0

    def payloads(self) -> list[dict]:
        return [m.to_payload() for m in self.models.values()]

    def summary(self) -> str:
        named = [m for m in self.models if m != UNATTRIBUTED]
        if not named:
            return (
                f"{self.total_decisions} decision(s), none of which records a model; "
                f"provenance was not captured, so none of the {self.asked} the model "
                f"answered can be attributed to it"
            )
        detail = ", ".join(
            f"{name} {rec.decisions}" for name, rec in sorted(self.models.items())
            if name != UNATTRIBUTED
        )
        # The two gaps are reported separately and neither is folded into the other.
        parts = [f"{self.total_decisions} decision(s): {detail}"]
        if self.unattributed_asked:
            parts.append(
                f"{self.unattributed_asked} of the {self.asked} the model answered "
                f"predate provenance and are unattributable"
            )
        if self.not_asked:
            parts.append(
                f"{self.not_asked} were decided by the rule and the model was never asked, "
                f"so they have no model to record"
            )
        return "; ".join(parts)


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
        asked = getattr(decision, "decision_source", None) == "llm"
        if asked:
            registry.asked += 1
            if name == UNATTRIBUTED:
                registry.unattributed_asked += 1
        else:
            registry.not_asked += 1
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
