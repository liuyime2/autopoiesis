from __future__ import annotations

from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import DataSnapshot, TradeDecision
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_engine import StrategyExecutor, StrategyLibrary, StrategySelector


class PolicyEngine:
    def __init__(
        self,
        *,
        strategy_library: StrategyLibrary,
        reflection_memory: ReflectionMemory | None = None,
        knowledge_library: KnowledgeLibrary | None = None,
        selector: StrategySelector | None = None,
        executor: StrategyExecutor | None = None,
        max_lessons: int = 5,
    ):
        self.strategy_library = strategy_library
        self.reflection_memory = reflection_memory
        self.knowledge_library = knowledge_library
        self.selector = selector or StrategySelector()
        self.executor = executor or StrategyExecutor()
        self.max_lessons = max_lessons

    def decide_snapshot(self, snapshot: DataSnapshot) -> TradeDecision:
        strategies = self.strategy_library.list()
        results = []
        if self.reflection_memory is not None:
            reflection = self.reflection_memory.load()
            if reflection is not None:
                results = self.reflection_memory.strategy_results(reflection)

        strategy = self.selector.select(strategies, results)
        if strategy is None:
            return TradeDecision(
                symbol=snapshot.symbol,
                action="HOLD",
                quantity=0,
                confidence=0.0,
                rationale="policy engine found no enabled admitted strategy",
            )
        decision = self.executor.decide(strategy, snapshot)
        try:
            lessons = self.relevant_lessons(strategy.strategy_id)
        except Exception:
            # A lesson is context, not a hard input. Nothing here is allowed to
            # take a trading cycle down with it.
            lessons = []
        if lessons:
            # The knowledge library used to be write-only: load/list had zero
            # call sites, so no lesson the agent admitted had ever influenced a
            # decision. The rationale is the audit surface, so the lessons that
            # informed this decision are recorded on it.
            decision = decision.model_copy(
                update={
                    "rationale": (
                        f"{decision.rationale} | lessons: "
                        + "; ".join(lesson.summary for lesson in lessons)
                    )
                }
            )
        return decision

    def relevant_lessons(self, strategy_id: str) -> list:
        """Admitted lessons that bear on this strategy.

        Best-effort by design: a lesson is context, not a hard input, so a
        corrupt artifact must never be able to block a trading cycle.
        """
        if self.knowledge_library is None:
            return []
        try:
            artifacts = self.knowledge_library.list(status="ACCEPTED")
        except Exception:
            return []
        relevant = [
            artifact
            for artifact in artifacts
            if strategy_id in (artifact.source_refs or ()) or strategy_id in (artifact.tags or ())
        ]
        if not relevant:
            relevant = [a for a in artifacts if a.artifact_type == "LESSON"]
        return relevant[: self.max_lessons]
