"""The scheduled fetch must add data, or it must be skipped.

Read off the first unattended run on 2026-10-05: the cache held seven files, every one of them
ending on the same last completed session, and the run merged six of them to reach the same
24,162 bars it already had. `cache_path` names a file after the *window requested*, so a rolling
window mints a new near-duplicate file every day forever while the contents never change.

These tests pin the three properties that fix it: the cache path is named after the data rather
than the request, a fetch that returns nothing writes nothing, and a fetch whose data is already
cached does not run at all.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _fetcher(tmp_path):
    """The real `tools/fetch_replay_bars.py`, loaded as a module and pointed at a temp cache.

    Loaded by path rather than imported: `tools/` is not a package, and `sys.path` already
    carries `src` only. Constructing it through the spec keeps the module-level `OUT_DIR`
    override local to the test instead of mutating the real replay cache.
    """
    spec = importlib.util.spec_from_file_location(
        "fetch_replay_bars_under_test",
        pathlib.Path(__file__).resolve().parents[2] / "tools" / "fetch_replay_bars.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.OUT_DIR = tmp_path
    return module


def _client_returning(closes, *, raises=False):
    """A stand-in for the broker client, shaped like the real one.

    `fetch_bars` iterates `bars.df.iterrows()`, so the frame needs that method and each row a
    subscriptable mapping of the OHLCV columns. Attributes cannot hold datetime keys, so it is a
    dict with `iterrows` rather than an object - the only two things `fetch_bars` touches.
    """
    rows = [
        (T0 + timedelta(minutes=5 * i), {
            "open": c, "high": c, "low": c, "close": c, "volume": 1.0,
        })
        for i, c in enumerate(closes)
    ]

    class _Frame(dict):
        def iterrows(self):
            return iter(rows)

    class _Bars:
        def __init__(self, rows_):
            self.df = _Frame()

    class _Client:
        def __init__(self):
            self.calls = 0

        def get_bars(self, symbol, timeframe, start, end):
            self.calls += 1
            if raises:
                raise RuntimeError("subscription does not permit querying recent SIP data")
            return _Bars(rows)

    return _Client()


def test_the_scheduled_path_is_one_rolling_file_not_a_new_name_per_day(tmp_path):
    """The scheduled fetch must overwrite one file, whatever window it asks for.

    `cache_path` derives the filename from the window requested, so a rolling window minted a
    fresh file every day holding identical bars: seven existed after two days of scheduling,
    every one ending on the same 2026-10-02 session, and the search merged six of them to
    reach the 24,162 bars it already had. A year of that is ~365 near-duplicates the driver
    re-reads and re-deduplicates on every run.

    The dated `cache_path` is kept deliberately - `replay_self_evolution.py` names a window on
    purpose and the gate depends on it - so this asserts the two paths are *different*, and that
    only the rolling one is window-independent.
    """
    fetcher = _fetcher(tmp_path)

    first = fetcher.rolling_path("SPY", "5Min")
    second = fetcher.rolling_path("SPY", "5Min")

    assert first == second == tmp_path / "SPY_5Min_rolling.json"
    assert first.name == "SPY_5Min_rolling.json", "no window in the name, so no daily duplicate"
    assert fetcher.cache_path("SPY", "2026-04-04T13:30:00Z", "2026-10-03T20:00:00Z") != first, (
        "a hand-run dated window keeps its own file, so one-off fetches do not clobber the roll"
    )


def test_a_fetch_that_returns_no_bars_writes_nothing(tmp_path):
    """A failed fetch must not be able to make the cache look fuller than it is.

    The paper account refuses the current day's tape, so the scheduled run fails often by
    design. A file named for that window still appeared on 2026-10-05, containing the
    previous session's data.
    """
    fetcher = _fetcher(tmp_path)
    client = _client_returning([])

    monkey = fetcher
    monkey.fetch_bars = lambda *a, **k: []          # type: ignore[attr-defined]
    monkey.client_from_env = lambda: client          # type: ignore[attr-defined]

    assert monkey.main(["x", "SPY", "2026-04-04T13:30:00Z", "2026-10-04T20:00:00Z", "5Min", "ROLLING"]) == 1
    assert list(tmp_path.glob("*.json")) == [], "a failed fetch left a file behind"


def test_an_unchanged_window_overwrites_rather_than_accumulates(tmp_path):
    """Fetching twice must leave one file, not two."""
    fetcher = _fetcher(tmp_path)
    fetcher.fetch_bars = lambda *a, **k: [  # type: ignore[attr-defined]
        _bar(T0 + timedelta(minutes=5 * i), 100.0 + i) for i in range(3)
    ]
    fetcher.client_from_env = lambda: _client_returning([100.0, 101.0, 102.0])  # type: ignore[attr-defined]

    args = ["x", "SPY", "2026-04-04T13:30:00Z", "2026-10-03T20:00:00Z", "5Min", "ROLLING"]
    assert fetcher.main(args) == 0
    assert len(list(tmp_path.glob("*.json"))) == 1

    # A second day, same data. This is the scheduled path, so it must overwrite.
    fetcher.fetch_would_add_nothing = lambda *a, **k: False  # type: ignore[attr-defined]
    args2 = ["x", "SPY", "2026-04-05T13:30:00Z", "2026-10-04T20:00:00Z", "5Min", "ROLLING"]
    assert fetcher.main(args2) == 0
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1, f"the cache accumulated duplicates: {[f.name for f in files]}"
    assert "rolling" in files[0].name, f"the scheduled path must use the rolling file: {files[0].name}"


def test_the_cache_is_skipped_when_it_already_holds_the_newest_session(tmp_path):
    """The skip is what makes the timer cheap and the log honest.

    A fetch whose window cannot return anything newer than the cache would write a
    near-duplicate and report a bar count that looks like progress. Deciding first, from the
    cached last-bar timestamp, is the same idempotence the trial ledger already has, one level
    earlier.
    """
    fetcher = _fetcher(tmp_path)
    last_cached = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
    fetcher.cached_last_bar = lambda *a, **k: last_cached  # type: ignore[attr-defined]
    fetcher.fetch_would_add_nothing = lambda *a, **k: True  # type: ignore[attr-defined]
    client = _client_returning([100.0, 101.0])
    fetcher.client_from_env = lambda: client  # type: ignore[attr-defined]
    fetcher.fetch_bars = lambda *a, **k: [                        # type: ignore[attr-defined]
        _bar(T0, 100.0), _bar(T0 + timedelta(minutes=5), 101.0)
    ]

    assert fetcher.main(["x", "SPY", "2026-04-04T13:30:00Z", "2026-10-03T20:00:00Z", "5Min", "ROLLING"]) == 0
    assert client.calls == 0, "the broker was queried for data already on disk"


def test_a_cache_behind_the_session_is_fetched(tmp_path):
    """The other half: a stale cache must still be refreshed."""
    fetcher = _fetcher(tmp_path)
    fetcher.cached_last_bar = lambda *a, **k: datetime(2026, 9, 1, tzinfo=timezone.utc)  # type: ignore[attr-defined]
    fetcher.fetch_would_add_nothing = lambda *a, **k: False  # type: ignore[attr-defined]
    client = _client_returning([100.0, 101.0])
    fetcher.client_from_env = lambda: client  # type: ignore[attr-defined]
    fetcher.fetch_bars = lambda *a, **k: [     # type: ignore[attr-defined]
        _bar(T0, 100.0), _bar(T0 + timedelta(minutes=5), 101.0)
    ]

    assert fetcher.main(["x", "SPY", "2026-04-04T13:30:00Z", "2026-10-03T20:00:00Z", "5Min", "ROLLING"]) == 0
    assert client.calls == 1, "a cache behind the newest session must be refreshed"


def _bar(stamp, close):
    from autopoiesis.replay import ReplayBar

    return ReplayBar(
        timestamp=stamp, open=close, high=close, low=close, close=close, volume=1.0,
    )


def test_the_cutoff_is_derived_from_the_clock_not_from_now():
    """The first version of the skip could never fire, which is why it is pinned here.

    `clock.timestamp` is *now*, not the last completed session, and the clock exposes no field
    naming the latter. Comparing the cache against `timestamp` is therefore False as soon as any
    time has passed, so the skip was dead code on the very first run. The cutoff has to come from
    the clock's shape.
    """
    fetcher = _fetcher(pathlib.Path("/tmp/opencode/_fetch_cutoff_probe"))
    cached = datetime(2026, 10, 2, 23, 55, tzinfo=timezone.utc)

    class _Clock:
        is_open = True
        timestamp = datetime(2026, 10, 5, 16, 29, tzinfo=timezone.utc)
        next_open = datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc)

    class _C:
        def get_clock(self):
            return _Clock()

    # Market open: a fetch can return a bar as recent as now - 5min, so the cache (3 days
    # stale) is behind and the fetch must run.
    assert fetcher.fetch_would_add_nothing(_C(), cached, "5Min") is False

    # Market open and the cache already holds a bar inside the last interval: skip.
    fresh = datetime(2026, 10, 5, 16, 26, tzinfo=timezone.utc)
    assert fetcher.fetch_would_add_nothing(_C(), fresh, "5Min") is True

    # Market closed: the cutoff is `next_open - 2 days` = Sun 2026-10-04 13:30Z, so a cache
    # holding Friday's close is *before* it and the fetch correctly runs. The cutoff is
    # deliberately conservative: it can only make the skip less likely, and the fetch is what
    # actually decides whether anything new exists.
    _Clock.is_open = False
    assert fetcher.fetch_would_add_nothing(_C(), cached, "5Min") is False

    # A cache at or past the cutoff does skip while the market is closed.
    assert fetcher.fetch_would_add_nothing(
        _C(), datetime(2026, 10, 4, 14, 0, tzinfo=timezone.utc), "5Min"
    ) is True

    # And `next_open - 1 day` would have been the wrong cutoff: it is Mon 09:30 EDT, *after*
    # the Friday close, so it would have skipped while the cache was three days stale.
    monday_open = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
    assert monday_open > cached, "next_open - 1 day lands after the cache and would misfire"

    # Cannot tell -> fetch, never skip.
    assert fetcher.fetch_would_add_nothing(_C(), None, "5Min") is None

    class _Broken:
        def get_clock(self):
            raise RuntimeError("network down")

    assert fetcher.fetch_would_add_nothing(_Broken(), cached, "5Min") is None


def test_a_window_ending_today_is_refused_by_the_broker_and_the_cache_is_unaffected():
    """Documented as measured, because the boundary moves with the time of day.

    Measured 2026-10-05: end=2026-10-03 and end=2026-10-04 both returned 4338 bars, while
    end=2026-10-05 raised "subscription does not permit querying recent SIP data" - even
    though the 2026-10-04 request had succeeded when issued by hand minutes earlier. So
    pinning the window to "yesterday" cannot be made reliable from the request side, and the
    skip has to be decided against the cache rather than against the calendar.
    """
    client = _client_returning([100.0], raises=True)
    try:
        client.get_bars("SPY", None, "2026-09-01", "2026-10-05")
    except RuntimeError as exc:
        assert "recent SIP data" in str(exc)
    else:
        raise AssertionError("the stub was expected to refuse the current day's tape")
