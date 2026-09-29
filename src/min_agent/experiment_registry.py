"""The loop's own audit trail: what was proposed, and what became of it.

The objective asks for a unique state source and for the chain
`hypothesis -> candidate -> offline validation -> shadow/probation -> live
validation -> promote/scale/pause/retire` to be one auditable path. Every link in
that chain is already journalled - CURRICULUM_PROPOSED, STRATEGY_ADMISSION_REVIEWED,
OFFLINE_VALIDATION_COMPLETED, STRATEGY_LIFECYCLE_UPDATED, STRATEGY_EVALUATION_RECORDED
- so there was no need for a registry to exist.

That is the point. A second persisted registry would be a second source of truth
about the same thing, and the two would drift, which is precisely the failure this
refactor has been removing everywhere else. So this module is a *view*: it derives
the chain per strategy from the journal and never writes anything.

A strategy's story is only complete if every link is present. A proposal with no
admission verdict, or a strategy that reached ACTIVE with no offline validation and
no evaluation evidence, is reported as a broken link rather than quietly omitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

PROPOSAL = "CURRICULUM_PROPOSED"
ADMISSION = "STRATEGY_ADMISSION_REVIEWED"
VALIDATION = "OFFLINE_VALIDATION_COMPLETED"
LIFECYCLE = "STRATEGY_LIFECYCLE_UPDATED"
EVALUATION = "STRATEGY_EVALUATION_RECORDED"


@dataclass
class Experiment:
    """One strategy's journey, as recorded."""

    strategy_id: str
    kind: str = ""
    rationale: str = ""
    proposed: int = 0
    admitted: bool | None = None
    admission_reason: str = ""
    offline_verdict: str = ""
    offline_reason: str = ""
    offline_scored: int = 0
    offline_good_hold_ratio: float | None = None
    lifecycle: str = ""
    lifecycle_reason: str = ""
    evaluations: int = 0
    cycles: int = 0
    missing_links: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing_links


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build(
    events: Sequence[object],
    strategies: Iterable[object] = (),
) -> list[Experiment]:
    """Derive one Experiment per strategy from the journal.

    `strategies` is the registry, so a strategy that exists on disk but never
    appears in the journal still shows up - otherwise the view would silently
    hide exactly the case worth looking at.
    """
    registry: dict[str, Experiment] = {}

    def slot(strategy_id: str) -> Experiment:
        if strategy_id not in registry:
            registry[strategy_id] = Experiment(strategy_id=strategy_id)
        return registry[strategy_id]

    for spec in strategies:
        experiment = slot(spec.strategy_id)
        experiment.kind = _field(spec, "kind", "") or ""
        experiment.rationale = _field(spec, "rationale", "") or ""
        experiment.lifecycle = _field(spec, "lifecycle", "") or ""

    for event in events:
        event_type = _field(event, "event_type", "")
        strategy_id = _field(event, "strategy_id")
        payload = _field(event, "payload", None) or {}

        if event_type == PROPOSAL and strategy_id:
            slot(strategy_id).proposed += 1

        elif event_type == ADMISSION and strategy_id:
            experiment = slot(strategy_id)
            experiment.admitted = bool(payload.get("accepted"))
            experiment.admission_reason = str(payload.get("reason", ""))

        elif event_type == VALIDATION and strategy_id:
            experiment = slot(strategy_id)
            experiment.offline_verdict = str(payload.get("verdict", ""))
            experiment.offline_reason = str(payload.get("reason", ""))
            experiment.offline_scored = int(payload.get("scored", 0) or 0)
            experiment.offline_good_hold_ratio = payload.get("good_hold_ratio")

        elif event_type == LIFECYCLE and strategy_id:
            experiment = slot(strategy_id)
            new_lifecycle = payload.get("new_lifecycle")
            if new_lifecycle and payload.get("phase") == "applied":
                experiment.lifecycle = str(new_lifecycle)
                experiment.lifecycle_reason = str(payload.get("reason", ""))

        elif event_type == EVALUATION:
            # The per-strategy evidence lives in payload["strategy_metrics"], keyed
            # by strategy_id. The event's own strategy_id is None, so reading that
            # field reported "tradable with no evaluation evidence" for strategies
            # that had been evaluated 663 times - the same mistake as reading
            # entry_rules that StrategySpec never had.
            metrics = payload.get("strategy_metrics") or {}
            for metrics_id in metrics:
                slot(str(metrics_id)).evaluations += 1

    for experiment in registry.values():
        if experiment.admitted is None:
            experiment.missing_links.append("no admission verdict")
        if experiment.admitted and not experiment.offline_verdict:
            experiment.missing_links.append("admitted but never screened")
        if experiment.lifecycle in {"ACTIVE", "PROBATION"} and not experiment.evaluations:
            experiment.missing_links.append("tradable with no evaluation evidence")

    return sorted(registry.values(), key=lambda e: e.strategy_id)


def summarize(experiments: Iterable[Experiment]) -> dict:
    """Counts for reporting. Every total is over strategies actually present, and
    broken links are counted separately rather than folded into the totals - a
    summary that hides an incomplete chain is worse than no summary."""
    items = list(experiments)
    by_lifecycle: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    for experiment in items:
        by_lifecycle[experiment.lifecycle or "UNKNOWN"] = (
            by_lifecycle.get(experiment.lifecycle or "UNKNOWN", 0) + 1
        )
        verdict = experiment.offline_verdict or "UNSCREENED"
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    broken = [e.strategy_id for e in items if e.missing_links]
    return {
        "strategies": len(items),
        "admitted": sum(1 for e in items if e.admitted),
        "rejected": sum(1 for e in items if e.admitted is False),
        "by_lifecycle": by_lifecycle,
        "by_offline_verdict": by_verdict,
        "incomplete_chains": len(broken),
        "incomplete_strategy_ids": broken[:10],
    }
