"""Every research trial leaves a record, including the failed ones.

The objective requires failed trials to be recorded, because selection bias is
invisible otherwise: keep the best of forty variants and report it as the only
candidate, and the number inherits the selection without showing it.

The research layer wrote nothing at all, so a backtest could run, could fail its
out-of-sample bar, and leave no evidence it had happened. These tests pin the record
exists, that it is separate from production, and that a torn final line does not cost
the history behind it.
"""

import json

from min_agent.research import trials


def test_a_failed_trial_is_recorded(tmp_path):
    path = tmp_path / "trials.jsonl"
    assert trials.record_trial(
        {"strategy_id": "s1", "verdict": "OVERFIT",
         "out_of_sample_return_pct": -1.2, "trials": 27},
        path=path,
    )

    stored = trials.read_trials(path)
    assert len(stored) == 1
    assert stored[0]["verdict"] == "OVERFIT"
    assert stored[0]["out_of_sample_return_pct"] == -1.2
    assert stored[0]["trials"] == 27, "the search size travels with the result"
    assert "recorded_at" in stored[0]


def test_the_trial_count_is_reported_because_it_is_the_overfitting_denominator(tmp_path):
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        trials.record_trial(
            {"strategy_id": f"s{i}", "verdict": "INSUFFICIENT"}, path=path,
        )
    trials.record_trial(
        {"strategy_id": "winner", "verdict": "PASSES_RESEARCH_GATE"}, path=path,
    )

    summary = trials.summarise(trials.read_trials(path))
    assert summary["trials_run"] == 4
    assert summary["passed"] == 1
    assert summary["failed"] == 3
    assert summary["by_verdict"]["INSUFFICIENT"] == 3


def test_no_trials_summarise_to_zero_rather_than_crashing(tmp_path):
    summary = trials.summarise([])
    assert summary == {
        "trials_run": 0, "passed": 0, "failed": 0, "by_verdict": {},
    }


def test_a_torn_final_line_does_not_cost_the_history_behind_it(tmp_path):
    """A crash mid-append is expected. Refusing to read the file because of it would
    lose the record of every trial before it."""
    path = tmp_path / "trials.jsonl"
    trials.record_trial({"strategy_id": "a", "verdict": "NO_EDGE"}, path=path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"strategy_id": "b", "verdi')

    stored = trials.read_trials(path)
    assert len(stored) == 1
    assert stored[0]["strategy_id"] == "a"


def test_a_write_failure_is_reported_rather_than_raised(tmp_path):
    """The trial already ran; its value is in the result, so losing the bookkeeping
    must not lose the run."""
    blocked = tmp_path / "trials.jsonl"
    blocked.write_text("x")            # a file where a directory is needed
    assert trials.record_trial({"verdict": "NO_EDGE"}, path=blocked / "sub.jsonl") is False


def test_the_trial_log_is_not_the_production_journal(tmp_path):
    """The separation is the point: production must never read this, or a backtest
    could influence what trades."""
    import inspect

    from min_agent.research import trials as module

    source = inspect.getsource(module)
    assert "journal.jsonl" not in source
    assert "min_agent.journal" not in source, (
        "the research trial log must not go through the production journal"
    )


def test_production_does_not_import_the_trial_log(tmp_path):
    """Belt and braces on top of the existing separation check."""
    import inspect

    from min_agent import daemon, loop
    for module in (daemon, loop):
        assert "research" not in inspect.getsource(module)


def test_trials_are_appended_not_overwritten(tmp_path):
    path = tmp_path / "trials.jsonl"
    for i in range(4):
        trials.record_trial({"n": i}, path=path)

    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 4
    assert [json.loads(l)["n"] for l in lines] == [0, 1, 2, 3]
