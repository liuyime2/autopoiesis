from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from typing import Any

import requests

from min_agent import coerce, regime
from min_agent.coerce import is_number
from min_agent.models import DataSnapshot, KnowledgeArtifact, TradeDecision


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

#: The decision engine's entire instruction, as data.
#:
#: This was a string literal inside `decide()`. That is the whole of the system's
#: self-representation: there was nowhere else it lived, so it could not be audited
#: without reading a method, diffed as a change to intent, or corrected without a code
#: change and a daemon restart.
#:
#: It also had a measurable hole. It spelled out the `action` enum, the `quantity` rule
#: and every `hold_reason` value - and never said that `confidence` must lie in [0, 1].
#: The JSON schema passed alongside it *does* carry `minimum: 0, maximum: 1`, so the
#: bound was available to constrained decoding, and 92 cycles still reached validation
#: with an out-of-range value: a numeric range does not survive into the decoding
#: grammar, so these words are the only place it can be enforced. Cost: 100 cycles lost
#: to a malformed `confidence` and 135 more to fail-closed HOLDs on invalid output -
#: about 20% of this project's entire decision history, every one of them a cycle the
#: market was open for.
#:
#: Every constraint the validator enforces is stated here in words. `tests/
#: min_agent/test_llm_decision.py` asserts that correspondence, so a field added to the
#: schema without a line here fails the build rather than costing 20% of the history.
DECISION_INSTRUCTION = (
    "You are a paper-trading decision engine. Return exactly one JSON object with keys: "
    "symbol, action, quantity, confidence, rationale, hold_reason, override_reason. "
    "action must be exactly one of BUY, SELL, HOLD, SHORT, COVER. "
    "SELL only reduces a long you hold; SHORT opens or adds to a short and COVER buys one "
    "back. Use SHORT or COVER only when risk_limits.shorts is not off. "
    "quantity must be the integer 0 when action is HOLD, and a positive integer otherwise. "
    "confidence must be a decimal number between 0 and 1 inclusive, written as a decimal "
    "like 0.65 and never as a percentage such as 65 and never above 1. "
    "Base every number on the context you are given; do not invent prices. "
    "If the context does not support a trade, return HOLD, set quantity to 0, and set "
    "hold_reason. "
    "hold_reason must be exactly one of: no_signal, risk_limit_near, market_uncertain, "
    "await_confirmation, other. Use risk_limit_near only when a risk figure in the "
    "context is what stopped you. The risk limits in the context are enforced "
    "independently downstream; state your reason, do not act as a second risk check. "
    "Do not include any order outside the supplied symbol and risk context. "
    "rule_decision in the context is what the selected strategy's own rule decided. It is "
    "the default: if your action differs from rule_decision.action, override_reason must "
    "say in one sentence what in the context makes the rule wrong here; otherwise set "
    "override_reason to null. An override without a reason is discarded. "
    "market in the context summarises recent prices from your own record - regime, trend, "
    "volatility and returns - and is description, not an instruction to trade."
)

