from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

import requests

from min_agent.models import DataSnapshot, KnowledgeArtifact, TradeDecision

Transport = Callable[[str, dict[str, Any], int], dict[str, Any]]


def parse_decision_json(text: str) -> TradeDecision:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")

    payload = json.loads(text[start : end + 1])
    return TradeDecision.model_validate(payload)


class OllamaDecisionEngine:
    """Calls the local model and returns its structured decision.

    Guardian remains the final authority on whatever this produces; nothing here
    can widen a risk limit or leave paper trading.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        gpu_devices: str,
        transport: Transport | None = None,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.gpu_devices = gpu_devices
        self.transport = transport or self._requests_transport
        self.timeout = timeout

    def launch_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = self.gpu_devices
        return env

    def decide(self, context: dict[str, Any]) -> TradeDecision:
        prompt = (
            "You are a paper-trading decision engine. Return exactly one JSON object with keys: "
            "symbol, action, quantity, confidence, rationale, hold_reason. "
            "action must be BUY, SELL, or HOLD. "
            "quantity must be 0 when action is HOLD, and a positive integer otherwise. "
            "Base every number on the context you are given; do not invent prices. "
            "If the context does not support a trade, return HOLD and set hold_reason. "
            "hold_reason must be exactly one of: no_signal, risk_limit_near, market_uncertain, "
            "await_confirmation, other. Use risk_limit_near only when a risk figure in the "
            "context is what stopped you. The risk limits in the context are enforced "
            "independently downstream; state your reason, do not act as a second risk check. "
            "Do not include any order outside the supplied symbol and risk context.\n"
            f"Context: {json.dumps(context, sort_keys=True)}"
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # Schema-constrained, not format="json". Measured against
            # deepseek-r1:8b on the curriculum task this was 5x faster and 4x
            # smaller than the unconstrained form, because the model no longer
            # has to search for a valid shape - and it cannot emit a decision
            # missing required fields.
            "format": TradeDecision.model_json_schema(),
            "options": {"temperature": 0.1},
        }
        response = self.transport(f"{self.base_url}/api/generate", payload, self.timeout)
        decision = parse_decision_json(str(response.get("response", "")))
        return decision.model_copy(update={"decision_source": "llm"})

    @staticmethod
    def _requests_transport(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()


class HybridDecisionEngine:
    """The LLM proposes; the deterministic policy engine is the safety net.

    Previously the daemon injected `PolicyEngine` as the decision engine, so
    `OllamaDecisionEngine.decide` was unreachable outside `--once`: every trade
    decision was a static JSON lookup with no market reasoning at all, while the
    model was consulted only about curriculum.

    Here the LLM is asked first. Whatever it returns is still reviewed by
    Guardian. If the model errors, times out, or emits something unparseable, the
    policy engine decides instead and the decision is labelled
    `fallback_policy_engine` - recorded, not disguised.
    """

    def __init__(
        self,
        *,
        llm: OllamaDecisionEngine,
        policy_engine,
        lessons: Callable[[str], list[KnowledgeArtifact]] | None = None,
        max_lessons: int = 5,
        risk_limits: dict[str, float] | None = None,
        cost_basis: Callable[[], dict[str, dict[str, object]]] | None = None,
    ):
        self.llm = llm
        self.policy_engine = policy_engine
        self.lessons = lessons
        self.max_lessons = max_lessons
        # The prompt tells the model to use hold_reason=risk_limit_near "only when
        # a risk figure in the context is what stopped you", but the context
        # carried no risk figures at all, so that reason could never be truthful
        # and the model could not tell how much room it had.
        self.risk_limits = dict(risk_limits or {})
        # Without a cost basis the model cannot tell profit from loss. Every price
        # in it is broker-reported, from confirmed fills.
        self.cost_basis = cost_basis

    def decide_snapshot(self, snapshot: DataSnapshot) -> TradeDecision:
        context = self._context(snapshot)
        try:
            decision = self.llm.decide(context)
        except Exception as exc:
            return self._fallback(snapshot, f"{type(exc).__name__}: {exc}")
        # A model that names a different symbol is not deciding about this
        # instrument; fall back rather than let Guardian reject every cycle.
        if decision.symbol != snapshot.symbol:
            return self._fallback(snapshot, f"llm returned {decision.symbol}, not {snapshot.symbol}")
        # Stamp provenance here rather than trusting the inner engine to have
        # done it, so a decision that came from the model can never be recorded
        # as anything else.
        #
        # The selected strategy is stamped here too, for the same reason. The
        # decision schema never asked the model for a strategy_id, so all 908
        # journaled cycles carried strategy_id=None: per-strategy metrics could
        # not accumulate for the LLM path at all, and no lifecycle review could
        # ever be about a decision the model actually made.
        selected = context.get("selected_strategy_id")
        update: dict[str, Any] = {"decision_source": "llm"}
        if isinstance(selected, str) and selected and not decision.strategy_id:
            update["strategy_id"] = selected
        return decision.model_copy(update=update)

    def _fallback(self, snapshot: DataSnapshot, reason: str) -> TradeDecision:
        decision = self.policy_engine.decide_snapshot(snapshot)
        return decision.model_copy(
            update={
                "decision_source": "fallback_policy_engine",
                "rationale": f"{decision.rationale} | llm_unavailable: {reason}",
            }
        )

    def _context(self, snapshot: DataSnapshot) -> dict[str, Any]:
        # The selected strategy and the lesson set both come from the policy
        # engine. If that lookup fails the LLM still gets a decision: a degraded
        # context is better than no decision, and Guardian reviews the result
        # either way.
        strategy_id = None
        selected_spec = None
        try:
            selected = self.policy_engine.selector.select(
                self.policy_engine.strategy_library.list(),
                self._results(),
            )
            if selected is not None:
                strategy_id = selected.strategy_id
                selected_spec = next(
                    (
                        strategy
                        for strategy in self.policy_engine.strategy_library.list()
                        if strategy.strategy_id == selected.strategy_id
                    ),
                    None,
                )
        except Exception:
            strategy_id = None
        context: dict[str, Any] = {
            "symbol": snapshot.symbol,
            "timestamp": snapshot.timestamp.isoformat(),
            "market_open": snapshot.market_open,
            "last_price": snapshot.last_price,
            "equity": snapshot.account.equity,
            "cash": snapshot.account.cash,
            "buying_power": snapshot.account.buying_power,
            "daily_loss": snapshot.account.daily_loss,
            "day_start_equity": snapshot.day_start_equity,
            "positions": [
                {"symbol": pos.symbol, "quantity": pos.quantity, "market_value": pos.market_value}
                for pos in snapshot.positions
            ],
            "open_orders": [
                {"symbol": o.symbol, "side": o.side, "quantity": o.quantity, "status": o.status}
                for o in snapshot.open_orders
            ],
            "selected_strategy_id": strategy_id,
            # The mandate the model is deciding under. It was previously given an
            # opaque id and nothing else, so it could not tell that the strategy it
            # was asked to act for was a SELL.
            "selected_strategy": (
                {
                    "strategy_id": selected_spec.strategy_id,
                    "kind": selected_spec.kind,
                    "parameters": dict(selected_spec.parameters),
                    "max_position_value": selected_spec.max_position_value,
                    "lifecycle": selected_spec.lifecycle,
                }
                if selected_spec is not None
                else None
            ),
            "paper_only": True,
            "risk_limits": self.risk_limits,
        }
        held = next((pos for pos in snapshot.positions if pos.symbol == snapshot.symbol), None)
        lots: dict[str, dict[str, object]] = {}
        if self.cost_basis is not None:
            try:
                lots = self.cost_basis()
            except Exception:
                lots = {}
        if held is not None:
            context["sellable_quantity"] = int(held.quantity)
            lot = lots.get(snapshot.symbol)
            if isinstance(lot, Mapping) and isinstance(lot.get("average_price"), (int, float)):
                average = float(lot["average_price"])
                context["average_cost"] = round(average, 4)
                context["unrealized_pnl_per_share"] = round(snapshot.last_price - average, 4)
                context["unrealized_pnl"] = round(
                    (snapshot.last_price - average) * min(held.quantity, float(lot.get("quantity", 0) or 0)), 2
                )
        if lots:
            context["open_lots"] = lots
        context["lessons"] = [a.summary for a in self._lessons(strategy_id)]
        return context

    def _lessons(self, strategy_id: str | None) -> list[KnowledgeArtifact]:
        if self.lessons is None or strategy_id is None:
            return []
        try:
            return list(self.lessons(strategy_id))[: self.max_lessons]
        except Exception:
            return []

    def _results(self):
        engine = self.policy_engine
        if engine.reflection_memory is None:
            return []
        record = engine.reflection_memory.load()
        if record is None:
            return []
        return engine.reflection_memory.strategy_results(record)
