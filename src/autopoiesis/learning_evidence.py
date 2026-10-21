"""Decision attribution for learning, independent of raw account bookkeeping.

Legacy records have unknown attribution. Today's mutable strategy library is
only a behavior comparison, never evidence of yesterday's strategy universe.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping

from autopoiesis import coerce
from autopoiesis.models import CycleRecord, StrategyProvenance, StrategySpec

ELIGIBILITY_VERSION = 1


def capture(strategy: StrategySpec) -> StrategyProvenance:
    behavior = {key: getattr(strategy, key) for key in (
        "strategy_id", "kind", "symbols", "parameters", "max_position_value",
    )}
    encoded = json.dumps(behavior, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return StrategyProvenance(
        strategy_id=strategy.strategy_id, universe=strategy.symbols,
        behavior_json=encoded, behavior_digest=hashlib.sha256(encoded.encode()).hexdigest(),
    )


def valid_provenance(value: object) -> StrategyProvenance | None:
    try:
        p = value if isinstance(value, StrategyProvenance) else StrategyProvenance.model_validate(value)
        behavior = json.loads(p.behavior_json)
        if set(behavior) != {"strategy_id", "kind", "symbols", "parameters", "max_position_value"}:
            return None
        if behavior["strategy_id"] != p.strategy_id or tuple(behavior["symbols"]) != p.universe:
            return None
        if hashlib.sha256(p.behavior_json.encode()).hexdigest() != p.behavior_digest:
            return None
        return p
    except (ValueError, TypeError, KeyError):
        return None


def eligibility(record: object) -> str:
    decision = coerce.field_of(record, "decision")
    snapshot = coerce.field_of(record, "snapshot")
    p = valid_provenance(coerce.field_of(decision, "strategy_provenance"))
    if p is None:
        return "UNKNOWN_PROVENANCE"
    if coerce.field_of(record, "strategy_id") != p.strategy_id or coerce.field_of(decision, "strategy_id") != p.strategy_id:
        return "STRATEGY_ID_MISMATCH"
    symbol = coerce.field_of(snapshot, "symbol")
    if coerce.field_of(decision, "symbol") != symbol:
        return "SYMBOL_MISMATCH"
    if symbol not in p.universe:
        return "OUTSIDE_RECORDED_UNIVERSE"
    return "ELIGIBLE"


def eligible(record: object) -> bool:
    return eligibility(record) == "ELIGIBLE"


def row_eligible(row: Mapping[str, object], strategy: StrategySpec | None = None) -> bool:
    p = valid_provenance(row.get("strategy_provenance"))
    return bool(
        row.get("eligibility_version") == ELIGIBILITY_VERSION
        and row.get("eligibility") == "ELIGIBLE" and p is not None
        and row.get("strategy_id") == p.strategy_id
        and row.get("decision_strategy_id") == p.strategy_id
        and row.get("symbol") == row.get("decision_symbol")
        and row.get("symbol") in p.universe
        and (strategy is None or capture(strategy).behavior_digest == p.behavior_digest)
    )


def source_digest(refs: Iterable[str], records: Mapping[str, object]) -> str | None:
    """Resolve every cited cycle; unresolved or contaminated citations fail closed."""
    sources = []
    for ref in sorted(set(refs)):
        record = records.get(ref)
        if not isinstance(record, CycleRecord) or not eligible(record):
            return None
        sources.append([ref, record.model_dump(mode="json")])
    return hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() if sources else None

def model_departed_from_rule(record) -> bool:
    """Whether this cycle is a departure from the rule *the model* made, and said why.

    The single predicate behind three surfaces - the record, the daily report and the doctor's
    "llm vs rule" figure - because they drifted apart the moment one of them had it. Measured
    2026-10-21: the daily report called 62 decisions departures from the rule and the doctor summed
    316 over nine days; 26 of that day's 62 carried no reason because they were not departures at
    all. The accumulation gate writes `selection_unavailable: ... has not passed the screen` and the
    in-flight guard writes `skip: an identical order ...`; both are the system acting, and both were
    being charged to the model.

    Three conditions, each with its own measurement behind it:

    - the rule had an action to depart from, and this cycle's action differs from it;
    - the system did not change the decision before the Guardian (`intervention` is absent);
    - the model gave a reason. A reasonless disagreement is not taken as the model's judgement -
      over 323 comparable cycles, 112 of 113 disagreements were the model declining a trade with
      nothing recorded about why.
    """
    decision = getattr(record, "decision", None)
    if decision is None:
        return False
    rule_action = getattr(decision, "rule_action", None)
    if rule_action is None or getattr(decision, "action", None) == rule_action:
        return False
    if getattr(record, "intervention", None) is not None:
        return False
    return bool(str(getattr(decision, "override_reason", "") or "").strip())
