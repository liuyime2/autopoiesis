"""A record already on the journal must stay readable, and a bad write must still fail.

Measured 2026-10-15, with the market open. `market-open-verify` failed with
`CORRUPT_HISTORY`: five XLE cycles were unreadable. The cause was a value written by the
fallback that is not in `HoldReason`'s literal set - `llm_unavailable_exposure_blocked`.
The record appended cleanly, so the write looked successful, and it then failed validation
on the way back in.

The fix is asymmetric on purpose, and both halves are asserted here:

- the **writer** refuses an unknown value, so the bug cannot recur
- the **reader** reads one as `other`, so the cycles already written are not lost

Only the second half can be tested by writing one, because the first half stops it. That
asymmetry is why the reader's test constructs the payload directly.

All fixtures synthetic.

Usage: pytest tests/autopoiesis/test_hold_reason_read_tolerance.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import CycleRecord, TradeDecision

BASE = {
    "symbol": "XLE", "action": "HOLD", "quantity": 0, "confidence": 0.0,
    "rationale": "synthetic",
}


# --- the reader does not lose a cycle ----------------------------------------


def _a_real_record_with(reason: str) -> str | None:
    """A complete cycle record from the live journal, with `hold_reason` replaced.

    Built from a real line rather than a hand-written literal: a minimal JSON blob fails
    for a dozen unrelated reasons, and a test that passes or fails for the wrong one is
    worse than no test. None when there is no runtime tree, which is the fresh-clone case.
    """
    import json as _json

    path = Path("runtime/autopoiesis/journal.jsonl")
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if '"execution":' not in line:
            continue
        payload = _json.loads(line)
        payload["decision"]["hold_reason"] = reason
        return _json.dumps(payload)
    return None


def test_an_unknown_hold_reason_is_read_as_other():
    """The exact value that made five cycles unreadable.

    Through the reader, not through `TradeDecision.model_validate`: the model stays strict
    because the writer is where a bad value belongs, and the tolerance is the journal
    reader's - the only place a record from an earlier version arrives.
    """
    from autopoiesis.journal import _read_tolerantly

    stripped = _a_real_record_with("llm_unavailable_exposure_blocked")
    if stripped is None:
        pytest.skip("no runtime journal on this host")

    parsed = _read_tolerantly(CycleRecord, stripped)

    assert parsed is not None, "an unrecognised value must not cost the cycle"
    assert parsed.decision.hold_reason == "other", (
        "`other` is the honest reading: the literal that means none of the named states"
    )


@pytest.mark.parametrize(
    "reason",
    ["no_signal", "risk_limit_near", "market_uncertain", "await_confirmation", "other"],
)
def test_a_known_hold_reason_is_untouched(reason):
    assert TradeDecision.model_validate({**BASE, "hold_reason": reason}).hold_reason == reason


def test_the_writer_still_refuses_an_unknown_value():
    """The other half of the asymmetry, and the reason it is asymmetric.

    Coercing on construction was tried and reverted: `test_hold_reason_is_validated` pins
    that a bad value is refused where it is written, and it was right to.
    """
    with pytest.raises(Exception):
        TradeDecision(**{**BASE, "hold_reason": "llm_unavailable_exposure_blocked"})


def test_a_record_that_fails_for_another_reason_is_still_dropped():
    """Tolerance is for the one field that needs it, not for anything malformed."""
    from autopoiesis.journal import _read_tolerantly

    stripped = '{"decision":{"symbol":"XLE","action":"NOT_AN_ACTION","quantity":0}}'
    assert _read_tolerantly(CycleRecord, stripped) is None


def test_a_missing_hold_reason_is_still_none():
    """Absent is not unknown. `None` means the field was not recorded at all."""
    assert TradeDecision.model_validate(BASE).hold_reason is None


def test_the_live_journal_has_no_dropped_lines():
    """The measurement that started this, asserted where it can be re-run.

    Skipped when the runtime tree is absent, which is the fresh-clone case: this is a
    statement about the record, not about the code.
    """
    path = Path("runtime/autopoiesis/journal.jsonl")
    if not path.is_file():
        pytest.skip("no runtime journal on this host")
    journal = JsonlJournal(path)
    journal._scan(CycleRecord, limit=None)

    assert journal.last_dropped_lines == 0, (
        f"{journal.last_dropped_lines} cycle record(s) cannot be read back; the first "
        f"sample is {journal.last_dropped_samples[:1]}"
    )
