from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from min_agent.evaluator import DeterministicEvaluator
from min_agent.models import BrokerEvidenceBatch, CycleRecord, ReflectionRecord, StrategyResult


class ReflectionMemory:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def reflect(self, records: list[CycleRecord], evidence: BrokerEvidenceBatch | None = None) -> ReflectionRecord:
        evaluation = DeterministicEvaluator().evaluate(records, evidence=evidence)
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(record.model_dump_json(indent=2), encoding="utf-8")

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
                        cycles=metrics.get("cycles", 0),
                        submitted_orders=metrics.get("submitted_orders", 0),
                        rejected_orders=metrics.get("rejected_orders", 0),
                        errors=metrics.get("errors", 0),
                        score=record.strategy_scores.get(strategy_id, 0.0),
                        evaluated_at=record.generated_at,
                        skipped_orders=metrics.get("skipped_orders", 0),
                        action_counts=metrics.get("action_counts", {}),
                        guardian_rejections=metrics.get("guardian_rejections", {}),
                        intended_notional=metrics.get("intended_notional", 0.0),
                        filled_quantity=metrics.get("filled_quantity", 0.0),
                        realized_pnl=metrics.get("realized_pnl"),
                        fees=metrics.get("fees"),
                        pnl_evidence=metrics.get("pnl_evidence", "missing_fill_price_and_broker_activity"),
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