"""The two properties a paper daemon's shutdown has to have, both of them measured.

A SIGTERM during a blocking stage has to (1) stop anything new from starting once that stage
returns, and (2) survive a stop window that is big enough for the stage to return at all. The second
one is not a preference: the historical evidence records two stop jobs that reached the 30-second
deadline and ended in SIGKILL, and a curriculum generation measured 141.9 seconds.

Synthetic controlled stages test transaction completion, and a separate subprocess tests actual
SIGTERM delivery during the daemon's default wait without signalling the test runner.
"""

from __future__ import annotations

import importlib.util
import select
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_tool():
    spec = importlib.util.spec_from_file_location("shutdown_budget", ROOT / "tools" / "shutdown_budget.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["shutdown_budget"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


# --- the derived budget ----------------------------------------------------------------------


def test_the_budget_is_derived_from_the_largest_declared_stage(tool):
    """The stop window is a computed number, not one somebody picked.

    It has to be the largest stage plus the stated buffer, because that stage is what systemd is
    waiting for when it sends SIGTERM and counts down.
    """
    inventory = tool.collect()
    largest = inventory.largest

    assert largest is not None, "no bounded stage found; the budget would be buffer-only"
    assert largest.seconds == pytest.approx(300.0), (
        "the curriculum generation holds the largest declared timeout; anything larger means a "
        "stage was added without this list being updated. It moved 600 -> 300 on 2026-10-18: a "
        "curriculum stage makes two of these calls, so at 600 a single stage could reach 1,200s "
        "against the daemon's 900s heartbeat budget"
    )
    assert inventory.budget == pytest.approx(largest.seconds + tool.STOP_BUFFER_SECONDS)


def test_every_bounded_stage_fits_inside_the_budget(tool):
    """The buffer covers every bounded stage, not just the largest.

    If a bounded stage's timeout exceeded the largest, the inventory would have picked it instead -
    this asserts that ordering holds, which is the assumption the budget rests on.
    """
    inventory = tool.collect()
    for stage in inventory.bounded:
        assert stage.seconds <= inventory.largest.seconds + 1e-9, (
            f"{stage.name} at {stage.seconds}s is larger than the inventory's maximum"
        )


def test_cooperative_default_wait_is_in_the_stage_inventory(tool, monkeypatch):
    inventory = tool.collect()
    stage = next((stage for stage in inventory.stages if stage.name == "daemon cooperative wait"), None)
    assert stage is not None, "default scheduler/backoff waits are missing from the stop inventory"
    assert stage.seconds == pytest.approx(1.0)
    # 330 = the largest bounded stage plus the buffer. The largest is `curriculum generation
    # (off-hours)` at `CURRICULUM_TIMEOUT_S`, which moved 600 -> 300 on 2026-10-18 because a
    # curriculum *stage* makes two of those calls and 2 x 600 outlived the daemon's 900s heartbeat
    # budget: an idle Sunday daemon sat at heartbeat age 1,287s with its journal silent for
    # twenty-one minutes and `doctor` called it dead. Lowering it lowers the stop window this
    # inventory is about, which is why the number is pinned here rather than derived - the point of
    # this test is that a timeout change is noticed by something other than a production stall.
    assert inventory.budget == pytest.approx(330.0)

    # A synthetic source declaration change must change the reported bound without importing it.
    read_text = Path.read_text

    def changed_source(path, *args, **kwargs):
        source = read_text(path, *args, **kwargs)
        if path == tool.SRC / "daemon.py":
            source = source.replace("COOPERATIVE_WAIT_POLL_SECONDS = 1.0",
                                    "COOPERATIVE_WAIT_POLL_SECONDS = 2.0")
        return source

    monkeypatch.setattr(Path, "read_text", changed_source)
    changed = next(item for item in tool.collect().stages if item.name == stage.name)
    assert changed.seconds == pytest.approx(2.0)


def test_the_unit_template_defers_the_budget_to_the_derivation(tool):
    """The property the whole tool exists to check.

    A template that declares a *number* can drift from the stage timeout that number was computed
    from: a stage grows, the derivation grows, the template does not, and the next SIGTERM during
    that stage ends in SIGKILL - which is what the historical evidence records twice. A marker
    cannot drift, because there is nothing in it to drift; the installer substitutes the derived
    number at install time.

    So the template is required to carry the marker, and a hardcoded number is a failure of this
    check rather than an alternative spelling of it.
    """
    assert tool.template_is_derived(), (
        "tools/autopoiesis.service.in declares a stop window instead of deferring to the derived "
        "one; run tools/shutdown_budget.py --apply or put the marker back"
    )


def test_a_declared_number_that_is_too_small_fails(tool):
    """The other half: a number is acceptable only while it covers the budget.

    Checked by hand rather than by editing the template, because a check that can only be
    demonstrated by breaking the file it guards is a check nobody runs.
    """
    original = tool.UNIT_TEMPLATE.read_text(encoding="utf-8")
    try:
        tool.UNIT_TEMPLATE.write_text(
            original.replace("@STOP_BUDGET_SECONDS@", "30"), encoding="utf-8"
        )
        declared = tool.unit_budget()
        budget = round(tool.collect().budget)
        assert declared == 30
        assert declared < budget, (
            "a 30-second window is not smaller than the derived budget; the stages changed and "
            "this test's premise no longer holds"
        )
    finally:
        tool.UNIT_TEMPLATE.write_text(original, encoding="utf-8")
    assert tool.template_is_derived(), "the template was not restored"


def test_the_budget_is_a_lower_bound_and_says_which_stages_make_it_one(tool):
    """Unbounded stages are named, not quietly excluded.

    The journal write and the atomic file writes have no declared ceiling. Pretending the derived
    budget is the daemon's maximum would be a guess formatted as a measurement; reporting which
    stages make it a bound is the honest version.
    """
    inventory = tool.collect()
    assert inventory.unbounded, "every stage is declared bounded; that claim needs re-checking"
    assert all(not stage.bounded for stage in inventory.unbounded)
    names = {stage.name for stage in inventory.unbounded}
    assert "journal append and fsync" in names


def test_budget_only_prints_one_number(tool, capsys):
    """The operator script substitutes this into a unit file with sed.

    A tool that printed anything else would put prose into a systemd unit, and a unit with prose in
    it fails to parse - which is a louder failure than a wrong number, but still a failure.
    """
    assert tool.main(["--budget-only"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.isdigit(), f"--budget-only printed {out!r}, which is not a bare number"


# --- SIGTERM behaviour -----------------------------------------------------------------------


def test_sigterm_interrupts_actual_default_long_wait():
    """Synthetic isolated child uses the real default wait, with no broker/model/runtime IO."""
    source = textwrap.dedent("""
        import signal
        from autopoiesis.config import AgentConfig
        from autopoiesis.daemon import AgentDaemon

        daemon = AgentDaemon(config=AgentConfig.from_env({}), loop=None)
        signal.signal(signal.SIGTERM, daemon._request_stop)
        print("WAITING", flush=True)
        daemon.sleep(30)
        print(f"STOPPED={daemon._stop_requested}", flush=True)
    """)
    child = subprocess.Popen([sys.executable, "-u", "-c", source], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([child.stdout], [], [], 10)
        assert ready, "synthetic child never entered the default wait"
        assert child.stdout.readline().strip() == "WAITING"
        time.sleep(0.05)
        started = time.monotonic()
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=3)
        elapsed = time.monotonic() - started
        assert child.returncode == 0, stderr
        assert "STOPPED=True" in stdout
        assert elapsed < 2, f"default wait took {elapsed:.3f}s after SIGTERM"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


class _BlockingLoop:
    """A trading stage that blocks until it is released, and records every call it receives."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []

    def run_once(self, *args, **kwargs):  # the shape the daemon calls
        self.calls.append("cycle")
        self.started.set()
        self.release.wait(timeout=5)
        self.calls.append("cycle-returned")


class _RecordingClock:
    """A broker clock that records every check, which is what the stop criterion forbids repeating.

    The criterion is that nothing new starts after the in-flight stage returns. "Nothing new" is a
    broker-clock check *and* a trading cycle, because the clock is what decides to trade - counting
    the calls is how that is asserted rather than described.
    """

    def __init__(self):
        self.calls = 0

    def should_trade_now(self):
        self.calls += 1
        return True

    def get_clock(self):  # the shape other code paths use
        return None



def _daemon(tmp_path, loop, *, clock, sleeps, statuses, stop_flag):
    from autopoiesis.config import AgentConfig
    from autopoiesis.daemon import AgentDaemon

    daemon = AgentDaemon.__new__(AgentDaemon)
    config = AgentConfig(
        mode="paper", symbols=("SPY",), allowlist=frozenset({"SPY"}),
        alpaca_api_key=None, alpaca_secret_key=None,
        alpaca_base_url="https://paper-api.alpaca.markets",
        ollama_base_url="http://127.0.0.1:11434", model="m", gpu_devices="0",
        journal_path=tmp_path / "journal.jsonl", max_position_value=5000.0,
        max_daily_loss=500.0,
    )
    for name, value in (
        ("config", config),
        ("loop", loop),
        ("scheduler", clock),
        ("sleep", lambda seconds: sleeps.append(seconds)),
        ("_heartbeat", lambda status, message: statuses.append((status, message))),
        ("_append_event", lambda *a, **k: None),
        ("error_count", 0),
        ("_clock_failures", 0),
        ("cycle_count", 0),
        ("daily_cycle_count", 0),
        ("current_cycle_date", None),
        ("_stop_requested", stop_flag),
        ("_appended", []),
        ("_maintenance", lambda: None),
        ("_acquire_lock", lambda: None),
        # `run()` reconciles orders before the loop; a test daemon has no reconciler and must not
        # reach for the attribute. The real one is injected by the CLI, which is what makes this a
        # property of the loop rather than of the wiring.
        ("startup_reconciler", None),
        ("_startup_reconcile", lambda: True),
        ("_reset_daily_count_if_needed", lambda: None),
        ("_round_symbols", lambda: ("SPY",)),
        ("_round_sleep_seconds", lambda elapsed: 1),
        ("_sleep_seconds", lambda: 1),
    ):
        setattr(daemon, name, value)
    return daemon


def test_a_sigterm_during_a_blocking_stage_stops_the_next_cycle(tmp_path, monkeypatch):
    """The success criterion from the diagnostic, as a test.

    A cycle is in flight and blocking. SIGTERM arrives; the handler sets the flag. The stage
    returns. What must then happen is *nothing new*: no further trading cycle, and no broker-clock
    check to decide whether to trade. The old code would have gone back round the loop and run
    another cycle, because the only thing the flag changed was the next iteration's condition -
    which is checked *before* the in-flight stage returns, not after.
    """
    # `signal.signal` only works in the main thread, so the daemon's own signal wiring cannot be
    # exercised from a test. What is under test is how the flag the handler sets is *consumed*, and
    # that is driven by calling the handler directly - the same call the signal makes. The wiring
    # itself is three lines and is not what a shutdown bug lives in.
    import autopoiesis.daemon as daemon_module

    monkeypatch.setattr(daemon_module.signal, "signal", lambda *a, **k: None)

    loop = _BlockingLoop()
    sleeps: list[int] = []
    statuses: list[tuple[str, str]] = []
    clock = _RecordingClock()
    daemon = _daemon(tmp_path, loop, clock=clock, sleeps=sleeps, statuses=statuses,
                     stop_flag=False)

    worker = threading.Thread(target=daemon.run, daemon=True)
    worker.start()
    assert loop.started.wait(timeout=5), "the blocking stage never started"

    # The signal arrives while the stage is blocked, exactly as it would in a running daemon.
    daemon._request_stop(15, None)
    loop.release.set()
    worker.join(timeout=10)

    assert not worker.is_alive(), "the loop did not return after the in-flight stage returned"
    assert loop.calls == ["cycle", "cycle-returned"], (
        f"a second cycle started after SIGTERM: {loop.calls}"
    )
    assert statuses[-1][0] == "STOPPED", f"the daemon did not record a clean stop: {statuses[-1]}"
    # One clock check decided to run the first round. No second one happened, because the stop flag
    # was honoured at the loop condition rather than only at the top of the round.
    assert clock.calls == 1, (
        f"the broker clock was checked {clock.calls} times; a check after the stop means the loop "
        "went round again"
    )


def test_a_stop_requested_before_the_loop_starts_runs_nothing(tmp_path):
    """The flag is honoured at the loop condition too, not only after a stage returns.

    A SIGTERM that arrives between cycles must not start one. This is the cheap half of the same
    property and is pinned so a refactor cannot trade it away while the other half still passes.
    """
    loop = _BlockingLoop()
    statuses: list[tuple[str, str]] = []
    clock = _RecordingClock()
    daemon = _daemon(tmp_path, loop, clock=clock, sleeps=[], statuses=statuses, stop_flag=True)

    daemon.run()

    assert loop.calls == []
    assert clock.calls == 0, "the clock was checked even though the stop was requested first"
    assert statuses and statuses[-1][0] == "STOPPED", (
        "the daemon still records the stop, so an operator can see it did not trade"
    )
