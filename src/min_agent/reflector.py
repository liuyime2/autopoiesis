from __future__ import annotations

from min_agent.models import CycleRecord


class BasicReflector:
    def summarize(self, records: list[CycleRecord]) -> str:
        if not records:
            return "no records"
        submitted = sum(1 for record in records if record.execution.status == "SUBMITTED")
        rejected = sum(1 for record in records if record.execution.status == "REJECTED")
        skipped = sum(1 for record in records if record.execution.status == "SKIPPED")
        return f"records={len(records)} submitted={submitted} rejected={rejected} skipped={skipped}"
