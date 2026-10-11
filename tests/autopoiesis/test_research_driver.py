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


def _search(bars, **kwargs):
    """Bookkeeping tests use synthetic bars and must label them as such."""
    return driver.search(bars, source="synthetic test fixture", **kwargs)


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
    report = _search(
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
        assert row["source"] == "synthetic test fixture"


def test_the_evidence_bar_counts_cumulative_trials_not_this_run(tmp_path):
    """A new candidate is judged against every attempt the project has made.

    The windowed version computed `required_trades` from this search's own candidates, so a
    ledger holding 37 trials still got the bar for 11 - and repeating the search each day would
    let the project shop across runs for a winner, which is the selection bias `trials.py` exists
    to prevent, arriving through the accounting rather than the statistics. Here the second sweep
    adds a threshold the ledger has not seen, so it genuinely adds attempts.
    """
    path = tmp_path / "trials.jsonl"

    first = _search(_bars(_oscillating()), thresholds=(0.002,), path=path)
    assert first["already_recorded"] == 0
    assert first["candidates"] == 2, "one threshold plus FIXED_SIZE, none on record yet"

    second = _search(_bars(_oscillating()), thresholds=(0.002, 0.005), path=path)
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

    report = _search(_bars(_oscillating()), thresholds=(0.002,), path=tmp_path / "t.jsonl")
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

    first = _search(bars, thresholds=(0.002,), path=path)
    assert first["repeats"] == 0
    rows_after_first = len(trials.read_trials(path))

    second = _search(bars, thresholds=(0.002,), path=path)
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
    _search(_bars(_oscillating(n=800)), thresholds=(0.002,), path=path)

    report = _search(_bars(_oscillating(n=1200)), thresholds=(0.002,), path=path)
    assert report["repeats"] == 0, "same rule over more data has not been judged before"
    assert len(trials.read_trials(path)) == 4


def test_same_length_changed_prices_are_a_new_dataset(tmp_path):
    """Bar count is not a data version: one changed price must create a new trial."""
    path = tmp_path / "trials.jsonl"
    bars = _bars(_oscillating(n=800))
    _search(bars, thresholds=(0.002,), path=path)
    changed = list(bars)
    changed[400] = Bar(changed[400].timestamp, changed[400].price * 1.001)

    report = _search(changed, thresholds=(0.002,), path=path)

    assert report["repeats"] == 0
    assert len(trials.read_trials(path)) == 4


def test_legacy_trial_without_fingerprint_is_not_trusted_as_exact_match(tmp_path):
    path = tmp_path / "trials.jsonl"
    bars = _bars(_oscillating(n=800))
    _search(bars, thresholds=(0.002,), path=path)
    recorded = trials.read_trials(path)
    spec = driver.candidates(bars, thresholds=(0.002,))[0]
    for row in recorded:
        row.pop("dataset_fingerprint", None)

    assert not driver._already_evaluated(
        recorded, spec, len(bars), None, driver.dataset_fingerprint(bars)
    )


def test_trial_source_label_is_explicit_and_fingerprint_is_reported(tmp_path):
    report = driver.search(
        _bars(_oscillating(n=800)),
        thresholds=(0.002,),
        path=tmp_path / "trials.jsonl",
        source="synthetic test fixture",
    )

    assert report["dataset_fingerprint"].startswith("sha256:")
    assert all(row["dataset_fingerprint"] == report["dataset_fingerprint"] for row in report["results"])
    assert all(row["source"] == "synthetic test fixture" for row in report["results"])


def test_fingerprint_covers_ordered_timestamps_and_requires_source(tmp_path):
    bars = _bars(_oscillating(n=800))
    changed_timestamp = list(bars)
    changed_timestamp[400] = Bar(changed_timestamp[400].timestamp + timedelta(seconds=1), changed_timestamp[400].price)
    assert driver.dataset_fingerprint(bars) != driver.dataset_fingerprint(changed_timestamp)
    assert driver.dataset_fingerprint(bars) == driver.dataset_fingerprint(list(bars))

    with pytest.raises(ValueError, match="source label"):
        driver.search(bars, thresholds=(0.002,), path=tmp_path / "trials.jsonl", source="  ")


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

    bars, provenance, _ignored = driver._load_real_bars(cache)

    assert len(bars) == 800, "400 + 400, with the 400 overlapping bars counted once"
    assert len({b.timestamp for b in bars}) == len(bars), "no timestamp appears twice"
    assert bars == sorted(bars, key=lambda b: b.timestamp), "the series is chronological"
    assert "2 file(s)" in provenance, "the report says how much history it merged"


def test_an_unwritable_ledger_does_not_lose_the_run(tmp_path):
    """A trial that cannot be journalled still ran; the count is reported, not hidden."""
    report = _search(
        _bars(_oscillating()),
        thresholds=(0.002,),
        path=tmp_path / "missing" / "trials.jsonl",
        # make the parent a file so mkdir fails
    )
    assert report["unrecorded"] == 0

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    report = _search(
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


def test_a_mixed_cache_is_reported_not_fatal(tmp_path):
    """The deployment's cache holds research files beside the bars, on purpose.

    `glob("*.json")` picked those up, `load_bars` raised `KeyError: 'bars'`, and the scheduled
    research job failed for a full day while every unit test passed - because the tests point the
    driver at a directory holding only bar files, so nothing exercised the mixed directory the
    deployment actually has. A non-bar file is ordinary now: the search must run, and it must name
    what it skipped rather than skip silently.
    """
    from datetime import datetime, timedelta

    from autopoiesis.replay import ReplayBar, save_bars

    cache = tmp_path / "replay"
    cache.mkdir()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = _oscillating(n=400, amplitude=0.01, period=12)
    rows = [
        ReplayBar(timestamp=t0 + timedelta(minutes=5 * i), open=p, high=p, low=p, close=p, volume=1000.0)
        for i, p in enumerate(prices)
    ]
    save_bars(cache / "SPY_5Min_x.json", "SPY", rows)
    (cache / "CIK_MAP.json").write_text('{"SPY": 9999}', encoding="utf-8")
    (cache / "FUNDAMENTALS.json").write_text('{"SPY": {"sector": "x"}}', encoding="utf-8")

    bars, _provenance, ignored = driver._load_real_bars(cache)
    assert len(bars) == 400
    assert ignored == ["CIK_MAP.json (no bars)", "FUNDAMENTALS.json (no bars)"]


def test_a_cache_of_only_non_bar_files_is_still_an_error(tmp_path):
    """Nothing to decide is not a cache. The message says which files were there, so a cache whose
    shape changed entirely is distinguishable from one that is missing."""
    cache = tmp_path / "replay"
    cache.mkdir()
    (cache / "CIK_MAP.json").write_text('{"SPY": 9999}', encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        driver._load_real_bars(cache)
    assert "no bar files" in str(excinfo.value)


def test_the_driver_writes_only_the_trials_ledger(tmp_path):
    """Nothing else in the tree may change during a search."""
    workspace = tmp_path / "runtime" / "autopoiesis"
    workspace.mkdir(parents=True)
    path = workspace / "research_trials.jsonl"

    _search(_bars(_oscillating()), thresholds=(0.002,), path=path)

    produced = sorted(p.name for p in workspace.iterdir())
    assert produced == ["research_trials.jsonl"], (
        f"a search touched more than its ledger: {produced}"
    )
    written = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert written and all("recorded_at" in row for row in written)


def test_the_merge_does_not_collapse_two_symbols_into_one_series(tmp_path):
    """The defect that was measured on the real cache, not imagined about it.

    The merge keyed its deduplication on the timestamp alone, which was right when the cache held
    five SPY files (54,089 raw bars to 24,162 unique, and the code comment still describes that
    world). The cache now holds 17 symbols and 988,704 rows, so the same key collapsed every
    instrument's five-minute bar at the same instant into whichever file was read first:
    **852,937 of 988,704 bars dropped**, and the 135,767 that survived were fragments of
    seventeen instruments interleaved - a price series of nothing. Every verdict recorded against
    it was a measurement of a series that never existed.

    Two symbols with the same timestamps is the smallest thing that shows it: the merged tape
    would hold one symbol's closes, chosen by which file `sorted()` happened to put first.
    """
    from autopoiesis.replay import ReplayBar, save_bars

    def _rows(base):
        return [ReplayBar(timestamp=base + timedelta(minutes=5 * i),
                          open=100.0 + i, high=100.0 + i, low=100.0 + i,
                          close=100.0 + i, volume=1000.0)
                for i in range(5)]

    cache = tmp_path / "replay"
    cache.mkdir()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    save_bars(cache / "SPY_5Min_a.json", "SPY", _rows(t0))
    save_bars(cache / "QQQ_5Min_a.json", "QQQ", _rows(t0))

    bars, provenance, ignored = driver._load_real_bars(cache, "SPY")

    assert len(bars) == 5, "one symbol's five bars, not one bar chosen between two symbols"
    assert [b.price for b in bars] == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert provenance.startswith("SPY:"), provenance
    # The other symbol is named, not merged away.
    assert any("QQQ" in note and "not SPY" in note for note in ignored), ignored


def test_a_cache_holding_only_another_symbol_is_an_error_that_names_it(tmp_path):
    """Nothing to decide is not a cache. The message says which symbol was wanted.

    Silently falling back to the other symbol would make the search answer a question nobody
    asked, and the report would not show it.
    """
    from autopoiesis.replay import ReplayBar, save_bars

    cache = tmp_path / "replay"
    cache.mkdir()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    save_bars(cache / "QQQ_5Min_a.json", "QQQ",
              [ReplayBar(timestamp=t0 + timedelta(minutes=5 * i), open=100.0, high=100.0,
                         low=100.0, close=100.0, volume=1.0) for i in range(5)])

    with pytest.raises(SystemExit) as excinfo:
        driver._load_real_bars(cache, "SPY")
    message = str(excinfo.value)
    assert "no bars for SPY" in message, message
    assert "QQQ_5Min_a.json" in message, "the file that was there is named"


def test_a_symbol_argument_reaches_the_loader(tmp_path):
    """The searched instrument comes from the invocation, not from a directory listing."""
    from autopoiesis.replay import ReplayBar, save_bars

    cache = tmp_path / "replay"
    cache.mkdir()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for symbol in ("SPY", "QQQ"):
        save_bars(cache / f"{symbol}_5Min_a.json", symbol,
                  [ReplayBar(timestamp=t0 + timedelta(minutes=5 * i), open=10.0, high=10.0,
                             low=10.0, close=10.0, volume=1.0) for i in range(5)])

    bars, provenance, _ = driver._load_real_bars(cache, "qqq")
    assert provenance.startswith("QQQ:"), provenance
    assert len(bars) == 5
