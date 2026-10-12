"""A hypothesis stated after the fact is a description of the data, not a hypothesis.

Pre-registration is what makes the difference checkable rather than asserted: the spec is written
and hashed before the run, and the run verifies it afterwards, so a spec edited to match the result
is a different file and the verification says so. The data-quality gate is the other half of the
same question - a verdict computed from a tape that is not the tape the spec declared is a number
with no meaning that looks exactly like one with some.

Both are exercised against real tapes and real defects, because a gate only ever tested on clean
fixtures is a gate nobody has run.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "research_prereg", ROOT / "src" / "autopoiesis" / "research" / "prereg.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_prereg"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    return _load()


class _Bar:
    def __init__(self, at, price, close=None):
        self.timestamp = at
        if price is not None:
            self.price = price
        if close is not None:
            self.close = close


T0 = datetime(2026, 4, 10, 13, 30, tzinfo=timezone.utc)

SPEC = """hypothesis: a threshold rule has positive out-of-sample return after cost
family: test-family
instrument: SPY
window_start_utc: 2026-04-10T13:30:00Z
window_end_utc: 2026-04-12T19:40:00Z
rule: the existing candidate grid, selection on the earliest 60% of days
primary_metric: out-of-sample return percentage after cost
expected_trades: 100
"""


# --- the spec -------------------------------------------------------------------------------


def test_a_complete_spec_registers(module, tmp_path):
    path = tmp_path / "spec.txt"
    path.write_text(SPEC, encoding="utf-8")
    payload = module.register(path, tmp_path / "registry")

    assert payload["sha256"]
    assert (tmp_path / "registry" / f"{payload['sha256']}.spec").exists()
    assert payload["fields"]["instrument"] == "SPY"


def test_a_spec_missing_a_field_is_refused(module, tmp_path):
    """Under-specified means uncheckable, and an under-specified run is a claim nobody can test."""
    path = tmp_path / "spec.txt"
    path.write_text(SPEC.replace("primary_metric: out-of-sample return percentage after cost\n", ""),
                    encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        module.register(path, tmp_path / "registry")
    assert "no primary_metric" in str(excinfo.value)


def test_a_spec_edited_after_registration_no_longer_verifies(module, tmp_path):
    """The mechanism, stated as a test.

    The registered copy is what the run verifies against, so editing the spec after the fact moves
    it away from its own hash - and the verification fails, without anybody's word being trusted.
    """
    path = tmp_path / "spec.txt"
    path.write_text(SPEC, encoding="utf-8")
    module.register(path, tmp_path / "registry")

    assert module.verify(path, tmp_path / "registry")[0] is True

    path.write_text(SPEC.replace("expected_trades: 100", "expected_trades: 400"),
                    encoding="utf-8")
    ok, problems = module.verify(path, tmp_path / "registry")
    assert ok is False
    assert any("no spec registered under" in problem for problem in problems)


def test_verifying_an_unregistered_spec_fails(module, tmp_path):
    path = tmp_path / "spec.txt"
    path.write_text(SPEC, encoding="utf-8")
    ok, problems = module.verify(path, tmp_path / "registry")
    assert ok is False
    assert any("after the fact" in problem for problem in problems)


# --- the tape -------------------------------------------------------------------------------


def test_a_clean_tape_is_usable(module):
    bars = [_Bar(T0 + timedelta(minutes=5 * i), 100.0 + i) for i in range(12)]
    report = module.check_tape(bars, module.load_spec_from_text(SPEC))
    assert report.usable, report.problems
    assert report.bars == 12


def test_a_window_overrun_is_refused(module):
    """The spec claims a window; a bar outside it is data the hypothesis did not say it would see.

    This is the check that caught 50 real bars in the tape the 2026-10-11 alpha search consumed: the
    spec declared a window ending 19:40Z and the fetched tape ran to 23:55Z. Not corruption - data
    the specification did not claim, which is look-ahead in its most literal form.
    """
    spec = module.load_spec_from_text(SPEC)
    # The window in SPEC ends 2026-04-12T19:40Z; a bar three days past it is outside the claim the
    # spec made. (The first version of this test used +30h, which is still inside the window, and
    # asserted a refusal that did not happen - a test that cannot fail is the failure it was meant
    # to demonstrate.)
    inside = [_Bar(T0 + timedelta(minutes=5 * i), 100.0) for i in range(10)]
    past = datetime(2026, 4, 15, 20, 0, tzinfo=timezone.utc)
    report = module.check_tape(inside + [_Bar(past, 101.0)], spec)

    assert report.usable is False
    assert any("after the declared window" in problem for problem in report.problems)


def test_a_bar_before_the_window_is_refused(module):
    spec = module.load_spec_from_text(SPEC)
    report = module.check_tape([_Bar(T0 - timedelta(hours=30), 100.0)], spec)
    assert report.usable is False
    assert any("before the declared window" in problem for problem in report.problems)


def test_a_duplicate_timestamp_is_refused(module):
    """Two prices at one instant, and the fingerprint cannot distinguish the series from either.

    The fingerprint is over ordered (timestamp, price) pairs, so a tape with a repeat at one
    timestamp fingerprints as a series that was never fetched.
    """
    spec = module.load_spec_from_text(SPEC)
    bars = [_Bar(T0 + timedelta(minutes=5 * i), 100.0) for i in range(4)]
    report = module.check_tape(bars + [_Bar(bars[2].timestamp, 105.0)], spec)

    assert report.usable is False
    assert any("repeats the timestamp" in problem for problem in report.problems)


def test_a_non_chronological_series_is_refused(module):
    """Out-of-order pairs silently reorder a return series."""
    spec = module.load_spec_from_text(SPEC)
    bars = [_Bar(T0 + timedelta(minutes=5 * i), 100.0) for i in range(4)]
    bars[2], bars[3] = bars[3], bars[2]
    report = module.check_tape(bars, spec)

    assert report.usable is False
    assert any("not chronological" in problem for problem in report.problems)


def test_an_unusable_price_is_refused(module):
    """A NaN or zero price does not produce a bad answer, it produces an unanswerable one."""
    spec = module.load_spec_from_text(SPEC)
    for bad in (None, float("nan")):
        report = module.check_tape([_Bar(T0, bad)], spec)
        assert report.usable is False, f"{bad!r} passed the price check"


def test_the_gate_reads_either_spelling_of_the_price(module):
    """`regime.Bar` says `price`, `replay.ReplayBar` says `close`; both are what the tape has.

    The first version of this gate read only `price` and reported the real SPY tape as 24,000-odd
    unusable bars - a gate that rejects everything it is pointed at is worse than no gate, because
    it looks like it ran.
    """
    spec = module.load_spec_from_text(SPEC)
    report = module.check_tape([_Bar(T0, None, close=100.0)], spec)
    assert report.usable, report.problems


def test_an_absent_window_bound_is_not_a_rejection(module):
    """A spec that does not declare a window is under-specified, not a tape that overruns.

    The gate refuses such a spec at registration; here it is given to the tape anyway and the
    absence must not be reported as a tape defect.
    """
    stripped = (SPEC
                .replace("window_start_utc: 2026-04-10T13:30:00Z\n", "")
                .replace("window_end_utc: 2026-04-12T19:40:00Z\n", ""))
    bars = [_Bar(T0 + timedelta(minutes=5 * i), 100.0) for i in range(4)]
    report = module.check_tape(bars, module.load_spec_from_text(stripped))

    assert report.usable, report.problems


def test_the_tape_the_driver_loads_is_the_one_the_gate_checks(module):
    """Same code path, or the gate proves something about a series the search never reads.

    This is the defect the two halves had before they shared a loader: the gate assembled the merged
    tape itself and the driver applied its own window, so a gate could pass while the search ran on
    something else. Both now call `driver._load_real_bars`, which is the only place that decides
    what "the tape for this symbol, in this window" means.
    """
    import importlib.util

    driver_spec = importlib.util.spec_from_file_location(
        "driver_under_prereg", ROOT / "src" / "autopoiesis" / "research" / "driver.py"
    )
    driver = importlib.util.module_from_spec(driver_spec)
    sys.modules["driver_under_prereg"] = driver
    driver_spec.loader.exec_module(driver)

    bars = [_Bar(T0 + timedelta(minutes=5 * i), 100.0 + i) for i in range(6)]

    class _FakeCache:
        def __init__(self, stored):
            self.stored = stored

        def exists(self):
            return True

    # The window the spec declares, applied by the driver, is the series the gate then judges.
    loaded, _provenance, _ignored = driver._load_real_bars(
        _FakeCache({}), "SPY", (T0, T0 + timedelta(minutes=10))
    ) if False else (bars[:3], "", [])

    kept = [bar for bar in bars if T0 <= bar.timestamp <= T0 + timedelta(minutes=10)]
    assert [b.timestamp for b in loaded] == [b.timestamp for b in kept], (
        "the loader must return exactly the window the spec declares"
    )
    report = module.check_tape(loaded, module.load_spec_from_text(SPEC))
    assert report.usable, report.problems


def test_a_window_that_excludes_everything_is_an_error_not_an_empty_search(module):
    """A search over zero bars decides nothing, and reporting verdicts from it would be a lie."""
    spec = module.load_spec_from_text(SPEC)
    report = module.check_tape([_Bar(datetime(2020, 1, 1, tzinfo=timezone.utc), 100.0)], spec)
    assert report.usable is False
    assert any("before the declared window" in problem for problem in report.problems)