#: The one transport contract: (prompt, context, num_ctx) -> the parsed body.
#: There was a second, identical-looking definition of this name further up the
#: file, differing only in dict-vs-Mapping. Python bound the later one and the
#: earlier one was unreachable, so the type readers saw was not the type that
#: ran. Only one definition now.
#:
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
        prompt = f"{DECISION_INSTRUCTION}\nContext: {json.dumps(context, sort_keys=True)}"
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
        risk_limits: dict[str, object] | None = None,
        cost_basis: Callable[[], dict[str, dict[str, object]]] | None = None,
        market_history: Callable[[], list] | None = None,
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
        #: Recent journal records, for the `market` block. The model was given one price and
        #: nothing about how it got there - no return, no volatility, no regime - so a rule's
        #: trend parameters and the model's override were both reasoning without a past.
        self.market_history = market_history
        #: The selected strategy's own decision for the snapshot `_context` last built.
        self._rule_decision: TradeDecision | None = None
        #: (shown, withheld) lesson ids for the snapshot `_context` last built.
        self._lesson_split: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())

    def decide_snapshot(self, snapshot: DataSnapshot) -> TradeDecision:
        context = self._context(snapshot)
        rule = self._rule_decision
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
        if rule is not None and decision.action != rule.action and not (decision.override_reason or "").strip():
            # The rule is the default and an override has to say why. Measured over 323
            # comparable cycles before this existed, 112 of 113 disagreements were the model
            # declining a trade the rule would have taken, with nothing recorded about why.
            return rule.model_copy(update={
                "decision_source": "policy_engine",
                "rule_action": rule.action,
                "lesson_ids": self._lesson_split[0],
                "lessons_withheld": self._lesson_split[1],
                "model": decision.model,
                "rationale": (
                    f"{rule.rationale} | llm proposed {decision.action} without an "
                    f"override_reason; rule taken"
                ),
            })
        update: dict[str, Any] = {
            "decision_source": "llm",
            "lesson_ids": self._lesson_split[0],
            "lessons_withheld": self._lesson_split[1],
        }
        if rule is not None:
            update["rule_action"] = rule.action
        if decision.action == (rule.action if rule is not None else decision.action):
            update["override_reason"] = None
        if isinstance(selected, str) and selected and not decision.strategy_id:
            update["strategy_id"] = selected
        return decision.model_copy(update=update)

    def _fallback(self, snapshot: DataSnapshot, reason: str) -> TradeDecision:
        decision = self.policy_engine.decide_snapshot(snapshot)
        return decision.model_copy(
            update={
                "decision_source": "fallback_policy_engine",
                # The fallback is the rule, so the rule's action is the action taken.
                "rule_action": decision.action,
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
        self._rule_decision = None
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
                # The daemon's own cycle count, not the one in reflection.json. That
                # file is rewritten every 30 minutes, so a cadence measured against it
                # advances once per window rather than once per cycle: measured live on
                # 2026-10-05 the incumbent was selected 0 times in 8 cycles because
                # `served` was frozen at 146 and `146 % 5 == 1` for the whole window.
                self._served(),
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
        executor = getattr(self.policy_engine, "executor", None)  # no executor, no rule to run
        if selected_spec is not None and executor is not None:
            try:
                self._rule_decision = executor.decide(
                    selected_spec, snapshot, self._prices_before(snapshot)
                )
            # A malformed parameter (KeyError), a value the decision schema refuses (pydantic's
            # ValidationError is a ValueError), or a wrong type - the ways a rule can fail.
            except (KeyError, ValueError, TypeError):
                # A rule that cannot be evaluated is no default; the model decides alone and
                # the cycle carries no rule_action, which is what the journal will say.
                self._rule_decision = None
        lots: dict[str, dict[str, object]] = {}
        if self.cost_basis is not None:
            try:
                lots = self.cost_basis()
            except Exception:
                lots = {}

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
            "rule_decision": (
                {"action": self._rule_decision.action, "quantity": self._rule_decision.quantity}
                if self._rule_decision is not None
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
            "exposure": self._exposure(snapshot, lots),
        }
        held = next((pos for pos in snapshot.positions if pos.symbol == snapshot.symbol), None)
        if held is not None:
            context["sellable_quantity"] = int(held.quantity)
            lot = lots.get(snapshot.symbol)
            if isinstance(lot, Mapping) and is_number(lot.get("average_price")):
                average = coerce.field_float_or(lot["average_price"], "average_price", 0.0)
                context["average_cost"] = round(average, 4)
                context["unrealized_pnl_per_share"] = round(snapshot.last_price - average, 4)
                context["unrealized_pnl"] = round(
                    (snapshot.last_price - average)
                    * min(held.quantity, coerce.field_float_or(lot.get("quantity"), "quantity", 0.0)),
                    2,
                )
        if lots:
            context["open_lots"] = lots
        offered = self._lessons(strategy_id)
        shown = [a for a in offered if self._show_lesson(snapshot, a.artifact_id)]
        self._lesson_split = (
            tuple(a.artifact_id for a in shown),
            tuple(a.artifact_id for a in offered if a not in shown),
        )
        context["lessons"] = [a.summary for a in shown]
        self._fit_rule_to_limits(context)
        market = self._market(snapshot)
        if market is not None:
            context["market"] = market
        return context

    def _fit_rule_to_limits(self, context: dict[str, Any]) -> None:
        """Size the rule's BUY to what the Guardian would allow, before the model sees it.

        The rule caps an entry by its own order notional and knows nothing of the position
        already held, so on 2026-10-07 it proposed BUY on 39 of 43 paired cycles while the
        position cap left no room - and the model, correctly, overrode every one. Scored as
        pairs, those overrides credited the model with refusing orders that could never have
        filled. The exposure block holds the Guardian's own arithmetic, so the rule is held
        to it here: a BUY is cut to `shares_you_may_buy`, and with no room, or with an
        exposure limit already binding, it becomes HOLD. The rule's action is then what it
        could actually have done, which is the only fair thing to pair the model against.
        """
        rule = self._rule_decision
        exposure = context.get("exposure") or {}
        if rule is None or rule.action != "BUY" or not isinstance(exposure, Mapping):
            return
        room = exposure.get("shares_you_may_buy")
        blocked = bool(exposure.get("buy_blocked_by_exposure_limit")) or bool(
            exposure.get("buy_blocked_by_account_limit")
        )
        if not isinstance(room, int) or (room >= rule.quantity and not blocked):
            return
        if room > 0 and not blocked:
            fitted = rule.model_copy(update={
                "quantity": room,
                "rationale": f"{rule.rationale} | cut to {room} by the position limit",
            })
        else:
            fitted = TradeDecision(
                symbol=rule.symbol, action="HOLD", quantity=0, confidence=rule.confidence,
                rationale=f"{rule.rationale} | no room under the position or exposure limit",
                strategy_id=rule.strategy_id, hold_reason="risk_limit_near",
            )
        self._rule_decision = fitted
        context["rule_decision"] = {"action": fitted.action, "quantity": fitted.quantity}

    def _prices_before(self, snapshot: DataSnapshot) -> list[float]:
        """Earlier prices for a RULE strategy; empty without history, never invented."""
        if self.market_history is None:
            return []
        try:
            return regime.prices_before(self.market_history(), snapshot.symbol, snapshot.timestamp)
        except (OSError, ValueError):
            return []

    def _market(self, snapshot: DataSnapshot) -> dict[str, Any] | None:
        """Recent price behaviour from the agent's own record, up to this snapshot only.

        Built from journalled snapshots plus the current one, so it contains nothing the
        loop had not observed by now. Returns are against the last observed price at least
        one hour / one day earlier; None where the record does not reach that far back.
        """
        if self.market_history is None:
            return None
        try:
            bars = regime.bars_from_records(self.market_history(), snapshot.symbol)
        except (OSError, ValueError):  # an unreadable journal, or a record that will not parse
            return None
        bars = [bar for bar in bars if bar.timestamp < snapshot.timestamp]
        if not bars or bars[-1].price != snapshot.last_price:
            bars.append(regime.Bar(snapshot.timestamp, snapshot.last_price))
        # Today's session only: `classify` refuses a window with a gap in it, and any window
        # long enough to be useful spans the overnight close, so over the whole history the
        # label was UNKNOWN on every cycle.
        label = regime.classify([bar for bar in bars if bar.timestamp.date() == snapshot.timestamp.date()])

        def change_since(hours: float) -> float | None:
            cutoff = snapshot.timestamp.timestamp() - hours * 3600.0
            earlier = [bar for bar in bars if bar.timestamp.timestamp() <= cutoff]
            if not earlier:
                return None
            return round((snapshot.last_price / earlier[-1].price - 1.0) * 100.0, 3)

        return {
            "regime": label.label,
            "trend_pct": label.trend_pct,
            "volatility_pct": label.volatility_pct,
            "return_1h_pct": change_since(1.0),
            "return_1d_pct": change_since(24.0),
            "observations": len(bars),
        }

    def _exposure(
        self, snapshot: DataSnapshot, lots: Mapping[str, Mapping[str, object]] | None = None
    ) -> dict[str, Any]:
        """The exposure arithmetic Guardian will actually apply, plus the account total.

        It has to match Guardian exactly. When the cap was measured over every
        position, the model was told a BUY was blocked by a $37k bond allocation it
        had no mandate over, and it correctly held - against a limit that forbade
        every trade. Guardian now measures the allowlist book, because that is the
        only exposure the agent can increase, so the context has to say the same
        thing. The account total is still reported, clearly separated, so the model
        can see the rest of the book without it being charged against its limit.

        The per-symbol half was missing even after that. `max_position_value` was in
        `risk_limits` and the position's market value was in `positions`, and the
        model was expected to divide one by the other to learn that it was 2.6x over
        a limit - which is exactly what it did in one recorded rationale, correctly,
        while every other one reasoned about the strategy's price signal and never
        mentioned the limit at all. Reading it out costs one comparison and removes
        the arithmetic that produced 90% HOLDs with no risk reason attached.

        So it is stated here, in Guardian's own terms and from Guardian's own
        quantity: the agent's holding, falling back to the account's figure for the
        symbol when that holding is unknown, which is the same fallback Guardian
        uses so the two cannot disagree about whether a buy fits.
        """
        # Gross, as Guardian measures it: a short's negative value is exposure, not a credit.
        total = sum(abs(pos.market_value) for pos in snapshot.positions)
        allowlist = self.risk_limits.get("allowlist")
        names = set(allowlist) if isinstance(allowlist, (list, tuple, set)) else None
        mandate = sum(
            abs(pos.market_value)
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
        out.update(self._position_limit_state(snapshot, lots or {}))
        return out

    def _position_limit_state(
        self, snapshot: DataSnapshot, lots: Mapping[str, Mapping[str, object]]
    ) -> dict[str, Any]:
        """`max_position_value` restated as the arithmetic Guardian applies to it.

        Guardian bounds the position a BUY would leave behind, measured on the
        agent's own shares. If the agent's holding is not known, it falls back to
        the account's figure for that symbol - an over-estimate, so the limit can
        only bind harder. The same two steps, in the same order, are used here, so
        a BUY the context calls blocked is the BUY Guardian refuses, and a BUY the
        context calls permitted is one that passes.
        """
        cap = self.risk_limits.get("max_position_value")
        if not (isinstance(cap, (int, float)) and cap > 0):
            return {}
        held = next(
            (pos for pos in snapshot.positions if pos.symbol == snapshot.symbol), None
        )
        # Managing the whole account, Guardian measures the account's position, so this does.
        lot = None if self.risk_limits.get("manage_account") else lots.get(snapshot.symbol)
        quantity: float | None = None
        if isinstance(lot, Mapping) and is_number(lot.get("quantity")):
            quantity = coerce.field_float_or(lot["quantity"], "open_lots.quantity", 0.0)
        if quantity is None:
            quantity = float(held.quantity) if held is not None else 0.0

        held_value = quantity * snapshot.last_price
        headroom = cap - held_value
        one_share = snapshot.last_price
        return {
            "max_position_value": cap,
            "your_position_value": round(held_value, 2),
            "position_headroom": round(headroom, 2),
            "over_position_limit": held_value > cap,
            # Whether *any* buy fits. Guardian applies the same rule to whatever
            # size was actually ordered, so this answers only the question that can
            # be answered before a size is chosen - `shares_you_may_buy` below is
            # the quantity-aware fact, and it is the one that equals Guardian's
            # verdict exactly.
            "buy_blocked_by_position_limit": held_value + one_share > cap,
            # The largest order Guardian will approve: (held + q) * price <= cap.
            "shares_you_may_buy": max(0, int(headroom // one_share)) if one_share > 0 else 0,
        }

    @staticmethod
    def _show_lesson(snapshot: DataSnapshot, artifact_id: str) -> bool:
        """Whether this cycle is in the half of cycles shown this lesson.

        A stable hash of the snapshot time and the lesson, so the split is reproducible from
        the journal and independent across lessons. With every lesson always shown, its effect
        could not be separated from the model's; with half, the two halves are the comparison.
        """
        key = f"{snapshot.timestamp.isoformat()}|{artifact_id}".encode()
        return hashlib.sha256(key).digest()[0] % 2 == 0

    def _lessons(self, strategy_id: str | None) -> list[KnowledgeArtifact]:
        if self.lessons is None or strategy_id is None:
            return []
        try:
            return list(self.lessons(strategy_id))[: self.max_lessons]
        except Exception:
            return []

    def _served(self) -> int | None:
        """The policy engine's live cycle count, or `None` to let `select` derive it.

        Reached through `getattr` rather than called directly. `_context` catches every
        exception and degrades to a decision with no selected strategy, so calling
        `policy_engine.served()` on a policy engine that does not have it would raise
        `AttributeError` *inside* that guard and silently drop the strategy - which is
        exactly what two provenance tests caught, by asserting the mandate reached the
        model and got a strategy_id back.
        """
        served = getattr(self.policy_engine, "served", None)
        if served is None:
            return None
        try:
            return int(served())
        except (TypeError, ValueError):
            return None

    def _results(self):
        engine = self.policy_engine
        if engine.reflection_memory is None:
            return []
        record = engine.reflection_memory.load()
        if record is None:
            return []
        return engine.reflection_memory.strategy_results(record)
