"""The research stage runs on its own, without a human, and cannot reach production.

Every verdict this project had ever reported came from a person running a script: the research
package had no caller, so "we tested this rule" meant "I ran something". These tests pin the
driver exists, that it records every trial with the figures behind it, that its evidence bar
counts the project's cumulative attempts rather than resetting per run, and that it holds no
import which could write production state - the separation that makes a backtest evidence rather
than a participant.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from autopoiesis.regime import Bar
from autopoiesis.research import driver, trials
from autopoiesis.research.walk_forward import BASE_REQUIRED_TRADES

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bars(prices):
    return [Bar(T0 + timedelta(minutes=5 * i), p) for i, p in enumerate(prices)]


def _oscillating(n=1200, amplitude=0.01, period=12):
    """A price series that moves enough to make a band rule fire.

    Synthetic on purpose and labelled: this is a test of the driver's bookkeeping, not a claim
    about any instrument. The verdicts it produces are whatever they are and nothing here asserts
    an edge exists.
    """
    import math

    return [100.0 * (1 + amplitude * math.sin(i / period)) for i in range(n)]


def test_every_candidate_leaves_a_trial_with_its_figures(tmp_path):
    path = tmp_path / "trials.jsonl"
    report = driver.search(
        _bars(_oscillating()), thresholds=(0.002, 0.005), path=path
    )

    stored = trials.read_trials(path)
    assert len(stored) == report["candidates"] == 3, "one row per candidate, plus FIXED_SIZE"
    for row in stored:
        assert row["verdict"], "a verdict code is what a reviewer groups by"
        assert row["verdict_detail"], "and the detail is what makes it re-examinable"
        assert "out_of_sample_trades" in row and "required_trades" in row, (
            "a verdict a reviewer cannot recompute is a log, not evidence"
        )
        assert row["source"] == "alpaca historical bars; not synthetic"


def test_the_evidence_bar_counts_cumulative_trials_not_this_run(tmp_path):
    """A new candidate is judged against every attempt the project has made.

    The windowed version computed `required_trades` from this search's own candidates, so a
    ledger holding 37 trials still got the bar for 11 - and repeating the search each day would
    let the project shop across runs for a winner, which is the selection bias `trials.py` exists
    to prevent, arriving through the accounting rather than the statistics. Here the second sweep
    adds a threshold the ledger has not seen, so it genuinely adds attempts.
    """
    path = tmp_path / "trials.jsonl"

    first = driver.search(_bars(_oscillating()), thresholds=(0.002,), path=path)
    assert first["already_recorded"] == 0
    assert first["candidates"] == 2, "one threshold plus FIXED_SIZE, none on record yet"

    second = driver.search(_bars(_oscillating()), thresholds=(0.002, 0.005), path=path)
    assert second["already_recorded"] == 2, "the prior run's trials count against the next one"
    # The second sweep adds one threshold. 0.002 and FIXED_SIZE are both already on record, so
    # only the new threshold is an attempt the project has not already made.
    assert second["candidates"] == 3, "2 on record plus the one genuinely new threshold"
    assert second["repeats"] == 2, "the repeated threshold and FIXED_SIZE are not new attempts"

    # A growing search raises the bar on a square-root schedule.
    assert second["required_trades"] > first["required_trades"]
    assert all(
        row["trials"] in (2, 3) for row in trials.read_trials(path)
    ), "each trial travels with the attempt count it was judged at"


def test_the_bar_rises_as_the_square_root_of_trials(tmp_path):
    import math

    report = driver.search(_bars(_oscillating()), thresholds=(0.002,), path=tmp_path / "t.jsonl")
    expected = math.ceil(BASE_REQUIRED_TRADES * math.sqrt(report["candidates"]))
    assert report["required_trades"] == expected
    assert report["results"][0]["required_trades"] == expected


def test_the_search_space_is_the_rule_space_that_already_exists():
    space = driver.candidates(_bars(_oscillating()), thresholds=(0.001, 0.002))

    kinds = {spec["kind"] for spec in space}
    assert kinds == {"TREND_FOLLOW", "FIXED_SIZE"}, "no rule kind is added to make a search pass"
    thresholds = [s["parameters"]["threshold_pct"] for s in space if s["kind"] == "TREND_FOLLOW"]
    assert thresholds == [0.001, 0.002]


def test_a_search_over_no_bars_is_refused():
    with pytest.raises(ValueError):
        driver.candidates([])


def test_rerunning_on_the_same_data_records_nothing_new(tmp_path):
    """A repeat is not a trial.

    `tools/verify.py` refuses duplicate `strategy_id`s, and it is right to: re-deriving a verdict
    for a candidate already judged on the same data inflates `trials_run`, which is the
    denominator `required_trades` divides by, so the bar would climb on a static dataset without
    any new candidate ever being tried. The fix is idempotence in the driver, not a looser gate.
    """
    path = tmp_path / "trials.jsonl"
    bars = _bars(_oscillating())

    first = driver.search(bars, thresholds=(0.002,), path=path)
    assert first["repeats"] == 0
    rows_after_first = len(trials.read_trials(path))

    second = driver.search(bars, thresholds=(0.002,), path=path)
    # `candidates` is the count of distinct trials on record, `repeats` how many of this run's own
    # candidates were already judged on this data.
    assert second["candidates"] == first["candidates"], (
        "a run that tries nothing new must not raise the attempt count"
    )
    assert second["repeats"] == 2, "both of this run's candidates are repeats"
    assert len(trials.read_trials(path)) == rows_after_first, "the ledger must not grow"
    assert second["required_trades"] == first["required_trades"], (
        "a no-op run must not move the evidence bar"
    )


def test_new_data_re_evaluates_a_candidate_already_on_record(tmp_path):
    """Identity is rule *and* dataset, so more data is a genuinely new trial."""
    path = tmp_path / "trials.jsonl"
    driver.search(_bars(_oscillating(n=800)), thresholds=(0.002,), path=path)

    report = driver.search(_bars(_oscillating(n=1200)), thresholds=(0.002,), path=path)
    assert report["repeats"] == 0, "same rule over more data has not been judged before"
    assert len(trials.read_trials(path)) == 4


def test_every_cache_file_is_merged_and_deduplicated(tmp_path):
    """A rolling fetch writes a new dated file per run, so no single file is the dataset.

    Taking the largest file would pin the search to whichever window happened to be widest and
    ignore the newest tape; taking the newest would throw away everything before it. Overlapping
    windows share timestamps, so the merge has to deduplicate or the series gains phantom bars.
    """
    from autopoiesis.replay import ReplayBar, save_bars

    cache = tmp_path / "replay"
    cache.mkdir()
    shared = _oscillating(n=400, amplitude=0.01, period=12)
    later = _oscillating(n=400, amplitude=0.02, period=12)

    def _rows(prices, start=0):
        from datetime import datetime, timedelta

        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            ReplayBar(
                timestamp=t0 + timedelta(minutes=5 * (start + i)),
                open=p, high=p, low=p, close=p, volume=1.0,
            )
            for i, p in enumerate(prices)
        ]

    save_bars(cache / "SPY_5Min_2026-01-01_2026-01-02.json", "SPY", _rows(shared))
    save_bars(cache / "SPY_5Min_2026-01-01_2026-01-03.json", "SPY", _rows(shared + later))

    bars, provenance = driver._load_real_bars(cache)

    assert len(bars) == 800, "400 + 400, with the 400 overlapping bars counted once"
    assert len({b.timestamp for b in bars}) == len(bars), "no timestamp appears twice"
    assert bars == sorted(bars, key=lambda b: b.timestamp), "the series is chronological"
    assert "2 file(s)" in provenance, "the report says how much history it merged"


def test_an_unwritable_ledger_does_not_lose_the_run(tmp_path):
    """A trial that cannot be journalled still ran; the count is reported, not hidden."""
    report = driver.search(
        _bars(_oscillating()),
        thresholds=(0.002,),
        path=tmp_path / "missing" / "trials.jsonl",
        # make the parent a file so mkdir fails
    )
    assert report["unrecorded"] == 0

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    report = driver.search(
        _bars(_oscillating()), thresholds=(0.002,), path=blocker / "trials.jsonl"
    )
    assert report["unrecorded"] == report["candidates"], "silently lost trials are reported"
    assert len(report["results"]) == report["candidates"], "the evaluation still happened"


def test_the_driver_cannot_reach_production_state():
    """Asserted from the source rather than trusted.

    Reading real bars through `autopoiesis.replay` is harmless. Importing anything that *writes*
    production state is not: a backtest that can reach the thing it grades stops being evidence.
    This is the half of the separation the gate in `tools/verify.py` cannot see, because that gate
    only walks production modules looking for research imports, and this direction is the other one.
    """
    import pathlib

    source = pathlib.Path(driver.__file__).read_text(encoding="utf-8")
    forbidden = (
        "autopoiesis.journal",
        "autopoiesis.guardian",
        "autopoiesis.executor",
        "autopoiesis.strategy_engine",
        "autopoiesis.strategy_admission",
        "autopoiesis.loop",
        "autopoiesis.daemon",
    )
    imported = [
        name
        for name in forbidden
        if f"import {name}" in source or f"from {name}" in source
    ]
    assert imported == [], f"the driver must not import state-writing modules: {imported}"


def test_a_missing_cache_is_an_error_rather_than_generated_prices(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        driver._load_real_bars(tmp_path / "absent")
    assert "never generates prices" in str(excinfo.value)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit):
        driver._load_real_bars(empty)


def test_the_driver_writes_only_the_trials_ledger(tmp_path):
    """Nothing else in the tree may change during a search."""
    workspace = tmp_path / "runtime" / "autopoiesis"
    workspace.mkdir(parents=True)
    path = workspace / "research_trials.jsonl"

    driver.search(_bars(_oscillating()), thresholds=(0.002,), path=path)

    produced = sorted(p.name for p in workspace.iterdir())
    assert produced == ["research_trials.jsonl"], (
        f"a search touched more than its ledger: {produced}"
    )
    written = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert written and all("recorded_at" in row for row in written)
