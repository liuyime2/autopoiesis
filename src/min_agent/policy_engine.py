from __future__ import annotations

from min_agent.models import DataSnapshot, TradeDecision
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_engine import StrategyExecutor, StrategyLibrary, StrategySelector


class PolicyEngine:
    def __init__(
        self,
        *,
        strategy_library: StrategyLibrary,
        reflection_memory: ReflectionMemory | None = None,
        selector: StrategySelector | None = None,
        executor: StrategyExecutor | None = None,
    ):
        self.strategy_library = strategy_library
        self.reflection_memory = reflection_memory
        self.selector = selector or StrategySelector()
        self.executor = executor or StrategyExecutor()

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
        return self.executor.decide(strategy, snapshot)
