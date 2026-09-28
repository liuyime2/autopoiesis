from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

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
    def __init__(self, *, transport: CurriculumTransport | None = None):
        self.transport = transport
        # Track consecutive EVALUATE fallbacks to break dead loops. Reset whenever
        # a STRATEGY_SPEC is generated. When count >= 3 with no_exploration=true,
        # fallback proposes a fresh strategy instead of another EVALUATE.
        self._consecutive_evaluate_count: int = 0

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

        prompt_context = {
            "target_daily_return_pct": target_daily_return_pct,
            "safety": {
                "paper_only": True,
                "guardian_limits_mutable": False,
                "generated_code_allowed": False,
                "real_data_only": True,
            },
            "reflection": reflection.model_dump(mode="json"),
            "strategies": [strategy.model_dump(mode="json") for strategy in current_strategies],
            "recent_exploration_summary": recent_exploration_summary,
            "guardian_max_position_value": guardian_max_position_value,
            "exploration_policy": [
                "No trade is not failure; no exploration is failure.",
                "If recent_exploration_summary.no_exploration is true, propose the next distinct small admissible strategy instead of repeating generic EVALUATE or near-duplicate TREND_FOLLOW.",
                "Do not force orders; Guardian, market-open checks, and paper executor remain authoritative.",
                "Do not claim profitability or target achievement without broker evidence in the supplied context.",
            ],
            "schema": {
                "task_id": "string",
                "task_type": "STRATEGY_SPEC|OPTIMIZE|EVALUATE",
                "summary": "string",
                "strategy_spec": "optional structured StrategySpec; required for STRATEGY_SPEC",
                "parameters": "object of safe scalar params",
                "rationale": "string",
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
        except Exception:
            return self._fallback_task(
                reflection,
                current_strategies,
                recent_exploration_summary=recent_exploration_summary,
                guardian_max_position_value=guardian_max_position_value,
            )
        if task.task_type == "STRATEGY_SPEC" and task.strategy_spec is not None:
            self._consecutive_evaluate_count = 0
        return task

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
            self._consecutive_evaluate_count = 0
            return CurriculumTask(
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
            self._consecutive_evaluate_count = 0
            return CurriculumTask(
                task_id="fallback-tiny-fixed-size",
                task_type="STRATEGY_SPEC",
                summary="create a tiny executable fixed-size skill",
                strategy_spec=spec,
                rationale="progress from baseline to a small executable probation strategy without forcing execution",
            )

        if recent_exploration_summary and recent_exploration_summary.get("no_exploration"):
            # Break out of EVALUATE dead-loop: after 3 consecutive EVALUATEs with
            # no exploration progress, propose a distinct small trading skill so
            # admission/Guardian can actually react to new behavior. Real journal
            # evidence (no SELLs, all HOLDs) shows EVALUATE alone is insufficient.
            if self._consecutive_evaluate_count >= 3:
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
                self._consecutive_evaluate_count = 0
                return CurriculumTask(
                    task_id=strategy_id,
                    task_type="STRATEGY_SPEC",
                    summary="break out of EVALUATE dead-loop with a distinct small executable skill",
                    strategy_spec=spec,
                    rationale=f"{self.__class__.__name__} observed {attempt} consecutive EVALUATE fallbacks while no_exploration remained true",
                )
            self._consecutive_evaluate_count += 1
            return CurriculumTask(
                task_id="evaluate-no-exploration",
                task_type="EVALUATE",
                summary="evaluate no-exploration window before creating another strategy",
                parameters={"window_cycles": reflection.window_cycles, "no_exploration": True},
                rationale="recent real journal evidence shows no exploration; wait for transport/admission to propose a distinct safe strategy",
            )

        self._consecutive_evaluate_count += 1
        return CurriculumTask(
            task_id="evaluate-recent-performance",
            task_type="EVALUATE",
            summary="evaluate recent strategy outcomes before changing behavior",
            parameters={"window_cycles": reflection.window_cycles},
            rationale="use measured paper outcomes before creating or selecting new strategies",
        )
