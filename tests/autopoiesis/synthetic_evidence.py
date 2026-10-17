"""Explicit synthetic provenance for fixtures intended to represent clean cycles.

Never import this module in production. The synthetic behavior is an actual
StrategySpec captured at fixture construction, not an inferred historical file.
"""

from datetime import datetime, timezone

from autopoiesis.learning_evidence import ELIGIBILITY_VERSION, capture
from autopoiesis.models import StrategySpec


def synthetic_spec(strategy_id="synthetic-strategy", symbol="SPY"):
    return StrategySpec(
        strategy_id=strategy_id, name="Synthetic fixture", kind="HOLD_BASELINE",
        symbols=(symbol,), parameters={}, max_position_value=1000,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc), rationale="synthetic test fixture",
    )


def synthetic_provenance(strategy_id="synthetic-strategy", symbol="SPY"):
    return capture(synthetic_spec(strategy_id, symbol))


def synthetic_clean_record(record):
    sid = record.strategy_id or record.decision.strategy_id or "synthetic-strategy"
    return record.model_copy(update={
        "strategy_id": sid,
        "decision": record.decision.model_copy(update={
            "strategy_id": sid, "strategy_provenance": synthetic_provenance(sid, record.snapshot.symbol),
        }),
    })


def synthetic_row_metadata(strategy_id="synthetic-strategy", symbol="SPY"):
    return {
        "eligibility_version": ELIGIBILITY_VERSION, "eligibility": "ELIGIBLE",
        "strategy_id": strategy_id, "decision_strategy_id": strategy_id,
        "symbol": symbol, "decision_symbol": symbol,
        "strategy_provenance": synthetic_provenance(strategy_id, symbol).model_dump(mode="json"),
    }


def synthetic_clean_events(events):
    """Use only where the fixture explicitly intends clean decision attribution."""
    for event in events:
        payload = event["payload"] if isinstance(event, dict) else event.payload
        for row in payload["rows"]:
            row.update(synthetic_row_metadata(row.get("strategy_id", "synthetic-strategy")))
    return events
