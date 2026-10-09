"""The last two checks with no failure-path test.

`champion / search` and `experiment chain` were the two names left uncovered after
section 41, both because they need a journal seeded with specific events plus a
real strategy library. Building those fixtures is the work; asserting only the happy
path would have left the two most consequential claims in the audit unpinned:

- a champion is designated **only** from broker-verified positive realized PnL, never
  from cycle counts or an in-sample backtest;
- a *tradable* strategy with no evaluation evidence is a genuine hole, distinct from
  the historical chains that predate the offline screen.

Both are driven through a real journal file and a real strategy directory rather
than a stub, because a stub would only have proved the stub works.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from autopoiesis import doctor as doctor_module
from autopoiesis.doctor import (
    DoctorReport,
    _check_champion_and_search,
    _check_experiment_chain,
)
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import JournalEvent

TS = datetime(2026, 6, 20, 15, 0, tzinfo=timezone.utc)


class _Cfg:
    def __init__(self, strategy_dir, journal_path):
        self.strategy_dir = str(strategy_dir)
        self.journal_path = journal_path
        self.knowledge_dir = "unused"
        self.allowlist = ["SPY"]


def _spec(strategy_id, lifecycle="ACTIVE", kind="FIXED_SIZE"):
    return {
        "strategy_id": strategy_id, "name": strategy_id, "kind": kind,
        "symbols": ["SPY"],
        "parameters": {"action": "BUY", "quantity": 1, "confidence": 0.6}
        if kind == "FIXED_SIZE"
        else {},
        "max_position_value": 5000.0,
        "created_at": "2026-06-20T14:00:00Z", "rationale": "r",
        "enabled": True, "lifecycle": lifecycle,
    }


def _library(tmp_path, specs):
    directory = tmp_path / "strategies"
    directory.mkdir(exist_ok=True)
    for spec in specs:
        (directory / f"{spec['strategy_id']}.json").write_text(json.dumps(spec))
    return directory


def _journal(tmp_path, events):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    for i, (event_type, strategy_id, payload) in enumerate(events):
        journal.append_event(JournalEvent(
            event_id=f"e{i}", event_type=event_type, timestamp=TS,
            status="SUCCESS", message="m", strategy_id=strategy_id,
            task_id=f"task-{i}", payload=payload,
        ))
    return journal


def _by_name(report, name):
    return {c.name: c for c in report.checks}[name]


# --- champion / search -------------------------------------------------------


def test_no_champion_is_named_without_broker_verified_pnl(tmp_path):
    """The whole point of the check: no PnL evidence means no benchmark.

    Naming a champion from cycles or an in-sample backtest would be choosing a
    benchmark on evidence the system already distrusts.
    """
    directory = _library(tmp_path, [_spec("s1")])
    journal = _journal(tmp_path, [
        ("CURRICULUM_PROPOSED", "s1", {
            "strategy_id": "s1", "rationale": "because",
            "recent_exploration_summary": {"window_cycles": 10, "action_counts": {}},
        }),
    ])
    report = DoctorReport()
    _check_champion_and_search(report, _Cfg(directory, journal.path), journal)
    check = _by_name(report, "champion / search")
    assert "champion=NONE" in check.detail, check.detail


def test_a_candidate_that_cannot_state_its_motivation_warns(tmp_path):
    """A bare rationale is not a justification.

    The proposal schema requires only free-text `rationale`, so a candidate can be
    admitted having said nothing about what observed failure it addresses, what data
    it used, or how it differs from what exists.
    """
    directory = _library(tmp_path, [_spec("s1")])
    journal = _journal(tmp_path, [
        # No `recent_exploration_summary`, so nothing recorded what prompted it.
        ("CURRICULUM_PROPOSED", "s1", {"strategy_id": "s1", "rationale": "feels good"}),
    ])
    report = DoctorReport()
    _check_champion_and_search(report, _Cfg(directory, journal.path), journal)
    check = _by_name(report, "champion / search")
    assert check.status.value == "warn", f"{check.status.value}: {check.detail}"
    assert "cannot state what observation motivated them" in check.detail


def test_a_fully_justified_screened_candidate_does_not_warn(tmp_path):
    """The OK path, so the WARN above cannot be produced by any input at all."""
    directory = _library(tmp_path, [_spec("s1")])
    journal = _journal(tmp_path, [
        ("CURRICULUM_PROPOSED", "s1", {
            "strategy_id": "s1", "rationale": "because",
            "recent_exploration_summary": {"window_cycles": 10},
        }),
        ("OFFLINE_VALIDATION_COMPLETED", "s1", {
            "strategy_id": "s1", "verdict": "PASS_SCREENED",
        }),
    ])
    report = DoctorReport()
    _check_champion_and_search(report, _Cfg(directory, journal.path), journal)
    check = _by_name(report, "champion / search")
    assert check.status.value == "ok", f"{check.status.value}: {check.detail}"
    assert "0 cannot state" in check.detail, check.detail


def test_an_unreadable_strategy_library_is_reported_not_silently_empty(tmp_path):
    journal = _journal(tmp_path, [])
    missing = tmp_path / "does-not-exist"

    class _Exploding:
        def list(self):
            raise PermissionError("nope")

    import autopoiesis.doctor as doctor_module
    original = doctor_module.strategy_engine.StrategyLibrary
    doctor_module.strategy_engine.StrategyLibrary = lambda *_a, **_k: _Exploding()
    try:
        report = DoctorReport()
        _check_champion_and_search(report, _Cfg(missing, journal.path), journal)
    finally:
        doctor_module.strategy_engine.StrategyLibrary = original
    check = _by_name(report, "champion / search")
    assert check.status.value == "warn"
    assert "could not read state" in check.detail


# --- experiment chain --------------------------------------------------------


def _chain_report(tmp_path, specs, events):
    directory = _library(tmp_path, specs)
    journal = _journal(tmp_path, events)
    report = DoctorReport()
    _check_experiment_chain(report, _Cfg(directory, journal.path), journal)
    return _by_name(report, "experiment chain")


def test_a_tradable_strategy_with_no_evaluation_evidence_warns(tmp_path):
    """The hole the check exists for: tradeable, and never evaluated."""
    check = _chain_report(
        tmp_path,
        [_spec("unevaluated", lifecycle="ACTIVE")],
        [("STRATEGY_ADMISSION_REVIEWED", "unevaluated", {"accepted": True, "reason": "ok"})],
    )
    assert check.status.value == "warn", f"{check.status.value}: {check.detail}"
    assert "tradable with no evaluation evidence" in check.detail
    assert "unevaluated" in check.detail


def test_a_fully_chained_strategy_passes(tmp_path):
    check = _chain_report(
        tmp_path,
        [_spec("chained", lifecycle="PROBATION")],
        [
            ("CURRICULUM_PROPOSED", "chained", {"strategy_id": "chained", "rationale": "r"}),
            ("STRATEGY_ADMISSION_REVIEWED", "chained", {"accepted": True, "reason": "ok"}),
            ("OFFLINE_VALIDATION_COMPLETED", "chained", {
                "strategy_id": "chained", "verdict": "PASS_SCREENED"}),
            # "Evaluated" and "screened" are different links in this chain, and I
            # got it wrong first: the tradable test counts
            # STRATEGY_EVALUATION_RECORDED via payload["strategy_metrics"], whose
            # own event carries strategy_id=None. The registry's own comment
            # records someone making exactly this mistake and reporting
            # "tradable with no evaluation evidence" for strategies evaluated 663
            # times, so the test pins the correct field rather than the obvious one.
            ("STRATEGY_EVALUATION_RECORDED", None, {
                "strategy_metrics": {"chained": {"scored": 12, "good_holds": 9}}}),
        ],
    )
    assert check.status.value == "ok", f"{check.status.value}: {check.detail}"


def test_a_retired_strategy_without_evidence_is_not_tradable_so_no_warning(tmp_path):
    """Historical gaps are reported, not failed - only *tradable* is a hole."""
    check = _chain_report(
        tmp_path,
        [_spec("old", lifecycle="RETIRED")],
        [("STRATEGY_ADMISSION_REVIEWED", "old", {"accepted": True, "reason": "ok"})],
    )
    assert "tradable with no evaluation evidence" not in check.detail
    assert "incomplete chain" in check.detail


def test_an_empty_library_is_reported_rather_than_claimed_complete(tmp_path):
    check = _chain_report(tmp_path, [], [])
    assert check.status.value == "warn"
    assert "no strategies to account for" in check.detail


def test_a_model_that_subtracts_from_a_positive_total_fails_the_gate(tmp_path):
    """The severity that must not be softened while the model happens to be up.

    `doctor` reports `pnl attribution` PASS on this host because the model
    contributed +4.59 of +568.98. That is the model's real trading result and the
    rule was not touched to get there - but it also means the FAIL branch has
    nothing exercising it, so a later "this check was too strict, loosen it" edit
    would pass every test in the suite.

    So the branch is driven directly through `_check_pnl_attribution`. An earlier
    version of this rule compared `== 0.0` and reported OK while the model was
    losing money, which is the exact failure this pins shut.
    """
    from autopoiesis import doctor as doctor_module
    from autopoiesis.attribution import Attribution
    from autopoiesis.doctor import _check_pnl_attribution

    config = _Cfg(tmp_path / "strategies", tmp_path / "j.jsonl")
    (tmp_path / "strategies").mkdir(exist_ok=True)

    def severity(model_pnl: float, total: float) -> str:
        # One recorded evidence event is what gets the check past its "no closed
        # lots on record" early return.
        journal = _journal(tmp_path, [
            ("PNL_EVIDENCE_RECORDED", None, {"pnl": {"closed_lots": 4}}),
        ])
        report = DoctorReport()
        result = Attribution(
            total_realized_pnl=total,
            closed_lots=4,
            lots_by_source={"llm": 4, "baseline": 4},
            by_source={"llm": model_pnl, "baseline": total - model_pnl},
            after_cost_pnl=total,
        )
        original = doctor_module.attribution.attribute
        doctor_module.attribution.attribute = lambda *a, **k: result
        try:
            _check_pnl_attribution(report, config, journal, [])
        finally:
            doctor_module.attribution.attribute = original
        return _by_name(report, "pnl attribution").status.value

    assert severity(-0.98, 563.41) == "fail", (
        "a model that opened 4 lots and contributed -0.98 to a +563.41 total must FAIL"
    )
    assert severity(4.59, 568.98) != "fail"
