
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from min_agent.models import DataSnapshot, TradeDecision
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_engine import StrategyLibrary
from min_agent.knowledge_library import KnowledgeLibrary


class ToolType(Enum):
    ANALYZE_MARKET = "analyze_market"
    TRADE = "trade"
    REFLECT = "reflect"
    LEARN_LESSON = "learn_lesson"
    CREATE_STRATEGY = "create_strategy"
    OPTIMIZE_STRATEGY = "optimize_strategy"
    RECONCILE = "reconcile"
    SEARCH_TOOL = "search_tool"


@dataclass
class Tool:
    name: str
    tool_type: ToolType
    description: str
    handler: Callable[..., Any]
    available: bool = True


@dataclass
class ReasoningStep:
    thought: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None


@dataclass
class ReasoningResult:
    steps: list[ReasoningStep]
    decision: TradeDecision
    lessons_learned: list[str]
    tool_used: Optional[str] = None


class ReasoningEngine:
    """
    React 风格的推理引擎：
    - Reasoning（思考）→ Tool Selection（选工具）→ Action（行动）→ Learn（学习）
    """

    def __init__(
        self,
        *,
        strategy_library: StrategyLibrary,
        knowledge_library: KnowledgeLibrary,
        reflection_memory: ReflectionMemory | None = None,
    ):
        self.strategy_library = strategy_library
        self.knowledge_library = knowledge_library
        self.reflection_memory = reflection_memory
        self._tools: dict[str, Tool] = {}
        self._register_core_tools()

    def _register_core_tools(self):
        """注册核心工具"""
        self._tools = {
            "analyze_market": Tool(
                name="analyze_market",
                tool_type=ToolType.ANALYZE_MARKET,
                description="分析当前市场数据和趋势",
                handler=self._analyze_market,
            ),
            "trade": Tool(
                name="trade",
                tool_type=ToolType.TRADE,
                description="执行交易操作 (BUY/SELL/HOLD)",
                handler=self._trade,
            ),
            "reflect": Tool(
                name="reflect",
                tool_type=ToolType.REFLECT,
                description="回顾历史表现，总结经验",
                handler=self._reflect,
            ),
            "learn_lesson": Tool(
                name="learn_lesson",
                tool_type=ToolType.LEARN_LESSON,
                description="从当前情况中学习，生成 LESSON",
                handler=self._learn_lesson,
            ),
            "create_strategy": Tool(
                name="create_strategy",
                tool_type=ToolType.CREATE_STRATEGY,
                description="创建新的交易策略",
                handler=self._create_strategy,
            ),
            "optimize_strategy": Tool(
                name="optimize_strategy",
                tool_type=ToolType.OPTIMIZE_STRATEGY,
                description="优化现有策略参数",
                handler=self._optimize_strategy,
            ),
            "reconcile": Tool(
                name="reconcile",
                tool_type=ToolType.RECONCILE,
                description="检查 broker 对账情况",
                handler=self._reconcile,
            ),
            "search_tool": Tool(
                name="search_tool",
                tool_type=ToolType.SEARCH_TOOL,
                description="搜索/请求创建缺失的工具",
                handler=self._search_tool,
            ),
        }

    def reason_and_decide(
        self,
        snapshot: DataSnapshot,
        *,
        market_open: bool,
        journal_context: Optional[dict[str, Any]] = None,
    ) -> ReasoningResult:
        """
        核心推理循环：
        1. Observe（观察）→ 2. Reason（推理）→ 3. Select Tool（选工具）→ 4. Act（行动）
        """
        steps: list[ReasoningStep] = []
        lessons_learned: list[str] = []

        # ===== 步骤 1：观察与分析 =====
        steps.append(ReasoningStep(
            thought=f"观察市场：{snapshot.symbol} 价格 {snapshot.last_price}，"
                   f"市场状态：{'开盘' if market_open else '收盘'}"
        ))

        # ===== 步骤 2：加载知识与反思 =====
        relevant_knowledge = self._load_relevant_knowledge(snapshot)
        if relevant_knowledge:
            steps.append(ReasoningStep(
                thought=f"回忆起 {len(relevant_knowledge)} 条相关知识经验"
            ))

        strategy_scores = self._load_strategy_scores()
        if strategy_scores:
            steps.append(ReasoningStep(
                thought=f"策略表现：{json.dumps(strategy_scores, ensure_ascii=False)}"
            ))

        # ===== 步骤 3：推理分支 =====
        if market_open:
            return self._reason_market_open(
                snapshot, steps, lessons_learned, journal_context
            )
        else:
            return self._reason_market_closed(
                snapshot, steps, lessons_learned, journal_context
            )

    def _reason_market_open(
        self,
        snapshot: DataSnapshot,
        steps: list[ReasoningStep],
        lessons_learned: list[str],
        journal_context: Optional[dict[str, Any]],
    ) -> ReasoningResult:
        """开盘时的推理逻辑：先分析，再交易决策"""
        steps.append(ReasoningStep(thought="市场开盘中，需要快速反应决策"))

        # 子步骤 1：使用 analyze_market 工具
        analysis = self._tools["analyze_market"].handler(snapshot)
        steps.append(ReasoningStep(
            thought=f"分析结果：{analysis}",
            tool_name="analyze_market",
        ))

        # 子步骤 2：选择策略并决策
        strategies = self.strategy_library.list()
        active_strategies = [s for s in strategies if s.enabled and s.lifecycle in {"ACTIVE", "PROBATION"}]

        if not active_strategies:
            steps.append(ReasoningStep(
                thought="没有活跃策略，创建新策略？",
                tool_name="create_strategy",
            ))
            decision = TradeDecision(
                symbol=snapshot.symbol,
                action="HOLD",
                quantity=0,
                confidence=0.0,
                rationale="no active strategies available",
            )
        else:
            # 使用策略执行决策
            from min_agent.strategy_engine import StrategySelector, StrategyExecutor
            selector = StrategySelector()
            executor = StrategyExecutor()

            reflection = self.reflection_memory.load() if self.reflection_memory else None
            results = self.reflection_memory.strategy_results(reflection) if reflection else []

            strategy = selector.select(active_strategies, results)
            if strategy:
                decision = executor.decide(strategy, snapshot)
                steps.append(ReasoningStep(
                    thought=f"选择策略 {strategy.strategy_id}，决策：{decision.action} {decision.quantity}",
                    tool_name="trade",
                    tool_args={"action": decision.action, "quantity": decision.quantity},
                ))
            else:
                decision = TradeDecision(
                    symbol=snapshot.symbol,
                    action="HOLD",
                    quantity=0,
                    confidence=0.0,
                    rationale="no strategy selected",
                )

        return ReasoningResult(
            steps=steps,
            decision=decision,
            lessons_learned=lessons_learned,
            tool_used="trade",
        )

    def _reason_market_closed(
        self,
        snapshot: DataSnapshot,
        steps: list[ReasoningStep],
        lessons_learned: list[str],
        journal_context: Optional[dict[str, Any]],
    ) -> ReasoningResult:
        """收盘时的推理逻辑：学习、总结、优化"""
        steps.append(ReasoningStep(thought="市场收盘，进入学习/优化模式"))

        # 子步骤 1：反思
        if self.reflection_memory:
            steps.append(ReasoningStep(
                thought="回顾近期表现，进行反思",
                tool_name="reflect",
            ))

        # 子步骤 2：学习 lessons
        lessons = self._tools["learn_lesson"].handler(snapshot)
        if lessons:
            steps.append(ReasoningStep(
                thought=f"总结出 {len(lessons)} 条经验教训",
                tool_name="learn_lesson",
            ))
            lessons_learned.extend(lessons)

        # 子步骤 3：检查是否需要创建/优化策略
        strategies = self.strategy_library.list()
        if len([s for s in strategies if s.lifecycle in {"ACTIVE", "PROBATION"}]) < 3:
            steps.append(ReasoningStep(
                thought="活跃策略不足，考虑创建新策略",
                tool_name="create_strategy",
            ))

        # 收盘时决策始终是 HOLD
        decision = TradeDecision(
            symbol=snapshot.symbol,
            action="HOLD",
            quantity=0,
            confidence=0.0,
            rationale="market closed - learning mode",
        )

        return ReasoningResult(
            steps=steps,
            decision=decision,
            lessons_learned=lessons_learned,
            tool_used="learn_lesson",
        )

    def _analyze_market(self, snapshot: DataSnapshot) -> dict[str, Any]:
        """分析市场工具"""
        return {
            "symbol": snapshot.symbol,
            "price": snapshot.last_price,
            "timestamp": snapshot.timestamp.isoformat() if snapshot.timestamp else None,
        }

    def _trade(self, action: str, quantity: float) -> bool:
        """交易工具"""
        return True

    def _reflect(self, window: int = 50) -> dict[str, Any]:
        """反思工具"""
        if self.reflection_memory:
            reflection = self.reflection_memory.load()
            if reflection:
                return {
                    "window_cycles": reflection.window_cycles,
                    "strategy_scores": reflection.strategy_scores,
                }
        return {}

    def _learn_lesson(self, snapshot: DataSnapshot) -> list[str]:
        """学习 lesson 工具"""
        lessons = []

        # 从知识库中查看已有的
        knowledge = self.knowledge_library.list()
        no_exploration_lessons = [
            k for k in knowledge
            if k.artifact_type == "LESSON" and "no-exploration" in k.artifact_id
        ]

        if no_exploration_lessons:
            lessons.append("已有的 no-exploration 经验提醒我们需要持续探索")

        return lessons

    def _create_strategy(self, spec: dict[str, Any]) -> bool:
        """创建策略工具"""
        return True

    def _optimize_strategy(self, strategy_id: str, params: dict[str, Any]) -> bool:
        """优化策略工具"""
        return True

    def _reconcile(self) -> dict[str, Any]:
        """对账工具"""
        return {"matched": True}

    def _search_tool(self, purpose: str) -> Optional[Tool]:
        """搜索/请求创建缺失的工具"""
        # 先搜索现有工具
        for tool in self._tools.values():
            if purpose.lower() in tool.description.lower():
                return tool

        # 如果没有，返回 None（表示需要创建）
        return None

    def _load_relevant_knowledge(self, snapshot: DataSnapshot) -> list[Any]:
        """加载相关知识"""
        knowledge = self.knowledge_library.list()
        # 简单过滤：与当前 symbol 相关的
        relevant = [
            k for k in knowledge
            if k.artifact_type == "LESSON"
        ]
        return relevant[-5:] if relevant else []

    def _load_strategy_scores(self) -> dict[str, float]:
        """加载策略评分"""
        if self.reflection_memory:
            reflection = self.reflection_memory.load()
            if reflection:
                return reflection.strategy_scores
        return {}

    def register_tool(self, tool: Tool):
        """注册新工具（动态扩展）"""
        self._tools[tool.name] = tool

    def list_tools(self) -> list[Tool]:
        """列出所有可用工具"""
        return list(self._tools.values())
