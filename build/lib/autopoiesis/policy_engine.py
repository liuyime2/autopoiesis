from __future__ import annotations

from autopoiesis import regime
from autopoiesis.knowledge_library import KnowledgeLibrary, lesson_key
from autopoiesis.models import DataSnapshot, TradeDecision
from autopoiesis.reflection_memory import ReflectionMemory
from autopoiesis.strategy_engine import StrategyExecutor, StrategyLibrary, StrategySelector


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
        served_provider=None,
        market_history=None,
    ):
        self.strategy_library = strategy_library
        self.reflection_memory = reflection_memory
        self.knowledge_library = knowledge_library
        self.selector = selector or StrategySelector()
        self.executor = executor or StrategyExecutor()
        self.max_lessons = max_lessons
        #: The daemon's own monotone cycle count, used for the incumbent cadence.
        #: `select()` cannot derive it: the only count it can see is the one in
        #: `reflection.json`, which is a 30-minute-old snapshot, so a cadence measured
        #: against it fires in whole-window batches rather than one cycle in five.
        self.served_provider = served_provider
        #: Recent journal records, so a RULE strategy reads the same history on the fallback
        #: path as on the LLM path.
        self.market_history = market_history

    def served(self) -> int | None:
        """The daemon's current cycle count, or `None` to let the selector derive it."""
        if self.served_provider is None:
            return None
        try:
            return int(self.served_provider())
        except (TypeError, ValueError):
            return None

    def decide_snapshot(self, snapshot: DataSnapshot) -> TradeDecision:
        strategies = self.strategy_library.list()
        results = []
        if self.reflection_memory is not None:
            reflection = self.reflection_memory.load()
            if reflection is not None:
                results = self.reflection_memory.strategy_results(reflection)

        # The same arguments the LLM path selects with, so the fallback cannot serve a
        # different strategy than the model would have been asked about. It omitted the
        # price, so coverage was judged on paper here and at the real price there.
        strategy = self.selector.select(
            strategies, results, last_price=snapshot.last_price, served=self.served()
        )
        if strategy is None:
            return TradeDecision(
                # Named so a fallback decision is never confused with a model
                # decision, and so the two can be compared.
                model="fallback_policy_engine",
                symbol=snapshot.symbol,
                action="HOLD",
                quantity=0,
                confidence=0.0,
                rationale="policy engine found no enabled admitted strategy",
            )
        prices: list[float] = []
        if self.market_history is not None:
            try:
                prices = regime.prices_before(self.market_history(), snapshot.symbol, snapshot.timestamp)
            except (OSError, ValueError):
                prices = []
        decision = self.executor.decide(strategy, snapshot, prices)
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
        """Admitted lessons to show the decision maker.

        There is no strategy-relevance filter here any more, and there never was a
        working one. It tested `strategy_id in artifact.source_refs or
        strategy_id in artifact.tags`, but the producer puts cycle UUIDs in
        source_refs and generic words ("exploration", "probation", "lifecycle") in
        tags - never a strategy id. The condition could not be true, so `relevant`
        was always empty and a fallback returned the first N LESSON artifacts to
        *every* decision regardless of strategy. Nine of the eleven admitted
        artifacts were variants of one sentence, so what actually reached the model
        was the same five near-identical lines on every cycle.

        Keeping a filter that reads as strategy-scoped while being a no-op is worse
        than not having one, so it is gone. The strategy_id argument is retained
        because callers pass it and because it is the obvious place to reintroduce
        real scoping once lessons are actually derived per strategy - which is a
        research-layer change, not something to fake here.

        Best-effort by design: a lesson is context, not a hard input, so a corrupt
        artifact must never be able to block a trading cycle.
        """
        del strategy_id  # no working relevance test exists; see the docstring
        if self.knowledge_library is None:
            return []
        try:
            artifacts = self.knowledge_library.list(status="ACCEPTED")
        except Exception:
            return []
        lessons = [a for a in artifacts if a.artifact_type == "LESSON"]
        # Deduplicate before truncating, not after. Slicing first meant the five
        # lesson slots were filled by the first five artifacts in library order,
        # and this library's order is dominated by one statement - so the decision
        # prompt carried the same sentence five times and learned one thing from a
        # five-slot budget. 10 of the 11 accepted artifacts share a single answer.
        # The slot is meant to buy distinct context; filling it with copies buys
        # nothing and costs prompt budget on every cycle.
        #
        # Keyed with the figures masked (`lesson_key`), keeping the newest copy, so a lesson
        # re-derived with fresh numbers is one lesson showing its current numbers.
        newest: dict[str, object] = {}
        for artifact in sorted(lessons, key=lambda a: a.created_at, reverse=True):
            key = lesson_key(artifact)
            if key and key not in newest:
                newest[key] = artifact
        return list(newest.values())[: self.max_lessons]
