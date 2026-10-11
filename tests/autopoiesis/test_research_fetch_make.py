"""The Makefile exposes explicit, ordered arguments for the real bar fetcher."""

import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _fake_python(path: pathlib.Path) -> pathlib.Path:
    path.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$FETCH_CALL_LOG\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_fetch_target_requires_explicit_dates_before_running_python(tmp_path):
    log = tmp_path / "called.txt"
    python = _fake_python(tmp_path / "fake-python")
    env = {**os.environ, "FETCH_CALL_LOG": str(log)}

    result = subprocess.run(
        ["make", "--no-print-directory", "fetch-replay-bars", f"PY={python}"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "REPLAY_START is required" in result.stderr
    assert not log.exists(), "missing dates must fail before the fetcher can run"


def test_fetch_target_passes_auditable_utc_window_to_existing_fetcher(tmp_path):
    log = tmp_path / "called.txt"
    python = _fake_python(tmp_path / "fake-python")
    env = {**os.environ, "FETCH_CALL_LOG": str(log)}

    result = subprocess.run(
        [
            "make", "--no-print-directory", "fetch-replay-bars", f"PY={python}",
            "REPLAY_SYMBOL=SPY", "REPLAY_START=2026-04-10T13:30:00Z",
            "REPLAY_END=2026-10-09T19:40:00Z", "REPLAY_INTERVAL=5Min", "REPLAY_ROLLING=1",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "tools/fetch_replay_bars.py",
        "SPY",
        "2026-04-10T13:30:00Z",
        "2026-10-09T19:40:00Z",
        "5Min",
        "ROLLING",
    ]


def test_scheduled_research_uses_the_same_makefile_entry_points():
    template = (ROOT / "tools" / "research.service.in").read_text(encoding="utf-8")

    assert "fetch-replay-bars REPLAY_SYMBOL=SPY" in template
    assert "research-search REPLAY_SYMBOL=SPY" in template
    assert "@PYTHON@ tools/fetch_replay_bars.py" not in template
