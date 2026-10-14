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
        ["make", "--no-print-directory", "fetch-replay-bars", "ENVFILE=/dev/null", f"PY={python}"],
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
            "make", "--no-print-directory", "fetch-replay-bars", "ENVFILE=/dev/null", f"PY={python}",
            "REPLAY_SYMBOL=SPY", "REPLAY_START=2026-04-10T13:30:00Z",
            "REPLAY_END=2026-10-09T19:40:00Z", "REPLAY_INTERVAL=5Min", "REPLAY_ROLLING=1",
            # Named explicitly rather than inherited. `make` exports the operator
            # environment file's variables to everything it runs, so under `make verify`
            # this subprocess arrived with `REPLAY_CACHE` already set to an absolute path
            # and the assertion below compared that against the bare default. The subject
            # of this test is that the target forwards an auditable UTC window, and a
            # configured cache directory is legitimate; the default is not a contract.
            "REPLAY_CACHE=runtime/autopoiesis/replay",
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
        "--cache", "runtime/autopoiesis/replay",
    ]


def test_scheduled_research_uses_the_same_makefile_entry_points():
    template = (ROOT / "tools" / "research.service.in").read_text(encoding="utf-8")

    assert "research-run SPEC=${AUTOPOIESIS_RESEARCH_SPEC}" in template
    assert "/bin/sh -c" not in template
    assert "existing cache anyway" not in template
    assert "date -u" not in template
    assert "@PYTHON@ tools/fetch_replay_bars.py" not in template


def test_make_search_requires_spec_before_running_python(tmp_path):
    log = tmp_path / "called.txt"
    python = _fake_python(tmp_path / "fake-python")
    result = subprocess.run(["make", "--no-print-directory", "research-search",
        "ENVFILE=/dev/null", "SPEC=", f"PY={python}"], cwd=ROOT,
        env={**os.environ, "FETCH_CALL_LOG": str(log)}, capture_output=True, text=True)
    assert result.returncode != 0
    assert "SPEC=" in result.stderr
    assert not log.exists()


def test_make_run_derives_fetch_settings_and_stops_after_fetch_failure(tmp_path):
    log = tmp_path / "calls.txt"
    python = tmp_path / "fake-python"
    # Synthetic process fixture: no installation, broker access or market data.
    python.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$FETCH_CALL_LOG"
case "$1" in
 tools/check_research_tape.py) printf '%s\\n' QQQ 2026-04-10T13:30:00Z 2026-04-10T20:00:00Z 1Hour ;;
 tools/fetch_replay_bars.py) exit 43 ;;
esac
''')
    python.chmod(0o755)
    result = subprocess.run(["make", "--no-print-directory", "research-run",
        "ENVFILE=/dev/null", "SPEC=synthetic.spec", f"PY={python}",
        f"CONDA_RUN={python}", f"REPLAY_CACHE={tmp_path / 'cache'}"], cwd=ROOT,
        env={**os.environ, "FETCH_CALL_LOG": str(log)}, capture_output=True, text=True)
    assert result.returncode != 0
    calls = log.read_text().splitlines()
    assert any("QQQ 2026-04-10T13:30:00Z 2026-04-10T20:00:00Z 1Hour" in call for call in calls)
    assert any(f"--cache {tmp_path / 'cache'}" in call for call in calls)
    assert not any("autopoiesis.research.driver" in call for call in calls)
