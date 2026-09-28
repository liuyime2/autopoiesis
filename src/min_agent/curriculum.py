from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from min_agent.atomicio import read_json, write_json_atomic
from min_agent.models import CurriculumTask, ReflectionRecord, STRATEGY_PARAMETER_SCHEMAS, StrategySpec

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
        self.last_failure: str | None = None
        self.last_raw_excerpt: str = ""
        self._consecutive_evaluate_count: int = self._load_count()

    def propose(
        self,
        *,
        reflection: ReflectionRecord,
        current_strategies: list[StrategySpec],
        target_daily_return_pct: float = 10.0,
        recent_exploration_summary: dict[str, object] | None = None,
        guardian_max_position_value: float | None = None,
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
            "strategies": self._compact_strategies(current_strategies, reflection),
            "strategy_count_total": len(current_strategies),
            "recent_exploration_summary": recent_exploration_summary,
            "guardian_max_position_value": guardian_max_position_value,
            "exploration_policy": [
                "No trade is not failure; no exploration is failure.",
                "If recent_exploration_summary.no_exploration is true, propose the next distinct small admissible strategy instead of repeating generic EVALUATE or near-duplicate TREND_FOLLOW.",
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
                    "required_params": list(schema["required"]),
                    "allowed_params": list(schema["allowed"]),
                    "description": schema["description"],
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
        raw = self.transport(prompt_context)
        try:
            task = parse_curriculum_task_json(raw)
        except Exception as exc:
            # The reason the model output was unusable belongs in the journal.
            # Swallowing it made a 100% fallback rate look like normal operation:
            # the journal said "fallback" without saying why, which is how the
            # self-evolution loop stayed dead while every health check was green.
            self.last_failure = f"{type(exc).__name__}: {exc}"
            self.last_raw_excerpt = raw[-500:] if isinstance(raw, str) else ""
            return self._fallback_task(
                reflection,
                current_strategies,
                recent_exploration_summary=recent_exploration_summary,
                guardian_max_position_value=guardian_max_position_value,
            )
        if task.task_type == "STRATEGY_SPEC" and task.strategy_spec is not None:
            self._set_count(0)
        return task

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
            def rank(spec: StrategySpec) -> tuple[int, str]:
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


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
