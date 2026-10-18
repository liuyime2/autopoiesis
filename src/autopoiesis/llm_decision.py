from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import requests

from autopoiesis import coerce, regime
from autopoiesis.coerce import is_number
from autopoiesis.learning_evidence import capture, valid_provenance
from autopoiesis.models import (
    DataSnapshot,
    KnowledgeArtifact,
    NewsItem,
    StrategyProvenance,
    TradeDecision,
)

#: What `HybridDecisionEngine._fallback` writes when the model was asked and did not produce
#: a usable answer. A module constant for the same reason `FAIL_CLOSED_RATIONALE` is one:
#: the daemon reads it to decide whether the model was consulted and failed, and a marker
#: that exists in two places is a marker the two can drift apart until the monitor quietly
#: stops counting the failures it exists to count.
LLM_UNAVAILABLE_MARKER = "llm_unavailable: "

#: Marks a decision about a symbol no strategy covers. The model is asked with HOLD as the
#: baseline and may depart from it only with a reason. `strategy_id` is left None, which is
#: what keeps these out of every per-strategy metric and out of the lifecycle: they are not
#: a strategy's decisions and must never be scored as one.
NO_STRATEGY_MARKER = "no_strategy"


def _age_seconds(clock: datetime | None, observed: datetime | None) -> float | None:
    """How long before the clock `observed` happened, in seconds, or `None` if unknowable.

    `None` rather than 0.0: "the broker did not report a time" and "the time is now" are different
    facts, and collapsing them would tell the model a stale print was current.
    """
    if clock is None or observed is None:
        return None
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return round((clock - observed).total_seconds(), 1)


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
#: tests/autopoiesis/test_llm_decision.py` asserts that correspondence, so a field added to the
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
    "risk in the context, when present, is a forecast of how volatile this symbol will be over "
    "the next month (forecast_vol_pct, annualised) and how good that forecast has been "
    "(forecast_quality). It says how much risk a position carries, not which way to go: never use "
    "it as a reason to BUY or SELL. It can only argue for a smaller position, never for opening or "
    "adding to one: ACTIVE means the forecast has been shown to work, not that trading is "
    "encouraged, and a vol_scaled_fraction of 1 means only that volatility does not call for a "
    "smaller size. When status is ACTIVE and vol_scaled_fraction is below 1, a smaller quantity is "
    "the right response to high volatility; when status is SUSPENDED or UNVERIFIED the forecast "
    "cannot be relied on, so ignore it. "
    "market in the context summarises recent prices from your own record - regime, trend, "
    "volatility and returns - and is description, not an instruction to trade. "
    "instrument in the context is what the symbol is: the broker's own name for it plus measured "
    "behaviour against the index. It exists so you are never asked about a ticker alone, and its "
    "flags are description, not a rule. A symbol that does not track the index, or moves several "
    "times as far as the index, is not a substitute for the index - treat it as the instrument it "
    "is. "
    "news in the context, when present, are headlines the broker carries for the symbol. They are "
    "context describing what has already been reported, and they are not a trading signal: measured "
    "on 18,873 headlines, the best reader's next-day return correlation was within 0.03 of zero, so "
    "never treat a headline as a reason to BUY or SELL."
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
#: Measured 2026-10-18 against the live model with a realistic context, because the
#: failure rate had gone 2.0% -> 5.6% -> 13.7% over three days with one cause every time:
#: "LLM response did not contain a JSON object". The diagnostic added on 2026-10-17 names
#: it - an empty body, which is what `done_reason=length` looks like.
#:
#: The measurement: prompt 1,539 tokens, answer 1,245 - of which 3,494 characters are the
#: thinking block - done_reason=stop, spare 1,312 of 4,096. That is 32% headroom on an
#: answer whose length is not bounded by anything: `thinking` is free text the model writes
#: to whatever length it likes, and when it runs longer than the spare the whole response is
#: lost rather than truncated into something parseable.
#:
#: 6144 is 1.5x, and the spare becomes 3,360 - more than the entire answer measured here.
#: Latency was measured on the curriculum prompt at 141.9s at 8192 against 141.8s at 12288,
#: so the window is not what costs time; the answer length is.
DECISION_NUM_CTX = 6144
#: Worst case must be prompt + answer + growth. 8192 was sized from 3441 measured
#: prompt and 1452 measured answer, leaving 3299 spare. A chars/3.6 estimate said
#: 2838 and was 19% low, because JSON punctuation and short keys are cheap per
#: character; budgets are set from token counts, never from character guesses.
#:
#: Both halves of that sum have since grown and the budget did not. Measured
#: 2026-10-14 against the live model with the live context: prompt 3,965 and
#: answer 2,677, so 6,642 of 8,192 - 19% spare - with the answer being free text
#: the model writes to whatever length it likes. Overrunning returns
#: `done_reason=length` with an empty body, which is the
#: "constrained response contained no JSON object" failure recorded four times in
#: the twelve hours to 05:36.
#:
#: 12288 is free: measured on the same prompt, 141.8s at 12288 against 141.9s at
#: 8192. The whole gap is spent on the answer.
CURRICULUM_NUM_CTX = 12288
#: Measured 118.4s at 6144, 141.9s at 8192, 141.8s at 12288 on the same prompt.
#: The engine's default 120s timeout sat inside that range, so simply fixing the
#: window would have turned a truncation into a ReadTimeout instead. The
#: curriculum is an off-hours job, not the 5-minute trading loop, so paying two
#: minutes for a correct answer is the right trade. The decision path keeps 120s
#: because it answers in ~12s.
CURRICULUM_TIMEOUT_S = 600
#: The watchlist choice: a symbol list in, two fields out. It sits in the trading loop
#: rather than off-hours, so it gets the decision window and not the curriculum's - a
#: watchlist that takes two minutes is a cycle the loop did not spend deciding.
ATTENTION_NUM_CTX = 4096


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
        # The shape check comes first. `str(response.get("response") or "")` on a non-dict
        # raises AttributeError before any of the diagnosis below can run, so a transport
        # that hands back something unexpected would report a Python error instead of what
        # it actually returned - which is the failure mode this whole block exists to end.
        if not isinstance(response, dict):
            raise ValueError(
                f"decision model reply was not an object: {type(response).__name__}"
            )
        body = str(response.get("response") or "")
        if not body:
            # The same defect the curriculum had, one level down, and it is why the
            # decision path's failure rate could not be measured. Measured 2026-10-16:
            # 43 of the fallbacks on 10-15 and 10-16 carried exactly one cause -
            # "ValueError: LLM response did not contain a JSON object" - and nothing on
            # record said *why* the model returned no object. `done_reason` and `error`
            # are the two fields that answer it, and both were discarded here.
            #
            # `done_reason=length` with an empty body is context exhaustion: the prompt
            # and the answer schema together left no room to answer. That is the case the
            # 4096 above is sized against, and the one that has to be visible in the
            # journal before the window can be re-derived from a measurement rather than
            # from a guess.
            done = str(response.get("done_reason") or "")
            error = str(response.get("error") or "")
            prompt_tokens = response.get("prompt_eval_count")
            answer_tokens = response.get("eval_count")
            thinking = len(str(response.get("thinking") or ""))
            if error:
                raise ValueError(f"decision model returned an error: {error}")
            if done == "length":
                raise ValueError(
                    "decision model ran out of context before answering "
                    f"(done_reason=length, num_ctx={DECISION_NUM_CTX}, "
                    f"prompt_tokens={prompt_tokens}, answer_tokens={answer_tokens}, "
                    f"prompt_chars={len(str(payload.get('prompt', '')))}, "
                    f"thinking_chars={thinking}); the prompt and the answer schema did not "
                    "fit together, so the model had no tokens left to produce a body"
                )
            raise ValueError(
                "decision model returned no body and no cause "
                f"(done_reason={done or 'unset'!r}, prompt_tokens={prompt_tokens}, "
                f"answer_tokens={answer_tokens}, thinking_chars={thinking}, "
                f"keys={sorted(response)})"
            )
        decision = parse_decision_json(body, model_name=self.model)
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
        risk_provider: Callable[[str], dict[str, Any] | None] | None = None,
        instrument_provider: Callable[[str], dict[str, Any] | None] | None = None,
        news_provider: Callable[[str], tuple[NewsItem, ...]] | None = None,
        #: Guardian's own ceiling, so the uncovered path can refuse to *pay* for a decision
        #: that will be refused anyway. None leaves the path exactly as it was.
        stale_after_seconds: int | None = None,
    ):
        self.llm = llm
        self.stale_after_seconds = stale_after_seconds
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
        #: `symbol -> risk block` (see risk_judgment). A forecast of how volatile the symbol will
        #: be and how good that forecast has been, never of which way it will go.
        self.risk_provider = risk_provider
        self.last_risk_error: str | None = None
        #: `symbol -> instrument profile` (see data_gateway). What the thing is, from the broker's
        #: own asset record plus a measured behaviour profile, because under a widened universe the
        #: symbol may be a stock or a leveraged fund the model has never been asked about.
        self.instrument_provider = instrument_provider
        #: `symbol -> headlines` (see data_gateway.recent_news). Context, labelled as context.
        self.news_provider = news_provider
        self.last_instrument_error: str | None = None
        self.last_news_error: str | None = None
        #: The selected strategy's own decision for the snapshot `_context` last built.
        self._rule_decision: TradeDecision | None = None
        self._selected_spec = None
        self._selected_provenance: StrategyProvenance | None = None
        #: (shown, withheld) lesson ids for the snapshot `_context` last built.
        self._lesson_split: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())

    def _decide_uncovered(self, snapshot: DataSnapshot, context: dict) -> TradeDecision:
        """A symbol no strategy covers still gets a decision, and it is not a strategy's.

        Measured 2026-10-14: roughly 316 cycles a day across the watchlist and XLB were
        `selection_unavailable: no matching frozen strategy covering snapshot symbol`, so
        the model was never asked about them at all. The plan authorises asking it, with
        HOLD as the baseline and a reason required to depart from it.

        Three properties, each load-bearing:

        - **HOLD is the baseline.** A departure without an `override_reason` is not taken,
          the same rule the covered path applies and for the same measured reason: over 323
          comparable cycles, 112 of 113 disagreements were the model declining a trade with
          nothing recorded about why.
        - **`strategy_id` stays None.** That is what keeps these decisions out of every
          per-strategy metric and out of the lifecycle. The plan's acceptance is that the
          count of `no_strategy` decisions inside strategy scoring is zero.
        - **Guardian is untouched.** The decision goes through the same gate as any other;
          nothing here widens a limit or bypasses a check.

        `decision_source` stays `"llm"` when the model answers, so the monitor that counts
        whether the model answered keeps working. The marker goes in the rationale.
        """
        def abstain(reason: str) -> TradeDecision:
            return TradeDecision(
                symbol=snapshot.symbol, action="HOLD", quantity=0, confidence=0,
                rationale=f"selection_unavailable: {reason}",
                hold_reason="no_signal", decision_source="policy_engine",
            )

        if self._selection_broke:
            # A selector that threw is an outage, not an uncovered symbol. Asking the model
            # here would spend a call on a cycle whose strategy library could not be read.
            return abstain("selection could not run; the model was not asked")
        if self._quote_already_stale(snapshot):
            # Measured 2026-10-15: 48 cycles in one session asked the model about a symbol
            # whose price was already too old for Guardian, which refused every one of them.
            # The call bought nothing and cost about forty seconds of the round.
            #
            # Checked against the *snapshot's own clock*, not the wall clock: the question is
            # "was this price already old when the snapshot was taken", which is the defect
            # Guardian documents - a fresh clock with an hour-old price - and it is the same
            # answer under replay, where the wall clock would be wrong.
            return abstain("the price quote is already stale; the model was not asked")
        if self._library_empty:
            # Nothing is covered because nothing exists. That is a configuration failure and
            # not the gap this branch was authorised to fill.
            return abstain("the strategy library is empty; the model was not asked")

        try:
            decision = self.llm.decide(context)
        except Exception as exc:
            return abstain(f"no matching frozen strategy and the model did not answer: {type(exc).__name__}")
        if decision.symbol != snapshot.symbol:
            return abstain("no matching frozen strategy and the model named another symbol")

        departure = decision.action != "HOLD"
        if departure and not (decision.override_reason or "").strip():
            return TradeDecision(
                symbol=snapshot.symbol, action="HOLD", quantity=0,
                confidence=decision.confidence,
                rationale=(
                    f"{NO_STRATEGY_MARKER}: the model wanted {decision.action} for a symbol "
                    f"no strategy covers and gave no override_reason, so the HOLD baseline "
                    f"stands"
                ),
                hold_reason="other", decision_source="llm",
                model=decision.model,
            )
        return decision.model_copy(update={
            "decision_source": "llm",
            "strategy_id": None,
            "strategy_provenance": None,
            "hold_reason": decision.hold_reason or ("other" if not departure else None),
            "rationale": f"{NO_STRATEGY_MARKER}: {decision.rationale}",
        })

    def _quote_already_stale(self, snapshot: DataSnapshot) -> bool:
        """Whether Guardian would refuse this snapshot's price before the model is asked.

        Only the quote, not the clock: `snapshot.timestamp` is the broker's clock and is by
        construction the moment the snapshot was taken, so it cannot be stale relative to
        itself. `quote_time` is when the price was observed, and it is the one Guardian
        checks separately.
        """
        ceiling = self.stale_after_seconds
        quote_time = snapshot.quote_time
        if ceiling is None or quote_time is None:
            return False
        stamp, quote = snapshot.timestamp, quote_time
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if quote.tzinfo is None:
            quote = quote.replace(tzinfo=timezone.utc)
        return (stamp - quote).total_seconds() > ceiling

    def decide_snapshot(self, snapshot: DataSnapshot) -> TradeDecision:
        context = self._context(snapshot)
        selected_spec = self._selected_spec
        provenance = valid_provenance(self._selected_provenance)
        # Two different facts, and only one of them authorises asking the model.
        #
        #   - **The selector returned nothing.** No strategy covers this symbol. That is the
        #     gap the plan names, and the model may be asked about it.
        #   - **The selector returned something that does not fit** - a spec for another
        #     symbol, or provenance that is absent, misidentified or digest-invalid. That is
        #     a failure of selection or of capture, and asking the model about it would spend
        #     a call on a cycle whose attribution is already broken.
        #
        # Merging the two was the first version of this change, and
        # `test_synthetic_unattributed_selection_abstains_before_model` caught it: four of
        # its six remaining boundaries are provenance failures with a spec selected, and all
        # four started asking the model.
        if selected_spec is None:
            return self._decide_uncovered(snapshot, context)
        if (
            context.get("selected_strategy_id") != selected_spec.strategy_id
            or provenance is None
            or provenance.strategy_id != selected_spec.strategy_id
            or snapshot.symbol not in selected_spec.symbols
            or snapshot.symbol not in provenance.universe
        ):
            return TradeDecision(
                symbol=snapshot.symbol, action="HOLD", quantity=0, confidence=0,
                rationale="selection_unavailable: no matching frozen strategy covering snapshot symbol",
                hold_reason="no_signal", decision_source="policy_engine",
            )
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
                "strategy_id": selected_spec.strategy_id,
                "strategy_provenance": self._selected_provenance,
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
        update["strategy_id"] = selected
        update["strategy_provenance"] = self._selected_provenance
        return decision.model_copy(update=update)

    def _fallback(self, snapshot: DataSnapshot, reason: str) -> TradeDecision:
        if self._selected_spec is None:
            return TradeDecision(
                symbol=snapshot.symbol, action="HOLD", quantity=0, confidence=0,
                rationale="selection_unavailable: no frozen strategy for fallback",
                hold_reason="no_signal", decision_source="policy_engine",
            )
        if self._rule_decision is not None:
            decision = self._rule_decision
        else:
            decision = TradeDecision(
                symbol=snapshot.symbol, action="HOLD", quantity=0, confidence=0,
                rationale="selected strategy could not produce a fallback decision",
                strategy_id=self._selected_spec.strategy_id,
                strategy_provenance=self._selected_provenance,
            )
        updates: dict[str, object] = {
            "decision_source": "fallback_policy_engine",
            "strategy_id": self._selected_spec.strategy_id,
            "strategy_provenance": self._selected_provenance,
            # The fallback is the rule, so the rule's action is the action taken.
            "rule_action": decision.action,
            "rationale": f"{decision.rationale} | {LLM_UNAVAILABLE_MARKER}{reason}",
        }
        note = self._fallback_exposure_note(decision)
        if note is not None:
            updates["action"] = "HOLD"
            updates["quantity"] = 0
            # `hold_reason` is a closed literal set - no_signal, risk_limit_near,
            # market_uncertain, await_confirmation, other - and this wrote a sixth value
            # that is not in it. The record was still appended, so the write looked
            # successful; it then failed `TradeDecision` validation on the way back in and
            # the journal reader counted it as a dropped line. Measured 2026-10-15: five
            # XLE cycles were unreadable and `market-open-verify` failed with
            # CORRUPT_HISTORY, which is exactly what that diagnostic exists to catch.
            #
            # `other` is the honest literal - the reason is not one of the four named
            # states - and the specific cause stays in the rationale, which is free text
            # and is where a reader looks for it anyway.
            updates["hold_reason"] = "other"
            # Appended, not substituted. The first version replaced the rationale and
            # took the actual cause with it - "Read timed out" and the symbol the model
            # named both vanished, which is the diagnosis this whole branch exists to
            # preserve. A refusal that hides why the model was unavailable is worse than
            # no note at all.
            updates["rationale"] = f"{updates['rationale']} | {note}"
        return decision.model_copy(update=updates)

    #: Actions a rule may take when the model did not answer. SELL reduces a long and
    #: COVER reduces a short, so both are exits or reductions. BUY and SHORT add exposure.
    REDUCING_ACTIONS = frozenset({"HOLD", "SELL", "COVER"})

    def _fallback_exposure_note(self, decision: TradeDecision) -> str | None:
        """Why an unavailable model must not be able to open a position through a rule.

        The fallback took the rule's action verbatim, including a BUY, so a parse failure
        became a new position with no judgement behind it. Reported 2026-10-14: on 10-13,
        16 of that day's sells were placed by the fallback rather than by any decision.

        The asymmetry is the point. A rule that reduces exposure when the model is
        silent is still acting on the model's own strategy; a rule that opens one is
        taking a position nobody asked for, at a moment the system has already said it
        could not think. Guardian still gates whatever survives this, so this narrows the
        fallback rather than replacing a risk check.

        Not a safety limit and not weakened anywhere: no Guardian bound, no daily loss
        limit and no market-open check is touched. It removes an ability, not a bound.

        Returns the sentence to append, or None when the action reduces exposure.
        """
        if decision.action in self.REDUCING_ACTIONS:
            return None
        return (
            f"the rule wanted {decision.action}, which adds exposure; the fallback may "
            f"only hold or reduce, so no order was placed"
        )

    def _context(self, snapshot: DataSnapshot) -> dict[str, Any]:
        # Selection and lessons come from the policy engine. A missing or invalid
        # frozen selection makes decide_snapshot abstain before calling the LLM;
        # Guardian remains the final authority on covered decisions.
        strategy_id = None
        selected_spec = None
        self._rule_decision = None
        self._selected_spec = None
        self._selected_provenance = None
        #: Whether selection could not run at all, as opposed to running and finding no
        #: strategy that covers this symbol. The two were indistinguishable - the handler
        #: below sets `strategy_id = None` in both cases - and they call for opposite
        #: behaviour: an uncovered symbol may be asked of the model, a broken selector may
        #: not. `test_a_broken_policy_engine_abstains_before_the_llm` pins that, and caught
        #: this branch asking the model about a selector that had just thrown.
        self._selection_broke = False
        #: Whether the library held any strategy at all. An empty library is a configuration
        #: failure, not an uncovered symbol: there is nothing to be uncovered *by*, and the
        #: plan's authority is for a symbol no strategy covers, not for a system with no
        #: strategies. Kept separate from `_selection_broke` because a library that reads
        #: cleanly and is empty is not the same failure as one that could not be read.
        self._library_empty = False
        try:
            strategies = self.policy_engine.strategy_library.list()
            selected = self.policy_engine.selector.select(
                strategies,
                self._results(strategies),
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
                # Coverage is per-symbol, so it is the cycled symbol that decides it. Without
                # this the selector served SPY-anchored strategies for BIL/TLT/XLB/... cycles
                # and the executor declined 89% of a day's service - see `select`.
                symbol=snapshot.symbol,
            )
            if selected is not None:
                strategy_id = selected.strategy_id
                selected_spec = selected
                self._selected_spec = selected
                self._selected_provenance = capture(selected)
            self._library_empty = not strategies
        except Exception:
            strategy_id = None
            self._selection_broke = True
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
            # What that number *is*, and how old. The account holder's decision of 2026-10-18 was
            # to keep pricing decisions on the last trade rather than the quote mid, which makes the
            # age of that print a fact the model has to be told: the freshness gate measures the
            # quote, so a stale print beside a live quote is precisely the case it lets through.
            # Leaving it unsaid would make the model the one reader of the context unable to see it.
            "last_price_source": "last_trade",
            "last_trade_age_seconds": _age_seconds(snapshot.timestamp, snapshot.price_time),
            "quote_age_seconds": _age_seconds(snapshot.timestamp, snapshot.quote_time),
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
        risk = self._risk(snapshot.symbol)
        if risk is not None:
            context["risk"] = risk
        # What this instrument is. Under a widened universe the symbol is often one the model has
        # never been asked about - a stock added from the broker's most-active list, or a leveraged
        # fund - and a ticker plus a price is not enough to decide about it.
        instrument = self._instrument(snapshot.symbol)
        if instrument is not None:
            context["instrument"] = instrument
        news = self._news(snapshot.symbol)
        if news:
            context["news"] = news
        return context

    def _instrument(self, symbol: str) -> dict[str, Any] | None:
        if self.instrument_provider is None:
            return None
        try:
            return self.instrument_provider(symbol)
        except Exception as exc:  # a failed profile must leave the decision exactly as it was
            self.last_instrument_error = f"{type(exc).__name__}: {exc}"
            return None

    def _news(self, symbol: str) -> list[dict[str, Any]]:
        if self.news_provider is None:
            return []
        try:
            items = self.news_provider(symbol)
        except Exception as exc:  # headlines are context; failing to read them is not a decision
            self.last_news_error = f"{type(exc).__name__}: {exc}"
            return []
        return [
            {
                "headline": item.headline,
                "source": item.source,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "summary": item.summary,
            }
            for item in items
        ]

    def _risk(self, symbol: str) -> dict[str, Any] | None:
        if self.risk_provider is None:
            return None
        try:
            return self.risk_provider(symbol)
        except Exception as exc:  # a failed risk judgment must leave the decision exactly as it was
            self.last_risk_error = f"{type(exc).__name__}: {exc}"
            return None

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
            library = getattr(self.policy_engine, "knowledge_library", None)
            return [a for a in self.lessons(strategy_id)
                    if a.source_kind != "journal" or (library is not None and library.lesson_verified(a))][: self.max_lessons]
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

    def _results(self, strategies=None):
        engine = self.policy_engine
        if engine.reflection_memory is None:
            return []
        record = engine.reflection_memory.load()
        if record is None:
            return []
        return engine.reflection_memory.strategy_results(record, strategies if strategies is not None else engine.strategy_library.list())
