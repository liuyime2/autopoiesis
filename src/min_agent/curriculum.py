from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from min_agent import coerce
from min_agent.atomicio import read_json, write_json_atomic
from min_agent.models import STRATEGY_PARAMETER_SCHEMAS, CurriculumTask, ReflectionRecord, StrategySpec

CurriculumTransport = Callable[[dict[str, Any]], str]


def parse_curriculum_task_json(text: str, *, now: Callable[[], datetime] | None = None) -> CurriculumTask:
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start == -1:
            break
        try:
            payload, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if not _is_task_like(payload):
            index = start + end
            continue
        return _validate_curriculum_payload(payload, now=now)
    raise ValueError("LLM response did not contain a curriculum task JSON object")


def _is_task_like(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    task_keys = {"task_id", "task_type", "summary", "strategy_spec", "rationale"}
    return bool(task_keys.intersection(payload))


def _validate_curriculum_payload(payload: dict[str, Any], *, now: Callable[[], datetime] | None = None) -> CurriculumTask:
    strategy_spec = payload.get("strategy_spec")
    if (
        str(payload.get("task_type", "")).strip().upper() == "STRATEGY_SPEC"
        and isinstance(strategy_spec, dict)
        and not strategy_spec.get("created_at")
    ):
        clock = now or (lambda: datetime.now(tz=timezone.utc))
        strategy_spec["created_at"] = clock().isoformat()
    return CurriculumTask.model_validate(payload)


class StructuredCurriculumAgent:
    def __init__(
        self,
        *,
        transport: CurriculumTransport | None = None,
        state_path: Path | str | None = None,
        breakout_threshold: int = 3,
        max_strategies_in_prompt: int = 40,
        max_parse_retries: int = 1,
    ):
        self.transport = transport
        # Tracks consecutive EVALUATE fallbacks so a dead loop can be broken.
        # This used to be plain instance state, which meant every daemon restart
        # reset it to zero. Combined with run_forever.sh restarting on every
        # broker blip, the count could never reach the threshold and the
        # breakout was unreachable. Persisted next to the journal instead.
        self.state_path = Path(state_path) if state_path else None
        self.breakout_threshold = breakout_threshold
        self.max_strategies_in_prompt = max_strategies_in_prompt
        self.max_parse_retries = max_parse_retries
        self.parse_attempts = 0
        self.last_failure: str | None = None
        self.last_raw_excerpt: str = ""
        self._consecutive_evaluate_count: int = self._load_count()
        self.last_context: dict[str, object] = {}
        self.capability_attempts: int = 0
        self.max_capability_attempts: int = 1

    def propose(
        self,
        *,
        reflection: ReflectionRecord,
        current_strategies: list[StrategySpec],
        target_daily_return_pct: float = 10.0,
        recent_exploration_summary: dict[str, object] | None = None,
        prior_rejections: Sequence[Mapping[str, object]] = (),
        guardian_max_position_value: float | None = None,
        open_positions: Sequence[Mapping[str, object]] = (),
        last_price: float | None = None,
        symbol: str = "SPY",
    ) -> CurriculumTask:
        if self.transport is None:
            return self._fallback_task(
                reflection,
                current_strategies,
                recent_exploration_summary=recent_exploration_summary,
                guardian_max_position_value=guardian_max_position_value,
            )

        # Deliberately compact. The previous context embedded the entire
        # ReflectionRecord dump (~5kB, which itself contains strategy_metrics and
        # a duplicate `evaluation` blob) plus a full model_dump of every strategy
        # (~8.4kB for 15 strategies) - about 14kB. deepseek-r1:8b could not
        # follow it: it stopped producing a curriculum task and instead emitted a
        # meta-refusal about "extracting the 'query' field", so every daemon
        # curriculum call fell back to a hard-coded EVALUATE. The self-evolution
        # loop was dead while reporting healthy. Only what a decision actually
        # needs is sent: current strategy identities and states, their measured
        # scores and action counts, the exploration summary, and the limits.
        prompt_context = {
            "target_daily_return_pct": target_daily_return_pct,
            "safety": {
                "paper_only": True,
                "guardian_limits_mutable": False,
                "generated_code_allowed": False,
                "real_data_only": True,
            },
            "window_cycles": reflection.window_cycles,
            "submitted_orders": reflection.submitted_orders,
            "rejected_orders": reflection.rejected_orders,
            "error_count": reflection.error_count,
            "pnl_evidence": reflection.evaluation.get("pnl_evidence"),
            "strategies": self._compact_strategies(current_strategies, reflection)[:10],
            "strategy_count_total": len(current_strategies),
            # "strategy_id: unique" in the schema block is unenforceable on its own.
            # The model proposed fixed-size-sell-001, which the library already
            # holds as RETIRED, so admission rejected the one strategy that could
            # have supplied a missing exit. The ids in use are stated so a fresh
            # one can be chosen; the count is stated too so the list's cap is not
            # mistaken for the library size.
            "strategy_ids_in_use": [strategy.strategy_id for strategy in current_strategies][-40:],
            "strategy_id_must_not_be_one_of_the_above": True,
            # What the agent actually holds, and which actions the library can
            # currently express. The context previously described only the
            # strategies, so the engine could not see that the book carried 23
            # shares bought in June while no selectable strategy could express a
            # SELL at all. It is a curriculum engine's job to know the gap it is
            # being asked to fill; these are facts about the account and the
            # library, not an instruction to trade.
            "open_positions": [dict(item) for item in open_positions],
            "capability_coverage": _capability_coverage(current_strategies, last_price, symbol),
            # Restated as a flat, unambiguous demand rather than left inside
            # capability_coverage. The model chose FIXED_SIZE and then action=BUY
            # three times in a row while the library stayed unable to sell: phase A
            # is asked to pick a kind from a three-item enum, and a nested report
            # saying "uncovered_actions_now: [SELL]" did not steer a choice that
            # has no SELL in it. Stating the required action next to the enum is
            # what makes the gap actionable at the point the kind is chosen.
            "required_action": (
                _required_action(_capability_coverage(current_strategies, last_price, symbol))
            ),
            "recent_exploration_summary": _model_facing_summary(recent_exploration_summary),
            # What the model already proposed and was refused, with the reason.
            # The curriculum was blind to its own output: the exploration summary
            # described trading activity (HOLD counts, orders, prices) and never
            # mentioned a single spec, so a rejected id looked as fresh as a new
            # one. On 2026-09-29 it proposed fixed-size-sell-002 four times in a
            # row and received the identical 212-character refusal each time,
            # which was computed, journalled, and never shown to the author. The
            # reason already names the exact dimensions that must differ.
            "recently_rejected": [dict(item) for item in prior_rejections],
            "recently_rejected_ids_must_not_be_reused": [
                str(item.get("strategy_id")) for item in prior_rejections if item.get("strategy_id")
            ],
            "guardian_max_position_value": guardian_max_position_value,
            "exploration_policy": [
                "No trade is not failure; no exploration is failure.",
                "If recent_exploration_summary.no_exploration is true, propose the next distinct small admissible strategy instead of repeating generic EVALUATE or near-duplicate TREND_FOLLOW.",
                "Every entry in recently_rejected is a strategy you already proposed and admission refused. Read the reason and change what it names: kind, symbols, action, quantity, or max_position_value. Renaming the same trade does not make it a new strategy and will be refused identically.",
                "Do not force orders; Guardian, market-open checks, and paper executor remain authoritative.",
                "Do not claim profitability or target achievement without broker evidence in the supplied context.",
            ],
            # Stated explicitly rather than shown by example. The full
            # model_dump of every strategy used to serve as the de-facto schema,
            # and when the context was compacted that example went with it: the
            # model then emitted a strategy_spec missing strategy_id, name,
            # symbols and max_position_value, and every call fell back. A
            # 400-character field list is a better teacher than 8kB of examples.
            "schema": {
                "task_id": "string, required, unique",
                "task_type": "STRATEGY_SPEC|OPTIMIZE|EVALUATE, required",
                "summary": "string, required, one line",
                "strategy_spec": "required when task_type is STRATEGY_SPEC, else null",
                "parameters": "object of safe scalar params, optional",
                "rationale": "string, required",
            },
            "strategy_spec_fields": {
                "required": [
                    "strategy_id", "name", "kind", "symbols",
                    "max_position_value", "created_at", "rationale",
                ],
                "optional": ["parameters", "enabled", "lifecycle"],
                "field_notes": {
                    "strategy_id": "a unique id not already present in strategies[].strategy_id",
                    "name": "human readable label",
                    "kind": "one of HOLD_BASELINE, FIXED_SIZE, TREND_FOLLOW",
                    "symbols": "array of symbols, e.g. [\"SPY\"]",
                    "parameters": "must match supported_strategy_schemas[kind] exactly",
                    "max_position_value": "a number at or below guardian_max_position_value",
                    "created_at": "ISO-8601 UTC timestamp, the current time",
                    "enabled": "true",
                    "rationale": "why this skill, grounded in the evidence above",
                },
            },
            "worked_example": {
                "task_id": "task-0001",
                "task_type": "STRATEGY_SPEC",
                "summary": "add a small fixed-size probe because nothing has explored",
                "strategy_spec": {
                    "strategy_id": "fixed-size-probe-0001",
                    "name": "Fixed Size Probe",
                    "kind": "FIXED_SIZE",
                    "symbols": ["SPY"],
                    "parameters": {"action": "BUY", "quantity": 1, "confidence": 0.6},
                    "max_position_value": guardian_max_position_value or 5000.0,
                    "enabled": True,
                    "created_at": "<current ISO-8601 UTC timestamp>",
                    "rationale": "no exploration in the recent window",
                },
                "parameters": {},
                "rationale": "smallest admissible trading skill",
            },
            "supported_strategy_schemas": {
                kind: {
                    "required_params": list(
                        coerce.field_str_tuple(
                            coerce.field_dict(schema, f"schema[{kind}]").get("required"),
                            f"schema[{kind}].required",
                        )
                    ),
                    "allowed_params": list(
                        coerce.field_str_tuple(
                            coerce.field_dict(schema, f"schema[{kind}]").get("allowed"),
                            f"schema[{kind}].allowed",
                        )
                    ),
                    "description": coerce.field_str(
                        coerce.field_dict(schema, f"schema[{kind}]").get("description"),
                        f"schema[{kind}].description",
                    ),
                }
                for kind, schema in STRATEGY_PARAMETER_SCHEMAS.items()
            },
            "curriculum_progression": [
                "1. Create/maintain HOLD_BASELINE as the first safe non-trading strategy.",
                "2. After baseline exists, propose a tiny FIXED_SIZE strategy with quantity <= 1 and only allowed params (action, quantity, confidence).",
                "3. Evaluate broker/journal evidence before proposing new skills.",
                "4. Only propose TREND_FOLLOW after a FIXED_SIZE skill has been admitted and evaluated.",
                "Do NOT skip progression steps or propose complex skills before small ones are proven.",
            ],
            "forbidden_outputs": [
                "Generated Python code, shell commands, or executable scripts.",
                "Changes to Guardian hard limits, risk constraints, or mode (paper/live).",
                "Fake, mock, or fabricated evidence sources.",
                "Unsupported strategy parameters that are not in the allowed list for the chosen kind.",
                "Claims of Google/web search facts unless supplied as explicit evidence context.",
                "Live trading instructions; paper-only.",
            ],
        }
        # The model's output is validated, not trusted. deepseek-r1:8b produces a
        # usable task some of the time and an unusable one otherwise - typically
        # a strategy_spec whose kind-conditional parameters are missing, because
        # ollama's constrained decoding does not enforce `const` or
        # `additionalProperties` in a oneOf schema (measured: the schema was
        # accepted in 6.4s and then ignored, producing task_type
        # 'strategy_generation' and arbitrary parameters). Rather than encode a
        # conditional the decoder will not honour, retry once with the failure
        # reason appended. Both attempts are still validated.
        attempts: list[str] = []
        for attempt in range(1 + self.max_parse_retries):
            gap_attempt = attempt > 0 and "capability gap" in (attempts[-1] if attempts else "")
            ctx = prompt_context if attempt == 0 else {
                **prompt_context,
                **(
                    {"unfilled_capability": _capability_demand(attempts, prompt_context)}
                    if gap_attempt
                    else {
                        "previous_output_rejected": {
                            "error": attempts[-1] if attempts else "no parse error recorded",
                            "instruction": (
                                "Return it again with every required field present. For "
                                "STRATEGY_SPEC, strategy_spec.parameters must match the "
                                'kind exactly: HOLD_BASELINE {}, FIXED_SIZE '
                                '{"action":"BUY|SELL|HOLD","quantity":int,"confidence":0-1}, '
                                'TREND_FOLLOW {"reference_price":number,"threshold_pct":0-0.2,'
                                '"quantity":int,"confidence":0-1}.'
                            ),
                        }
                    }
                ),
            }
            self.last_context = ctx
            raw = self.transport(ctx)
            try:
                task = parse_curriculum_task_json(raw)
            except Exception as exc:
                attempts.append(f"{type(exc).__name__}: {exc}")
                continue

            gap = _unmet_capability(task, prompt_context)
            if gap and not (self.capability_attempts >= self.max_capability_attempts):
                # The engine reported a capability the library cannot perform, and
                # the model answered with a strategy that does not fill it. An
                # 8B model asked for "the next distinct admissible strategy" keeps
                # answering BUY. Escalating the retry with the specific gap named
                # is the curriculum engine doing its job; the model still authors
                # the strategy and the market still decides the outcome. If the gap
                # is never filled the task is still returned and the shortfall is
                # visible in capability_attempts.
                self.capability_attempts = attempt + 1
                attempts.append(
                    f"capability gap not filled: library cannot express {gap}"
                )
                continue

            self.last_failure = None
            self.last_raw_excerpt = raw[-500:] if isinstance(raw, str) else ""
            self.parse_attempts = attempt + 1
            self.capability_attempts = 0
            if task.task_type == "STRATEGY_SPEC" and task.strategy_spec is not None:
                self._set_count(0)
            return task

        # Every attempt failed. The reason belongs in the journal: swallowing it
        # made a 100% fallback rate look like normal operation, which is how the
        # self-evolution loop stayed dead while every health check was green.
        self.last_failure = (
            f"after {len(attempts)} attempt(s): {attempts[-1]}" if attempts else "no attempt made"
        )
        self.last_raw_excerpt = raw[-500:] if isinstance(raw, str) else ""
        self.parse_attempts = len(attempts)
        return self._fallback_task(
            reflection,
            current_strategies,
            recent_exploration_summary=recent_exploration_summary,
            guardian_max_position_value=guardian_max_position_value,
        )

    def _compact_strategies(
        self, strategies: list[StrategySpec], reflection: ReflectionRecord | None = None
    ) -> list[dict[str, object]]:
        """Bounded, decision-relevant view of the library.

        The per-strategy payload scales linearly, so a library that grew past a
        few dozen entries would push the prompt back to the size at which
        deepseek-r1:8b stopped following it. Selectable strategies come first
        (those are the ones a proposal has to fit around), then the rest by most
        recent, capped, with the omitted count stated so the model is not misled
        into thinking the library is small.
        """
        if len(strategies) <= self.max_strategies_in_prompt:
            ordered = strategies
        else:
            def rank(spec: StrategySpec) -> tuple[int, datetime]:
                selectable = spec.enabled and spec.lifecycle not in {"PAUSED", "RETIRED"}
                return (0 if selectable else 1, spec.created_at)

            ordered = sorted(strategies, key=rank)[: self.max_strategies_in_prompt]
        # One entry per strategy, carrying its own measured evidence. These were
        # four separate per-strategy maps (strategies, strategy_scores,
        # strategy_actions, strategy_trade_evidence), which sent the same 40
        # strategies four times: ~12.5kB of a 15.3kB context. Merged, it is
        # ~6.6kB and the model reads it as a table instead of four lists to join.
        metrics = (reflection.strategy_metrics if reflection else None) or {}
        scores = (reflection.strategy_scores if reflection else None) or {}
        return [
            {
                "strategy_id": spec.strategy_id,
                "kind": spec.kind,
                "lifecycle": spec.lifecycle,
                "symbols": list(spec.symbols),
                "parameters": spec.parameters,
                "score": scores.get(spec.strategy_id),
                "action_counts": (metrics.get(spec.strategy_id) or {}).get("action_counts", {}),
                "cycles": (metrics.get(spec.strategy_id) or {}).get("cycles", 0),
                "trade_attempts": (metrics.get(spec.strategy_id) or {}).get("trade_attempts", 0),
                "submitted_orders": (metrics.get(spec.strategy_id) or {}).get("submitted_orders", 0),
                "filled_quantity": (metrics.get(spec.strategy_id) or {}).get("filled_quantity", 0.0),
            }
            for spec in ordered
        ]

    def _fallback_task(
        self,
        reflection: ReflectionRecord,
        current_strategies: list[StrategySpec],
        *,
        recent_exploration_summary: dict[str, object] | None = None,
        guardian_max_position_value: float | None = None,
    ) -> CurriculumTask:
        if not current_strategies:
            spec = StrategySpec(
                strategy_id="hold-baseline",
                name="Hold Baseline",
                kind="HOLD_BASELINE",
                symbols=("SPY",),
                parameters={},
                max_position_value=1.0,
                created_at=datetime.now(tz=timezone.utc),
                rationale="safe fallback strategy when no validated strategy exists",
            )
            self._set_count(0)
            return CurriculumTask(
                source="fallback",
                task_id="fallback-hold-baseline",
                task_type="STRATEGY_SPEC",
                summary="create a safe hold baseline",
                strategy_spec=spec,
                rationale="no current strategies exist; start with non-trading baseline",
            )

        has_baseline = any(strategy.kind == "HOLD_BASELINE" for strategy in current_strategies)
        has_trading = any(strategy.kind != "HOLD_BASELINE" for strategy in current_strategies)
        if has_baseline and not has_trading:
            max_position_value = guardian_max_position_value or 500.0
            spec = StrategySpec(
                strategy_id="fallback-tiny-fixed-size",
                name="Fallback Tiny Fixed Size",
                kind="FIXED_SIZE",
                symbols=current_strategies[0].symbols,
                parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
                max_position_value=max_position_value,
                created_at=datetime.now(tz=timezone.utc),
                rationale="baseline exists; propose the smallest executable trading skill for Guardian review",
            )
            self._set_count(0)
            return CurriculumTask(
                source="fallback",
                task_id="fallback-tiny-fixed-size",
                task_type="STRATEGY_SPEC",
                summary="create a tiny executable fixed-size skill",
                strategy_spec=spec,
                rationale="progress from baseline to a small executable probation strategy without forcing execution",
            )

        if recent_exploration_summary and recent_exploration_summary.get("no_exploration"):
            # Break out of the EVALUATE dead-loop: after `breakout_threshold`
            # consecutive EVALUATEs with no exploration progress, the next task is
            # a distinct small trading skill so admission/Guardian can react to
            # new behaviour. Real journal evidence (no SELLs, all HOLDs) shows
            # EVALUATE alone is insufficient.
            #
            # Checked before counting, so three EVALUATEs are emitted and the
            # fourth breaks out. The count used to live in process memory, which
            # is why this was unreachable in practice: run_forever.sh restarted
            # the daemon on every broker blip and reset it to zero each time.
            if self._consecutive_evaluate_count >= self.breakout_threshold:
                existing_ids = {strategy.strategy_id for strategy in current_strategies}
                attempt = self._consecutive_evaluate_count
                strategy_id = f"fallback-breakout-fixed-size-{attempt:02d}"
                while strategy_id in existing_ids:
                    attempt += 1
                    strategy_id = f"fallback-breakout-fixed-size-{attempt:02d}"
                max_position_value = guardian_max_position_value or 500.0
                spec = StrategySpec(
                    strategy_id=strategy_id,
                    name=f"Fallback Breakout Fixed Size #{attempt}",
                    kind="FIXED_SIZE",
                    symbols=current_strategies[0].symbols,
                    parameters={"action": "BUY", "quantity": 1, "confidence": 0.55},
                    max_position_value=max_position_value,
                    created_at=datetime.now(tz=timezone.utc),
                    rationale="repeated EVALUATE with no exploration; propose a distinct small skill so Guardian/admission can react to new behavior",
                )
                self._set_count(0)
                return CurriculumTask(
                    source="fallback",
                    task_id=strategy_id,
                    task_type="STRATEGY_SPEC",
                    summary="break out of EVALUATE dead-loop with a distinct small executable skill",
                    strategy_spec=spec,
                    rationale=(
                        f"{self.__class__.__name__} observed {attempt} consecutive EVALUATE fallbacks "
                        "while no_exploration remained true"
                    ),
                )
            self._increment()
            return CurriculumTask(
                source="fallback",
                task_id="evaluate-no-exploration",
                task_type="EVALUATE",
                summary="evaluate no-exploration window before creating another strategy",
                parameters={"window_cycles": reflection.window_cycles, "no_exploration": True},
                rationale="recent real journal evidence shows no exploration; wait for transport/admission to propose a distinct safe strategy",
            )

        self._increment()
        return CurriculumTask(
            task_id="evaluate-recent-performance",
            task_type="EVALUATE",
            summary="evaluate recent strategy outcomes before changing behavior",
            parameters={"window_cycles": reflection.window_cycles},
            rationale="use measured paper outcomes before creating or selecting new strategies",
            source="fallback",
        )

    # --- persisted counters -------------------------------------------------

    def _load_count(self) -> int:
        if self.state_path is None:
            return 0
        data = read_json(self.state_path)
        if isinstance(data, dict):
            value = data.get("consecutive_evaluate_count")
            if isinstance(value, int) and value >= 0:
                return value
        return 0

    def _set_count(self, value: int) -> None:
        self._consecutive_evaluate_count = value
        if self.state_path is None:
            return
        try:
            write_json_atomic(
                self.state_path, {"consecutive_evaluate_count": value, "updated_at": _now_iso()}
            )
        except OSError:
            pass

    def _increment(self) -> None:
        self._set_count(self._consecutive_evaluate_count + 1)


def _model_facing_summary(summary: dict[str, object] | None) -> dict[str, object] | None:
    """The exploration summary minus fields only the daemon can use.

    `_recent_exploration_summary` carries up to 50 `cycle_ids` so the daemon can
    cite them as `source_refs` on a no-exploration KnowledgeArtifact. That is
    provenance for a journal write, not input for a decision: 50 UUIDs were ~2100
    of the 2522 characters in this block, about 550 tokens of a 3441-token prompt,
    and no curriculum choice can depend on them. Deleting them from the summary
    would have broken the artifact's lineage, so they are removed at the boundary
    where the data changes consumer instead of where it is collected.
    """
    if not summary:
        return summary
    return {key: value for key, value in summary.items() if key != "cycle_ids"}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# --- schema-constrained generation -------------------------------------------
#
# ollama's constrained decoder does not honour `const` or `additionalProperties`
# inside a oneOf: a conditional schema describing the per-kind parameters was
# accepted in 6.4s and then ignored, yielding task_type 'strategy_generation'
# and arbitrary parameters. A FLAT object with a top-level `required` list and
# `enum` values IS honoured - measured 4.6s, no missing required fields, no enum
# violations. So the kind-conditional requirement is expressed by generating
# against the schema for the kind that was chosen, rather than by encoding the
# condition in one schema.

_STRATEGY_FIELDS = {
    "strategy_id": {"type": "string"},
    "name": {"type": "string"},
    "symbols": {"type": "array", "items": {"type": "string"}},
    "max_position_value": {"type": "number", "exclusiveMinimum": 0},
    "created_at": {"type": "string"},
    "rationale": {"type": "string"},
    "enabled": {"type": "boolean"},
}

_KIND_PARAM_SCHEMA: dict[str, dict[str, object]] = {
    "HOLD_BASELINE": {},
    "FIXED_SIZE": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "quantity": {"type": "integer", "minimum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "TREND_FOLLOW": {
        "reference_price": {"type": "number", "exclusiveMinimum": 0},
        "threshold_pct": {"type": "number", "minimum": 0, "maximum": 0.2},
        "quantity": {"type": "integer", "minimum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

_KIND_SELECT_SCHEMA = {
    "type": "object",
    "required": ["kind", "why"],
    "properties": {
        "kind": {"type": "string", "enum": ["HOLD_BASELINE", "FIXED_SIZE", "TREND_FOLLOW"]},
        "why": {"type": "string"},
    },
}


_FIELD_GUIDE = {
    "TREND_FOLLOW": (
        " Units: threshold_pct is a FRACTION, not a percentage - 0.02 means 2 "
        "percent, and it must be greater than 0 and at most 0.2. reference_price is "
        "the real last_price from the context, copied exactly."
    ),
    "FIXED_SIZE": (
        " Units: confidence is a number between 0 and 1, not a percentage; quantity "
        "is a whole number of shares."
    ),
    "HOLD_BASELINE": "",
}


def strategy_spec_schema(kind: str) -> dict[str, object]:
    """A flat, fully-required schema for one strategy kind."""
    params = _KIND_PARAM_SCHEMA[kind]
    properties = {**_STRATEGY_FIELDS, "kind": {"type": "string", "enum": [kind]}, **params}
    return {
        "type": "object",
        "required": sorted(properties),
        "properties": properties,
    }


def kind_selection_schema() -> dict[str, object]:
    return dict(_KIND_SELECT_SCHEMA)


def generate_curriculum_task_json(*, call, prompt_context: dict, spec_hint: str | None = None) -> str:
    """Two-phase, schema-constrained generation of one CurriculumTask.

    `call(prompt, schema, context)` performs one constrained generation and
    returns the raw response text.

    Phase A picks the strategy kind with a tiny flat enum schema. Phase B asks
    for the spec against that kind's flat, fully-required schema. Two small
    calls rather than one schema that encodes the kind-conditional parameter
    requirement, because ollama's constrained decoder honours a flat `required`
    list and `enum` values but ignores `const` and `additionalProperties` inside
    a oneOf. Measured: the oneOf form was accepted in 6.4s and then ignored; the
    flat form is honoured.

    The returned text is still only a candidate - the caller validates it.
    """
    if spec_hint is not None:
        spec = _json_load(call(
            f"Return the strategy_spec for kind {spec_hint}. Every field is required. "
            f"{_FIELD_GUIDE.get(spec_hint, '')}"
            f"Use only the symbols present in the context. {prompt_context_text(prompt_context)}",
            strategy_spec_schema(spec_hint),
        ))
        task = {
            "task_id": _task_id(spec_hint, spec),
            "task_type": "STRATEGY_SPEC",
            "summary": str(spec.get("name") or f"{spec_hint} proposal"),
            "strategy_spec": _reshape_spec(spec, spec_hint),
            "parameters": {},
            "rationale": str(spec.get("rationale") or "proposed by the curriculum agent"),
        }
        return json.dumps(task)

    required = prompt_context.get("required_action")
    demand = (
        f"The library cannot express a {required} at the real last price, and the "
        f"positions in open_positions have no working exit because of it. Choose a "
        f"kind whose parameters can carry action={required}: FIXED_SIZE takes an "
        f"action field, and TREND_FOLLOW derives its side from price. "
        f"Do not choose HOLD_BASELINE, which cannot exit. "
        if isinstance(required, str) and required
        else "Choose exactly one strategy kind to propose next. "
    )
    kind_raw = call(
        f"{demand}{prompt_context_text(prompt_context)}",
        kind_selection_schema(),
    )
    kind = str(_json_load(kind_raw).get("kind") or "FIXED_SIZE").upper()
    if kind not in _KIND_PARAM_SCHEMA:
        kind = "FIXED_SIZE"
    spec = _json_load(call(
        f"Return the strategy_spec for kind {kind}. Every field is required. "
        + _FIELD_GUIDE.get(kind, "")
        + (
            f"Set parameters.action to {required} - the library cannot express it "
            f"and the open positions have no exit. "
            if isinstance(required, str) and required and kind == "FIXED_SIZE"
            else ""
        )
        + f"Use only the symbols present in the context. {prompt_context_text(prompt_context)}",
        strategy_spec_schema(kind),
    ))
    return json.dumps({
        "task_id": _task_id(kind, spec),
        "task_type": "STRATEGY_SPEC",
        "summary": str(spec.get("name") or f"{kind} proposal"),
        "strategy_spec": _reshape_spec(spec, kind),
        "parameters": {},
        "rationale": str(spec.get("rationale") or "proposed by the curriculum agent"),
    })


def prompt_context_text(prompt_context: dict) -> str:
    return "Context: " + json.dumps(prompt_context, sort_keys=True)


def _json_load(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("constrained response contained no JSON object")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("constrained response was not a JSON object")
    return parsed


def _reshape_spec(spec: dict, kind: str) -> dict:
    """Move the flat kind-specific keys into `parameters`, as StrategySpec wants.

    `created_at` is overwritten with the system clock. The model had to supply the
    field because the schema requires it, and it duly invented one - a strategy
    admitted today came back with created_at 2024-06-14. That field orders
    probation (StrategySelector sorts PROBATION candidates by created_at), so a
    fabricated timestamp silently reorders promotion. A timestamp is the system's
    to record, not the model's to invent.
    """
    params_source = _KIND_PARAM_SCHEMA[kind]
    params = {key: spec[key] for key in params_source if key in spec}
    _normalise_units(params)
    return {
        "strategy_id": spec.get("strategy_id"),
        "name": spec.get("name"),
        "kind": kind,
        "symbols": spec.get("symbols"),
        "parameters": params,
        "max_position_value": spec.get("max_position_value"),
        "enabled": spec.get("enabled", True),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "rationale": spec.get("rationale"),
    }


def _normalise_units(params: dict) -> None:
    """Convert a percentage into the fraction the schema and validator want.

    Measured on the real models: the constrained decoder enforces `required` and
    `enum` but ignores numeric `minimum`/`maximum`, so a schema that says
    threshold_pct <= 0.2 does not stop the model answering 1.5, 0.5 or 2.5 - it
    reads the field as a percentage every time, and the prompt saying "0.02 means
    2 percent" only moved it from 1.5 to 0.5. A value above the maximum and within
    100 is therefore rescaled by a factor of 100, which is a unit conversion
    rather than a value the agent invented: 1.5 means 1.5 percent, so 0.015.

    Without this every TREND_FOLLOW proposal is rejected by StrategySpec and the
    curriculum silently falls back.
    """
    threshold = params.get("threshold_pct")
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        value = float(threshold)
        if 0.2 < value <= 100.0:
            params["threshold_pct"] = value / 100.0


def _task_id(kind: str, spec: dict) -> str:
    raw = f"{kind}:{spec.get('strategy_id')}:{datetime.now(tz=timezone.utc).isoformat()}"
    return f"task-{abs(hash(raw)) % 1000000:06d}"


def _required_action(coverage: Mapping[str, object]) -> str | None:
    """The single action the next strategy must express, if the library lacks one.

    Reported as a flat string because it is consumed by a three-item kind enum, not
    by free text. HOLD_BASELINE cannot satisfy it: holding is not an exit.
    """
    uncovered = coverage.get("uncovered_actions_now")
    if not uncovered:
        return None
    actions = [item.upper() for item in coerce.field_str_tuple(uncovered, "uncovered_actions_now")]
    return actions[0] if len(actions) == 1 else "|".join(actions)


def _capability_coverage(
    strategies: Sequence[StrategySpec], last_price: float | None, symbol: str
) -> dict[str, object]:
    """Which actions the selectable library can actually produce right now.

    Nominal coverage is not coverage. TREND_FOLLOW derives its side from price, so
    on paper the library could always sell - but the live strategy is anchored to a
    June reference of 735.01 and SPY trades at 767, so it emits BUY and would keep
    emitting BUY while price stays above 727.6. The library therefore had an exit on
    paper and none in practice, and a coverage report based on declared actions said
    "SELL: covered" anyway.

    So each selectable strategy is evaluated at the real last price from the broker
    snapshot and the actions it actually yields are counted. When no price is
    available the result is marked unknown rather than assumed.
    """
    selectable = [
        strategy
        for strategy in strategies
        if strategy.enabled and strategy.lifecycle not in {"PAUSED", "RETIRED"}
    ]
    declared: dict[str, list[str]] = {}
    executable: dict[str, list[str]] = {}
    for strategy in selectable:
        action = str(strategy.parameters.get("action", "")).upper()
        if action:
            declared.setdefault(action, []).append(strategy.strategy_id)
        if last_price is None or strategy.kind not in {"FIXED_SIZE", "TREND_FOLLOW"}:
            continue
        produced = _producible_action(strategy, last_price, symbol)
        if produced:
            executable.setdefault(produced, []).append(strategy.strategy_id)

    return {
        "evaluated_at_last_price": last_price,
        "selectable_strategy_ids": [strategy.strategy_id for strategy in selectable][:40],
        "selectable_strategy_count": len(selectable),
        "declared_actions": {key: sorted(set(value))[:20] for key, value in sorted(declared.items())},
        "executable_actions_now": {
            key: sorted(set(value))[:20] for key, value in sorted(executable.items())
        },
        "uncovered_actions_now": (
            None if last_price is None else sorted({"BUY", "SELL"} - set(executable))
        ),
    }


def _producible_action(strategy: StrategySpec, last_price: float, symbol: str) -> str | None:
    """The action this strategy would emit at `last_price`, or None if it holds.

    Only the strategy's own sizing and threshold logic is consulted. Guardian is
    not simulated: it stays authoritative at execution time, and the curriculum is
    being told what the library can express, not what would be approved.
    """
    from min_agent.models import AccountSnapshot, DataSnapshot
    from min_agent.strategy_engine import StrategyExecutor

    try:
        snapshot = DataSnapshot(
            symbol=symbol,
            timestamp=datetime.now(tz=timezone.utc),
            market_open=True,
            last_price=last_price,
            source="curriculum_capability_probe",
            account=AccountSnapshot(
                equity=0.0, cash=0.0, buying_power=0.0, portfolio_value=0.0, daily_loss=0.0
            ),
        )
        decision = StrategyExecutor().decide(strategy, snapshot)
    except Exception:
        return None
    return decision.action if decision.action in {"BUY", "SELL"} else None


def _unmet_capability(task: CurriculumTask, context: Mapping[str, object]) -> str | None:
    """The action the library cannot perform that this task does not supply."""
    coverage = context.get("capability_coverage")
    if not isinstance(coverage, Mapping):
        return None
    uncovered = coverage.get("uncovered_actions_now")
    if not uncovered:
        return None
    spec = task.strategy_spec
    if spec is None:
        return ", ".join(str(item) for item in uncovered)
    supplied = {str(spec.parameters.get("action", "")).upper()}
    if spec.kind == "TREND_FOLLOW":
        # A trend follower takes its side from price, so it can cover either - but
        # only in one regime at a time. Treating every TREND_FOLLOW as covering
        # SELL let three consecutive BUY-only proposals count as filling the gap.
        # It counts only if it actually produces the action at the real price.
        supplied = set()
    last_price = coverage.get("evaluated_at_last_price")
    if isinstance(last_price, (int, float)):
        produced = _producible_action(spec, float(last_price), "SPY")
        if produced:
            supplied.add(produced)
    for action in uncovered:
        if str(action).upper() in supplied:
            return None
    return ", ".join(str(item) for item in uncovered)


def _capability_demand(attempts: list[str], context: Mapping[str, object]) -> dict[str, object]:
    coverage = context.get("capability_coverage")
    price = coverage.get("evaluated_at_last_price") if isinstance(coverage, Mapping) else None
    return {
        "uncovered_actions_now": (
            coverage.get("uncovered_actions_now") if isinstance(coverage, Mapping) else None
        ),
        "evaluated_at_last_price": price,
        "strategy_ids_already_in_use": list(
            coerce.field_str_tuple(context.get("strategy_ids_in_use"), "strategy_ids_in_use")
        )[-25:],
        "instruction": (
            "The library cannot express the uncovered action at the real last "
            "price, so the positions in open_positions have no working exit. "
            "Propose a spec that expresses it: FIXED_SIZE with parameters.action "
            "set to that action, or TREND_FOLLOW. Keep quantity small enough that "
            "max_position_value covers it at last_price, set confidence at or above "
            "the guardian minimum, use last_price for any reference_price, and "
            "give strategy_id a value not present in strategy_ids_already_in_use."
        ),
    }
