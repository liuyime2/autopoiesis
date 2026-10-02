from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from min_agent import coerce
from min_agent.atomicio import write_text_atomic
from min_agent.evaluator import DeterministicEvaluator
from min_agent.models import (
    BrokerEvidenceBatch,
    BrokerFillActivity,
    CycleRecord,
    ReflectionRecord,
    StrategyResult,
)


class ReflectionMemory:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def reflect(
        self,
        records: list[CycleRecord],
        evidence: BrokerEvidenceBatch | None = None,
        fills: Mapping[str, float] | None = None,
        seeded_fills: Sequence[BrokerFillActivity] | None = None,
    ) -> ReflectionRecord:
        evaluation = DeterministicEvaluator().evaluate(
            records, evidence=evidence, fills=fills, seeded_fills=seeded_fills
        )
        strategy_metrics = {
            sid: ev.model_dump(mode="json") for sid, ev in evaluation.strategy_metrics.items()
        }
        strategy_scores = {
            sid: ev.score for sid, ev in evaluation.strategy_metrics.items()
        }
        summary = (
            f"records={evaluation.window_cycles} submitted={evaluation.submitted_orders} "
            f"rejected={evaluation.rejected_orders} errors={evaluation.error_count} "
            f"skipped={evaluation.skipped_orders} strategies={len(strategy_scores)} "
            f"pnl_evidence={evaluation.pnl_evidence}"
        )
        return ReflectionRecord(
            generated_at=datetime.now(tz=timezone.utc),
            window_cycles=evaluation.window_cycles,
            submitted_orders=evaluation.submitted_orders,
            rejected_orders=evaluation.rejected_orders,
            error_count=evaluation.error_count,
            guardian_rejections=evaluation.guardian_rejections,
            strategy_scores=strategy_scores,
            summary=summary,
            evaluation=evaluation.model_dump(mode="json"),
            strategy_metrics=strategy_metrics,
        )

    def save(self, record: ReflectionRecord) -> None:
        write_text_atomic(self.path, record.model_dump_json(indent=2) + "\n")

    def load(self) -> ReflectionRecord | None:
        if not self.path.exists():
            return None
        return ReflectionRecord.model_validate_json(self.path.read_text(encoding="utf-8"))

    def strategy_results(self, record: ReflectionRecord) -> list[StrategyResult]:
        if record.strategy_metrics:
            results = []
            for strategy_id, metrics in record.strategy_metrics.items():
                results.append(
                    StrategyResult(
                        strategy_id=strategy_id,
                        cycles=coerce.field_int(metrics.get("cycles"), f"{strategy_id}.cycles"),
                        submitted_orders=coerce.field_int(
                            metrics.get("submitted_orders"), f"{strategy_id}.submitted_orders"
                        ),
                        rejected_orders=coerce.field_int(
                            metrics.get("rejected_orders"), f"{strategy_id}.rejected_orders"
                        ),
                        errors=coerce.field_int(metrics.get("errors"), f"{strategy_id}.errors"),
                        score=record.strategy_scores.get(strategy_id, 0.0),
                        evaluated_at=record.generated_at,
                        skipped_orders=coerce.field_int(
                            metrics.get("skipped_orders"), f"{strategy_id}.skipped_orders"
                        ),
                        action_counts=coerce.field_count_dict(
                            metrics.get("action_counts"), f"{strategy_id}.action_counts"
                        ),
                        guardian_rejections=coerce.field_count_dict(
                            metrics.get("guardian_rejections"), f"{strategy_id}.guardian_rejections"
                        ),
                        intended_notional=coerce.field_float_or(
                            metrics.get("intended_notional"), f"{strategy_id}.intended_notional", 0.0
                        ),
                        filled_quantity=coerce.field_float_or(
                            metrics.get("filled_quantity"), f"{strategy_id}.filled_quantity", 0.0
                        ),
                        realized_pnl=coerce.field_float(
                            metrics.get("realized_pnl"), f"{strategy_id}.realized_pnl"
                        ),
                        fees=coerce.field_float(metrics.get("fees"), f"{strategy_id}.fees"),
                        pnl_evidence=coerce.field_str(
                            metrics.get("pnl_evidence"),
                            f"{strategy_id}.pnl_evidence",
                            "missing_fill_price_and_broker_activity",
                        ),
                        trade_attempts=coerce.field_int(
                            metrics.get("trade_attempts"), f"{strategy_id}.trade_attempts"
                        ),
                        strategy_fault_rejections=(
                            coerce.field_int(
                                metrics.get("strategy_fault_rejections"),
                                f"{strategy_id}.strategy_fault_rejections",
                            )
                            if metrics.get("strategy_fault_rejections") is not None
                            else None
                        ),
                    )
                )
            return results

        return [
            StrategyResult(
                strategy_id=strategy_id,
                cycles=record.window_cycles,
                submitted_orders=record.submitted_orders,
                rejected_orders=record.rejected_orders,
                errors=record.error_count,
                score=score,
                evaluated_at=record.generated_at,
            )
            for strategy_id, score in record.strategy_scores.items()
        ]
