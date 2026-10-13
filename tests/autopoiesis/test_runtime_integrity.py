"""The integrity checker must actually fail.

`tools/check_runtime_integrity.py` carries an exception list so a real historical
defect does not keep the gate red forever. An exception list that also silences new
breakage is worse than no checker, so each rule is tested against a planted
violation. It also earned its keep immediately: its first three versions reported
228 phantom duplicate cycles, three bogus "unknown lifecycle" strategies and
dozens of missing rule fields, none of which existed.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_runtime_integrity", ROOT / "tools" / "check_runtime_integrity.py"
)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _runtime(tmp_path, cycles=(), events=(), strategies=(), knowledge=()):
    runtime = tmp_path / "autopoiesis"
    (runtime / "strategies").mkdir(parents=True, exist_ok=True)
    (runtime / "knowledge").mkdir(parents=True, exist_ok=True)
    lines = []
    for i, c in enumerate(cycles):
        lines.append(json.dumps({
            "cycle_id": c, "strategy_id": "s1",
            "snapshot": {"timestamp": "2026-06-01T14:30:00+00:00", "last_price": 100.0,
                         "symbol": "SPY"},
            "decision": {"action": "HOLD", "decision_source": "baseline"},
        }))
    for i, e in enumerate(events):
        lines.append(json.dumps({"cycle_id": None, "event_type": e[0],
                                 "strategy_id": e[1] if len(e) > 1 else None,
                                 "payload": e[2] if len(e) > 2 else {}}))
    (runtime / "journal.jsonl").write_text(
        "".join(l + "\n" for l in lines), errors="replace"
    )
    for spec in strategies:
        (runtime / "strategies" / f"{spec['strategy_id']}.json").write_text(
            json.dumps(spec)
        )
    (runtime / "knowledge" / "artifacts.jsonl").write_text(
        "".join(json.dumps(k) + "\n" for k in knowledge), errors="replace"
    )
    return runtime


def _spec(sid="s1", **over):
    base = {
        "strategy_id": sid, "name": "n", "kind": "TREND_FOLLOW",
        "symbols": ["SPY"], "parameters": {"quantity": 1},
        "max_position_value": 5000.0, "enabled": True,
        "lifecycle": "ACTIVE", "created_at": "2026-06-01T00:00:00Z",
        "rationale": "r",
    }
    base.update(over)
    return base


def _run(tmp_path, runtime):
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"known_missing": []}))
    return checker.main(runtime=runtime, findings=findings)


def test_a_clean_runtime_passes(tmp_path):
    runtime = _runtime(
        tmp_path, cycles=["c1"],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
        strategies=[_spec()],
    )
    assert _run(tmp_path, runtime) == 0


def test_a_newly_admitted_strategy_with_no_file_fails(tmp_path):
    """The exception list names specific ids, so a different one must still fail."""
    runtime = _runtime(
        tmp_path,
        events=[("STRATEGY_ADMISSION_REVIEWED", "brand-new", {"accepted": True})],
        strategies=[_spec()],
    )
    assert _run(tmp_path, runtime) == 1


def test_a_rejected_proposal_with_no_file_is_fine(tmp_path):
    """A rejected strategy correctly never enters the library. Flagging these was
    the first version's bug."""
    runtime = _runtime(
        tmp_path,
        events=[("STRATEGY_ADMISSION_REVIEWED", "rejected-one", {"accepted": False})],
        strategies=[_spec()],
    )
    assert _run(tmp_path, runtime) == 0


def test_a_recorded_retirement_excuses_a_missing_file(tmp_path):
    runtime = _runtime(
        tmp_path,
        events=[
            ("STRATEGY_ADMISSION_REVIEWED", "gone", {"accepted": True}),
            ("STRATEGY_LIFECYCLE_UPDATED", "gone", {"new_lifecycle": "RETIRED"}),
        ],
        strategies=[_spec()],
    )
    assert _run(tmp_path, runtime) == 0


def test_a_cycle_written_twice_fails(tmp_path):
    """A cycle is the unit every aggregate is built from."""
    runtime = _runtime(tmp_path, cycles=["c1", "c1"], strategies=[_spec()])
    assert _run(tmp_path, runtime) == 1


def test_an_event_reusing_a_cycle_id_is_not_a_duplicate(tmp_path):
    """An event carries cycle_id as a foreign key. The first version reported 228
    phantom duplicates from exactly this."""
    lines = [
        json.dumps({"cycle_id": "c1", "snapshot": {"timestamp": "t"},
                    "decision": {"action": "HOLD"}}),
        json.dumps({"cycle_id": "c1", "event_type": "REFLECTION_GENERATED",
                    "payload": {}}),
        json.dumps({"cycle_id": "c1", "event_type": "STRATEGY_EVALUATION_RECORDED",
                    "payload": {}}),
    ]
    runtime = tmp_path / "autopoiesis"
    (runtime / "strategies").mkdir(parents=True)
    (runtime / "journal.jsonl").write_text("".join(l + "\n" for l in lines))
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"known_missing": []}))
    assert checker.main(runtime=runtime, findings=findings) == 0


