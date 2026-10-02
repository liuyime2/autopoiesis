"""Where a candidate came from, how hard the system searched for it, and who is
currently being compared with whom.

Three things the objective requires and the system did not record:

* **Lineage** - which observation produced a candidate. `StrategySpec` has no lineage
  field at all, so nothing links a strategy back to the failure it was proposed to
  fix. The link exists in the journal: the curriculum event carries the exploration
  summary that motivated it and the `task_id` it came from.
* **Search effort** - how many variants were tried. The live journal holds 69
  proposals across 63 task ids, and one task produced 4 specs. That is a search, and
  a search that leaves no trace is how selection bias becomes invisible: keep the
  best of four and report it as though it were the only one.
* **Champion-challenger** - which strategy is currently the one everything else is
  measured against. The registry had 2 ACTIVE strategies and no way to say which was
  the benchmark.

Like the experiment registry, this is a **view over the journal** and not a store.
A second persisted ledger about the same events would drift from them, and drift in
the trial count is precisely the failure this is meant to prevent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from min_agent import coerce

PROPOSAL = "CURRICULUM_PROPOSED"
ADMISSION = "STRATEGY_ADMISSION_REVIEWED"
VALIDATION = "OFFLINE_VALIDATION_COMPLETED"
LIFECYCLE = "STRATEGY_LIFECYCLE_UPDATED"
EVALUATION = "STRATEGY_EVALUATION_RECORDED"

CHAMPION = "CHAMPION"
CHALLENGER = "CHALLENGER"
UNRATED = "UNRATED"


@dataclass
class CandidateOrigin:
    """One candidate, and the observation that produced it."""

    strategy_id: str
    task_id: str | None = None
    rationale: str = ""
    source: str = ""
    observation: dict = field(default_factory=dict)
    task_type: str = ""
    #: How many specs this same task proposed. A task proposing four is a search,
    #: and the count is the whole point of recording it.
    siblings_from_same_task: int = 1
    accepted: bool | None = None
    admission_reason: str = ""
    offline_verdict: str = ""
    offline_scored: int = 0
    offline_good_hold_ratio: float | None = None
    lifecycle: str = ""

    @property
    def is_search_result(self) -> bool:
        return self.siblings_from_same_task > 1

    def missing_justification(self) -> list[str]:
        """What this candidate failed to state about itself.

        The objective requires every candidate to say what observed failure it
        addresses, how it differs from what already exists, what data it used and how
        many searches it went through. The proposal schema requires only free-text
        `rationale`, so all four can be absent while the candidate is admitted.

        This reports the gap rather than blocking admission on it, because inventing a
        justification after the fact would be worse than recording that none was
        given.
        """
        gaps: list[str] = []
        if not self.observation:
            gaps.append("no recorded observation that motivated it")
        if not self.rationale:
            gaps.append("no rationale")
        if not self.offline_verdict:
            gaps.append("never screened")
        elif self.offline_verdict != "PASS_SCREENED":
            gaps.append(f"screened as {self.offline_verdict}")
        return gaps


@dataclass
class ChampionChallenger:
    champion: str | None = None
    champion_reason: str = ""
    challengers: list[str] = field(default_factory=list)
    unrated: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "champion": self.champion,
            "champion_reason": self.champion_reason,
            "challengers": self.challengers,
            "unrated": self.unrated,
            "note": (
                "designated from evidence on record, not assigned by hand; the "
                "champion is the only strategy with broker-verified positive PnL"
            ),
        }


def build_origins(events: Sequence[object]) -> dict[str, CandidateOrigin]:
    """Derive per-candidate lineage, including how many specs each task produced."""
    proposals: dict[str, dict] = {}
    task_counts: dict[str, int] = {}

    for event in events:
        if coerce.field_of(event, "event_type") != PROPOSAL:
            continue
        payload = coerce.field_dict(coerce.field_of(event, "payload"), "event.payload")
        strategy_id = coerce.field_str(
            payload.get("strategy_id") or coerce.field_of(event, "strategy_id"), "strategy_id"
        )
        if not strategy_id:
            continue
        task_id = coerce.field_str(coerce.field_of(event, "task_id"), "task_id")
        if task_id:
            task_counts[task_id] = task_counts.get(task_id, 0) + 1
        proposals[strategy_id] = {
            "task_id": task_id,
            "rationale": str(payload.get("rationale", "")),
            "source": str(payload.get("source", "")),
            "task_type": str(payload.get("task_type", "")),
            "observation": payload.get("recent_exploration_summary") or {},
        }

    origins: dict[str, CandidateOrigin] = {}
    for strategy_id, info in proposals.items():
        origins[strategy_id] = CandidateOrigin(
            strategy_id=strategy_id,
            task_id=info["task_id"],
            rationale=info["rationale"],
            source=info["source"],
            task_type=info["task_type"],
            observation=info["observation"],
            siblings_from_same_task=task_counts.get(info["task_id"], 1),
        )

    for event in events:
        event_type = coerce.field_str(coerce.field_of(event, "event_type"), "event_type")
        strategy_id = coerce.field_str(coerce.field_of(event, "strategy_id"), "strategy_id")
        payload = coerce.field_dict(coerce.field_of(event, "payload"), "event.payload")
        if event_type == ADMISSION and strategy_id in origins:
            origins[strategy_id].accepted = bool(payload.get("accepted"))
            origins[strategy_id].admission_reason = str(payload.get("reason", ""))
        elif event_type == VALIDATION and strategy_id in origins:
            origins[strategy_id].offline_verdict = str(payload.get("verdict", ""))
            origins[strategy_id].offline_scored = coerce.field_int(
                payload.get("scored"), "payload.scored"
            )
            origins[strategy_id].offline_good_hold_ratio = coerce.field_float(
                payload.get("good_hold_ratio"), "payload.good_hold_ratio"
            )
        elif event_type == LIFECYCLE and strategy_id in origins:
            if payload.get("phase") == "applied" and payload.get("new_lifecycle"):
                origins[strategy_id].lifecycle = str(payload["new_lifecycle"])

    return origins


def search_effort(origins: Iterable[CandidateOrigin]) -> dict:
    """What it cost to find these candidates.

    Reported because the objective forbids letting an unexamined search drive
    learning: if a task produced four variants, the surviving one was chosen from
    four, and a number derived from it inherits that selection.
    """
    items = list(origins)
    per_task: dict[str, int] = {}
    for origin in items:
        if origin.task_id:
            per_task[origin.task_id] = per_task.get(origin.task_id, 0) + 1
    searched = {t: n for t, n in per_task.items() if n > 1}
    return {
        "candidates": len(items),
        "distinct_tasks": len(per_task),
        "proposals": sum(per_task.values()),
        "tasks_that_searched": len(searched),
        "max_variants_from_one_task": max(per_task.values()) if per_task else 0,
        "searched_tasks": dict(sorted(searched.items(), key=lambda kv: -kv[1])[:10]),
    }


def designate_champion(
    strategies: Iterable[object],
    results_by_id: dict[str, object] | None = None,
    origins: dict[str, CandidateOrigin] | None = None,
) -> ChampionChallenger:
    """Name the strategy everything else is measured against, from evidence.

    The champion is the only strategy with **broker-verified positive realized PnL**.
    That is a high bar on purpose. The alternative - champion by cycles, or by an
    in-sample backtest, or by the LLM's opinion of which strategy is best - would be
    choosing a benchmark on the same unproven evidence the system already distrusts.
    With nothing verified positive, `champion` stays `None`, and that is the honest
    answer: there is no champion, so nothing is being compared against anything.
    """
    outcomes = ChampionChallenger()
    results_by_id = results_by_id or {}
    verified: list[tuple[float, str]] = []

    for spec in strategies:
        strategy_id = coerce.field_str(coerce.field_of(spec, "strategy_id"), "strategy_id")
        lifecycle = coerce.field_str(coerce.field_of(spec, "lifecycle"), "lifecycle")
        result = results_by_id.get(strategy_id)
        pnl = coerce.field_float(
            coerce.field_of(result, "realized_pnl") if result is not None else None,
            f"{strategy_id}.realized_pnl",
        )
        evidence = coerce.field_str(
            coerce.field_of(result, "pnl_evidence") if result is not None else None,
            f"{strategy_id}.pnl_evidence",
        )
        origin = (origins or {}).get(strategy_id)

        if (
            evidence == "broker_strategy_closed_lot_pnl_verified"
            and pnl is not None
            and pnl > 0
            and lifecycle == "ACTIVE"
        ):
            verified.append((float(pnl), strategy_id))
        elif lifecycle in {"PROBATION"} or (origin and origin.lifecycle == "PROBATION"):
            outcomes.challengers.append(strategy_id)
        else:
            outcomes.unrated.append(strategy_id)

    if verified:
        verified.sort(reverse=True)
        # "the only such strategy" was the first wording, and it was false: two
        # ACTIVE strategies carry broker-verified positive PnL. A health report that
        # overstates its own case is the same defect as one that understates it.
        outcomes.champion = verified[0][1]
        if len(verified) == 1:
            qualifier = "the only strategy on record with it"
        else:
            qualifier = (
                f"the highest of {len(verified)} ACTIVE strategies with it "
                f"(next: {verified[1][1]} at {verified[1][0]:+.2f})"
            )
        outcomes.champion_reason = (
            f"broker-verified realized PnL {verified[0][0]:+.2f} across a closed "
            f"lot; {qualifier}"
        )
    else:
        outcomes.champion_reason = (
            "none: no strategy has broker-verified positive realized PnL, so there "
            "is no evidence-based benchmark and challengers have nothing to be "
            "measured against yet"
        )
    outcomes.challengers.sort()
    outcomes.unrated.sort()
    return outcomes
