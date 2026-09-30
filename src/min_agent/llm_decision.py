from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

import requests

from min_agent.models import DataSnapshot, KnowledgeArtifact, TradeDecision

Transport = Callable[[str, dict[str, Any], int], dict[str, Any]]


def parse_decision_json(text: str, *, model_name: str | None = None) -> TradeDecision:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")

    payload = json.loads(text[start : end + 1])
    decision = TradeDecision.model_validate(payload)
    # Stamped at the point the model is called rather than inferred later from config.
    # The model can be swapped between the call and the journal write, and a decision
    # attributed to the wrong model version is worse than one with no attribution.
    if model_name and decision.model is None:
        return decision.model_copy(update={"model": model_name})
    return decision


Transport = Callable[[str, Mapping[str, Any], int], Mapping[str, Any]]

#: Two ollama call sites, two different jobs, two different context budgets.
#:
#: These were once one number applied to both, and a test asserted they stayed
#: equal. That test passed while the curriculum was returning empty responses,
#: because "both call sites pin a window" was never the property that mattered -
#: "both call sites can finish their own prompt and answer" was. A shared constant
#: is only correct while two workloads happen to agree; these never did.
#:
#: Measured against the live model on the real 27-strategy context, same prompt
#: both times:
#:
#:   num_ctx= 4096  prompt=3441 eval= 652  used=4093  done=length  body=""
#:   num_ctx= 6144  prompt=3441 eval=1143  used=4584  done=stop    body=valid
#:   num_ctx= 8192  prompt=3441 eval=1452  used=4893  done=stop    body=valid
#:
#: 4096 is truncation, not a slow answer: 655 tokens of room for a reply that
#: needs ~1200. 8192 is chosen over 6144 because the margin must survive the
#: context growing, not merely the context that happened to exist today.
DECISION_NUM_CTX = 4096
#: Worst case must be prompt + answer + growth. 3441 measured prompt, 1452
#: measured answer, 3299 spare at 8192. A chars/3.6 estimate said 2838 and was
#: 19% low, because JSON punctuation and short keys are cheap per character;
#: budgets are set from token counts, never from character guesses.
CURRICULUM_NUM_CTX = 8192
#: Measured 118.4s at 6144, 141.9s at 8192, 141.8s at 12288 on the same prompt.
#: The engine's default 120s timeout sat inside that range, so simply fixing the
#: window would have turned a truncation into a ReadTimeout instead. The
#: curriculum is an off-hours job, not the 5-minute trading loop, so paying two
#: minutes for a correct answer is the right trade. The decision path keeps 120s
#: because it answers in ~12s.
CURRICULUM_TIMEOUT_S = 600


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
            # qwen3.8:27b is a hybrid reasoning model: it emits a "thinking" block
            # before the answer. Measured on a real context, that block is 358-814
            # tokens of restating RSI bands at 37 tok/s - 4x the decision itself,
            # for a number the model then rounds to the same HOLD either way.
            # /no_think drops it: 23.2s -> 12.7s with the same action and
            # confidence. It is a latency fix, not a reasoning-quality claim, so
            # the two variants are compared below rather than assumed equal.
            "options": {
                "temperature": 0.1,
                # DECISION_NUM_CTX, not the server's 32768. The largest context the
                # engine can build is ~253 tokens in, ~814 tokens of answer
                # measured, so 4096 leaves 3.5x headroom. KV cache is allocated for
                # the full window even when unused: at 32768 the same call took
                # 17.8s against 4.6s at 4096.
                #
                # This value is for the DECISION prompt only. It was briefly used
                # for the curriculum prompt too, on the assumption that one window
                # should serve both call sites. That assumption was wrong by 13x:
                # the decision context is ~253 tokens, the curriculum context
                # measures 3441. At 4096 the curriculum had 655 tokens left to
                # answer, needed ~1202, and returned done_reason=length with an
                # empty body - the exact "constrained response contained no JSON
                # object" failure. See CURRICULUM_NUM_CTX below.
                "num_ctx": DECISION_NUM_CTX,
            },
        }
        response = self.transport(f"{self.base_url}/api/generate", payload, self.timeout)
        decision = parse_decision_json(
            str(response.get("response", "")), model_name=self.model,
        )
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
                # Coverage is evaluated at the real price, not on paper. A
                # TREND_FOLLOW nominally covers both sides but only emits the one
                # its reference price points at, and the live one is anchored to a
                # June reference of 735.01 while SPY trades at 767 - so counting it
                # as an exit let the library report SELL as covered while being
                # unable to sell.
                snapshot.last_price,
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
            # What the limits mean right now. The model was told the cap but never
            # the total it is measured against, so it could not know that this
            # account holds ~$76k against a $20k cap and that every BUY would be
            # rejected by Guardian - it kept proposing BUY and Guardian kept saying
            # "total exposure exceeds hard limit". This is arithmetic on the broker
            # positions already in the context, not a hint to trade.
            "exposure": self._exposure(snapshot),
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

    def _exposure(self, snapshot: DataSnapshot) -> dict[str, Any]:
        """The exposure arithmetic Guardian will actually apply, plus the account total.

        It has to match Guardian exactly. When the cap was measured over every
        position, the model was told a BUY was blocked by a $37k bond allocation it
        had no mandate over, and it correctly held - against a limit that forbade
        every trade. Guardian now measures the allowlist book, because that is the
        only exposure the agent can increase, so the context has to say the same
        thing. The account total is still reported, clearly separated, so the model
        can see the rest of the book without it being charged against its limit.
        """
        total = sum(pos.market_value for pos in snapshot.positions)
        allowlist = self.risk_limits.get("allowlist")
        names = set(allowlist) if isinstance(allowlist, (list, tuple, set)) else None
        mandate = sum(
            pos.market_value
            for pos in snapshot.positions
            if names is None or pos.symbol in names
        )
        cap = self.risk_limits.get("max_total_exposure")
        position_value = snapshot.last_price
        held_here = next(
            (pos for pos in snapshot.positions if pos.symbol == snapshot.symbol), None
        )
        out: dict[str, Any] = {
            "agent_book_value": round(mandate, 2),
            "account_total_value": round(total, 2),
            "position_count": len(snapshot.positions),
        }
        if isinstance(cap, (int, float)) and cap > 0:
            out["max_total_exposure"] = cap
            out["exposure_headroom"] = round(max(0.0, cap - mandate), 2)
            # Guardian's exact test for a BUY, over the allowlist book.
            out["buy_blocked_by_exposure_limit"] = mandate + position_value > cap
        account_cap = self.risk_limits.get("max_account_value")
        if isinstance(account_cap, (int, float)) and account_cap > 0:
            out["buy_blocked_by_account_limit"] = total + position_value > account_cap
        if held_here is not None:
            out["position_in_this_symbol"] = held_here.quantity
        return out

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
