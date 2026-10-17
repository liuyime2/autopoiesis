"""Synthetic fixtures exercise contract enforcement; they are not market evidence."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autopoiesis.research import driver, prereg, trials

SPEC = """hypothesis: synthetic integrity diagnostic
family: synthetic-contract-tests
instrument: SPY
interval: 5Min
window_start_utc: 2026-04-10T13:30:00Z
window_end_utc: 2026-04-10T20:00:00Z
rule: threshold_grid_v1
primary_metric: oos_return_pct_after_cost_v1
expected_trades: 100
thresholds: 0.002,0.005
candidate_budget: 3
evaluator: walk_forward_v1
n_folds: 3
min_train_bars: 20
commission_pct: 0.005
slippage_pct: 0.005
"""


@pytest.fixture
def contract(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setenv("AUTOPOIESIS_PREREGISTRY", str(registry))
    monkeypatch.setenv("AUTOPOIESIS_RESEARCH_TRIALS", str(ledger))
    monkeypatch.setattr(trials, "DEFAULT_TRIALS_PATH", ledger)
    spec = tmp_path / "diagnostic.spec"
    spec.write_text(SPEC)
    prereg.register(spec, registry)
    cache = tmp_path / "replay"
    cache.mkdir()
    write_cache(cache / "SPY_5Min_a.json")
    return spec, registry, ledger, cache


def write_cache(path, *, closes=None, interval="5Min", provenance="alpaca historical bars; not synthetic"):
    # Synthetic unit-test rows carrying a fixture of the trusted cache envelope.
    start = datetime(2026, 4, 10, 13, 30, tzinfo=timezone.utc)
    prices = closes if closes is not None else [100 + i * 0.01 for i in range(60)]
    path.write_text(json.dumps({"symbol": "SPY", "interval": interval,
        "provenance": provenance, "bars": [
        {"t": (start + timedelta(minutes=5*i)).isoformat(), "o": p,
         "h": p, "l": p, "c": p, "v": 1000} for i, p in enumerate(prices)]}))


def test_cli_requires_spec_before_loading(monkeypatch):
    monkeypatch.setattr(driver, "_load_real_bars", lambda *a, **k: pytest.fail("loaded before gate"))
    assert driver.main(["absent", "SPY"]) != 0


def test_contract_symbol_cannot_become_make_shell_syntax(contract):
    spec, registry, ledger, _cache = contract
    spec.write_text(SPEC.replace("instrument: SPY", 'instrument: SPY"; printf SYNTHETIC_MARKER; #'))
    prereg.register(spec, registry)
    with pytest.raises(ValueError, match="instrument"):
        driver.registered_contract(spec, registry=registry)
    assert not ledger.exists()


@pytest.mark.parametrize("field,value", [("out_of_sample_return_pct", float("nan")),
    ("out_of_sample_trades", -1), ("verdict", "PASSES"), ("verdict", "PASSES_RESEARCH_GATE")])
def test_corrupt_matching_result_cannot_be_reused(contract, field, value):
    spec, _registry, ledger, cache = contract
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    rows = trials.read_trials(ledger)
    rows[0][field] = value
    corrupted = "".join(json.dumps(row) + "\n" for row in rows)
    ledger.write_text(corrupted)
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert ledger.read_text() == corrupted


def test_cli_refuses_unregistered_before_backtest(contract, monkeypatch):
    spec, registry, ledger, cache = contract
    for path in registry.iterdir():
        path.unlink()
    monkeypatch.setattr(driver, "run_walk_forward", lambda *a, **k: pytest.fail("ungated backtest"))
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert not ledger.exists()


@pytest.mark.parametrize("field,value", [("rule", "selection on earliest 60%"),
    ("primary_metric", "Sharpe"), ("evaluator", "unknown"), ("candidate_budget", "20"),
    ("thresholds", "0.00051,0.00052"), ("commission_pct", "nan"),
    ("slippage_pct", "-1"), ("n_folds", "0"), ("min_train_bars", "0")])
def test_unsupported_settings_refused_before_backtest(contract, monkeypatch, field, value):
    spec, registry, ledger, cache = contract
    content = "\n".join(f"{field}: {value}" if line.startswith(field + ":") else line
        for line in SPEC.splitlines()) + "\n"
    spec.write_text(content)
    prereg.register(spec, registry)
    monkeypatch.setattr(driver, "run_walk_forward", lambda *a, **k: pytest.fail("unsupported backtest"))
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert not ledger.exists()


def test_conflicting_overlap_is_refused(contract):
    _spec, _registry, _ledger, cache = contract
    write_cache(cache / "SPY_5Min_b.json", closes=[999])
    with pytest.raises((ValueError, SystemExit), match="conflict"):
        driver._load_real_bars(cache, "SPY")


def test_registration_metadata_is_required_and_idempotent(contract):
    spec, registry, _ledger, _cache = contract
    digest = prereg.spec_hash(spec)
    first = (registry / (digest + ".json")).read_bytes()
    prereg.register(spec, registry)
    assert (registry / (digest + ".json")).read_bytes() == first
    (registry / (digest + ".json")).unlink()
    assert not prereg.verify(spec, registry)[0]


def test_registration_refuses_to_repair_corrupted_copy(contract):
    spec, registry, _ledger, _cache = contract
    digest = prereg.spec_hash(spec)
    (registry / (digest + ".spec")).write_text("corrupted")
    with pytest.raises((ValueError, SystemExit)):
        prereg.register(spec, registry)


def test_repeat_identity_changes_with_settings(contract):
    spec, registry, ledger, cache = contract
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    first = trials.read_trials(ledger)
    assert len(first) == 3
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    assert len(trials.read_trials(ledger)) == 3
    spec.write_text(SPEC.replace("commission_pct: 0.005", "commission_pct: 0.02"))
    prereg.register(spec, registry)
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    updated = trials.read_trials(ledger)
    assert len(updated) == 6
    assert {row["trials"] for row in updated[3:]} == {6}
    assert len({row["evaluation_identity"] for row in updated}) == 2


def test_persistence_failure_fails_command(contract, monkeypatch):
    spec, _registry, _ledger, cache = contract
    monkeypatch.setattr(driver, "record_trial", lambda *a, **k: False)
    assert driver.main([str(cache), "SPY", str(spec)]) != 0


@pytest.mark.parametrize("mutation", ["naive", "negative", "infinite", "non-real", "corrupt", "unordered"])
def test_invalid_selected_tape_never_reaches_backtest(contract, monkeypatch, mutation):
    spec, _registry, ledger, cache = contract
    path = cache / "SPY_5Min_a.json"
    payload = json.loads(path.read_text())
    if mutation == "naive":
        payload["bars"][0]["t"] = "2026-04-10T13:30:00"
    elif mutation == "negative":
        payload["bars"][0]["c"] = -1
    elif mutation == "infinite":
        payload["bars"][0]["c"] = float("inf")
    elif mutation == "non-real":
        payload["provenance"] = "synthetic"
    elif mutation == "unordered":
        payload["bars"][0], payload["bars"][1] = payload["bars"][1], payload["bars"][0]
    if mutation == "corrupt":
        path.write_text("{")
    else:
        path.write_text(json.dumps(payload))
    monkeypatch.setattr(driver, "run_walk_forward", lambda *a, **k: pytest.fail("invalid backtest"))
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert not ledger.exists()


def test_filters_interval_then_window_before_quality_or_conflicts(contract):
    spec, _registry, _ledger, cache = contract
    # A different interval with unusable row timestamps must be excluded before parsing.
    (cache / "SPY_1Day_other.json").write_text(json.dumps({"symbol": "SPY", "interval": "1Day",
        "provenance": "synthetic irrelevant interval", "bars": [{"t": "broken"}]}))
    # Conflicting invalid prices outside the declared window are irrelevant to this tape.
    (cache / "SPY_5Min_old.json").write_text(json.dumps({"symbol": "SPY", "interval": "5Min",
        "provenance": "alpaca historical bars; not synthetic",
        "bars": [{"t": "2020-01-01T00:00:00Z", "c": -1}]}))
    prepared = driver.prepare_research(cache, "SPY", spec)
    assert len(prepared.bars) == 60
    assert any("interval 1Day" in note for note in prepared.ignored)
    assert any("outside the declared window" in note for note in prepared.ignored)


def test_identical_overlapping_files_are_deduplicated(contract):
    spec, _registry, _ledger, cache = contract
    write_cache(cache / "SPY_5Min_b.json")
    assert len(driver.prepare_research(cache, "SPY", spec).bars) == 60


@pytest.mark.parametrize("symbol", ["QQQ", ""])
def test_symbol_mismatch_is_refused_before_backtest(contract, monkeypatch, symbol):
    spec, _registry, ledger, cache = contract
    monkeypatch.setattr(driver, "run_walk_forward", lambda *a, **k: pytest.fail("mismatch backtest"))
    assert driver.main([str(cache), symbol, str(spec)]) != 0
    assert not ledger.exists()


def test_evaluator_change_invalidates_cached_outcomes(contract, monkeypatch):
    spec, _registry, ledger, cache = contract
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    monkeypatch.setattr(driver, "evaluator_digest", lambda: "changed-implementation")
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    rows = trials.read_trials(ledger)
    assert len(rows) == 6
    assert {row["trials"] for row in rows[3:]} == {6}


def test_corrupt_trial_history_cannot_reset_attempt_count(contract, monkeypatch):
    spec, _registry, ledger, cache = contract
    ledger.write_text("corrupted trial history\n")
    monkeypatch.setattr(driver, "run_walk_forward", lambda *a, **k: pytest.fail("uncounted backtest"))
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert ledger.read_text() == "corrupted trial history\n"


def test_after_window_registration_is_recorded_as_integrity_diagnostic(contract):
    spec, _registry, ledger, cache = contract
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    rows = trials.read_trials(ledger)
    assert {row["preregistration_scope"] for row in rows} == {"registered_after_window_start"}
    assert {row["evaluator_sha256"] for row in rows} == {driver.evaluator_digest()}
    assert all(row["registration"]["registered_at"] for row in rows)
    assert {row["evidence_mode"] for row in rows} == {"registered_integrity_diagnostic"}


def test_registration_future_metadata_is_refused(contract):
    spec, registry, ledger, cache = contract
    metadata = registry / (prereg.spec_hash(spec) + ".json")
    payload = json.loads(metadata.read_text())
    payload["registered_at"] = "2999-01-01T00:00:00Z"
    metadata.write_text(json.dumps(payload))
    assert not prereg.verify(spec, registry)[0]
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert not ledger.exists()


def test_verified_spec_is_a_frozen_read(contract, monkeypatch):
    spec, _registry, _ledger, cache = contract
    original = Path.read_bytes
    reads = []
    expected = prereg.spec_hash(spec)

    def read_then_change(path):
        frozen = original(path)
        if path == spec:
            reads.append(path)
            path.write_text(SPEC.replace("instrument: SPY", "instrument: QQQ"))
        return frozen

    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    prepared = driver.prepare_research(cache, "SPY", spec)
    assert prepared.spec_digest == expected
    assert prepared.symbol == "SPY"
    assert reads == [spec]


def test_default_registry_is_working_directory_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOPOIESIS_PREREGISTRY", raising=False)
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "diagnostic.spec"
    spec.write_text(SPEC)
    prereg.register(spec)
    assert prereg.registry_path() == Path("runtime/autopoiesis/preregistered")
    assert prereg.verify(spec)[0]
    assert (tmp_path / "runtime/autopoiesis/preregistered").is_dir()


def test_concurrent_cli_refuses_before_reading_trials_or_backtesting(contract, monkeypatch):
    import subprocess
    import sys

    spec, _registry, ledger, cache = contract
    # A separate process owns the append ledger for an active synthetic research session.
    holder = subprocess.Popen([sys.executable, "-c",
        "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); "
        "print('locked',flush=True); input()", str(ledger)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(driver, "read_trials", lambda *a, **k: pytest.fail("concurrent trial read"))
        monkeypatch.setattr(driver, "run_walk_forward", lambda *a, **k: pytest.fail("concurrent backtest"))
        assert driver.main([str(cache), "SPY", str(spec)]) != 0
        assert ledger.read_text() == ""
    finally:
        holder.communicate("\n", timeout=5)


@pytest.mark.parametrize("field", ["interval", "thresholds", "candidate_budget", "evaluator",
    "n_folds", "min_train_bars", "commission_pct", "slippage_pct"])
def test_missing_execution_setting_fails_before_loading(contract, monkeypatch, field):
    spec, registry, ledger, cache = contract
    spec.write_text("\n".join(line for line in SPEC.splitlines()
                              if not line.startswith(field + ":")) + "\n")
    prereg.register(spec, registry)
    monkeypatch.setattr(driver, "_load_real_bars", lambda *a, **k: pytest.fail("incomplete loader"))
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert not ledger.exists()


def test_unknown_contract_promise_is_not_silently_ignored(contract, monkeypatch):
    spec, registry, ledger, cache = contract
    spec.write_text(SPEC + "holdout: last 40%\n")
    prereg.register(spec, registry)
    monkeypatch.setattr(driver, "_load_real_bars", lambda *a, **k: pytest.fail("unsupported promise"))
    assert driver.main([str(cache), "SPY", str(spec)]) != 0
    assert not ledger.exists()


def test_duplicate_contract_keys_are_refused():
    with pytest.raises(ValueError, match="duplicate spec field"):
        prereg.load_spec_from_text(SPEC + "instrument: QQQ\n")


def test_evaluator_receives_declared_costs_and_folds(contract, monkeypatch):
    spec, _registry, ledger, cache = contract
    calls = []
    actual = driver.run_walk_forward

    def capture(bars, **settings):
        calls.append(settings)
        return actual(bars, **settings)

    monkeypatch.setattr(driver, "run_walk_forward", capture)
    assert driver.main([str(cache), "SPY", str(spec)]) == 0
    assert len(calls) == 3
    assert all(call["n_folds"] == 3 and call["min_train_bars"] == 20
               and call["commission_pct"] == 0.005 and call["slippage_pct"] == 0.005
               and call["trials"] == 3 for call in calls)
    assert {row["folds"] for row in trials.read_trials(ledger)} == {3}