@pytest.mark.parametrize("lifecycle", sorted(checker.LIFECYCLES))
def test_every_declared_lifecycle_is_accepted(tmp_path, lifecycle):
    runtime = _runtime(
        tmp_path, strategies=[_spec(lifecycle=lifecycle)],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
    )
    assert _run(tmp_path, runtime) == 0, f"{lifecycle} is a real lifecycle value"


def test_an_unknown_lifecycle_fails(tmp_path):
    runtime = _runtime(
        tmp_path, strategies=[_spec(lifecycle="BANANA")],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
    )
    assert _run(tmp_path, runtime) == 1


def test_a_hold_baseline_with_no_parameters_passes(tmp_path):
    """A HOLD_BASELINE legitimately carries no parameters. Requiring them flagged
    the baseline strategy as corrupt."""
    runtime = _runtime(
        tmp_path, strategies=[_spec(kind="HOLD_BASELINE", parameters={},
                                   lifecycle="BASELINE")],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
    )
    assert _run(tmp_path, runtime) == 0


def test_a_forged_created_at_fails(tmp_path):
    """created_at is system-owned; a 2025 timestamp means an LLM authored it."""
    runtime = _runtime(
        tmp_path, strategies=[_spec(created_at="2025-06-01T00:00:00Z")],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
    )
    assert _run(tmp_path, runtime) == 1


def test_a_corrupt_strategy_file_fails(tmp_path):
    runtime = _runtime(tmp_path, strategies=[_spec()])
    (runtime / "strategies" / "s1.json").write_text("{not json")
    assert _run(tmp_path, runtime) == 1


def test_duplicate_knowledge_artifacts_fail(tmp_path):
    runtime = _runtime(
        tmp_path, strategies=[_spec()],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
        knowledge=[{"claim": "be careful"}, {"claim": "Be careful"}],
    )
    assert _run(tmp_path, runtime) == 1


def _cycle_with_symbol(ts, symbol, sid="s1"):
    """A cycle record whose strategy does not cover `symbol`."""
    return {
        "cycle_id": "", "strategy_id": sid,
        "snapshot": {"timestamp": ts, "last_price": 91.5, "symbol": symbol},
        "decision": {"action": "HOLD", "decision_source": "llm"},
    }


def test_a_strategy_served_for_a_symbol_it_does_not_cover_fails(tmp_path):
    """The rule the four-day contamination would have been caught by.

    """
    # The checker's own boundary, so the planted violation is unambiguously after the fix.
    after = "2099-01-01T00:00:00+00:00"
    runtime = _runtime(
        tmp_path,
        strategies=[_spec(symbols=["SPY"])],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
    )
    rec = _cycle_with_symbol(after, "BIL")
    rec["cycle_id"] = "c-bil"
    with (runtime / "journal.jsonl").open("a") as fh:
        fh.write(json.dumps(rec) + "\n")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(checker, "MISMATCH_FIX_UTC", "2026-10-12T20:20:00+00:00")
    try:
        assert _run(tmp_path, runtime) == 1, (
            "a cycle that selected a strategy for a symbol it does not cover is a recurrence and "
            "must fail the gate, not be excused"
        )
    finally:
        monkey.undo()


def test_a_pre_fix_mismatch_is_counted_and_excused(tmp_path):
    """The historical cohort is reported, not silently dropped and not a blocker.

    The boundary does the work: a violation journalled before the fix instant is part of the
    measured contamination and the note says how many there are, so the number is visible and
    rotation can retire the entry. Nothing after the instant may add to it.
    """
    import contextlib
    import io

    before = "2026-10-09T15:00:00+00:00"
    runtime = _runtime(
        tmp_path,
        strategies=[_spec(symbols=["SPY"])],
        events=[("STRATEGY_ADMISSION_REVIEWED", "s1", {"accepted": True})],
    )
    rec = _cycle_with_symbol(before, "BIL")
    rec["cycle_id"] = "c-old-bil"
    with (runtime / "journal.jsonl").open("a") as fh:
        fh.write(json.dumps(rec) + "\n")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(checker, "MISMATCH_FIX_UTC", "2026-10-12T20:20:00+00:00")
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = checker.main(runtime=runtime,
                                findings=tmp_path / "findings.json")
        assert code == 0, "a pre-fix cycle is the measured defect, not a new one"
        assert "pre-fix cycle(s)" in buffer.getvalue(), (
            "the cohort must be counted out loud; an exemption nobody can see is a bypass"
        )
    finally:
        monkey.undo()


def test_a_strategy_absent_from_the_registry_is_not_judged(tmp_path):
    """Retired and pruned strategies cannot be checked for coverage, so they are skipped.

    Judging them would fail the gate for a file that no longer exists - the same direction the
    admission check takes.
    """
    runtime = _runtime(
        tmp_path,
        strategies=[],
        events=[("STRATEGY_ADMISSION_REVIEWED", "ghost", {"accepted": True})],
    )
    rec = {"cycle_id": "c-ghost", "strategy_id": "ghost",
           "snapshot": {"timestamp": "2099-01-01T00:00:00+00:00", "symbol": "TLT",
                        "last_price": 77.9},
           "decision": {"action": "HOLD", "decision_source": "llm"}}
    with (runtime / "journal.jsonl").open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"known_missing": [{"strategy_id": "ghost"}]}))
    assert checker.main(runtime=runtime, findings=findings) == 0
