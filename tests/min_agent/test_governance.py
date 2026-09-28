"""Machine-enforced AGENTS.md invariants for the operations layer.

AGENTS.md section 6 requires:

    no bypass of Guardian risk checks;
    no automatic weakening of hard risk limits.

Neither of those is enforceable by inspection alone, so it is enforced here.
The scripts this module covers previously rewrote strategy JSON files to force
`enabled=true`, force `lifecycle="PROBATION"`, and raise
`max_position_value` to a hard-coded 5000.0. That is how
`fixed-size-sell-001` ended up `enabled=true, lifecycle=RETIRED` while
`auto-fix.log` reported it as healthy.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Shell/Python ops scripts that are allowed to touch runtime state at all.
OPS_SCRIPTS = ("auto-fix.sh", "auto_reviewer.py")

# Any write of these keys, from a script outside Guardian, is a forbidden
# automatic risk relaxation.
FORBIDDEN_ASSIGNMENT = re.compile(
    r"""(?:max_position_value|max_daily_loss|max_trades_per_day|max_total_exposure|min_confidence)\s*["']?\s*[:=]\s*"""
    r"""(?!\s*(?:None|null))(?P<value>[0-9][0-9_.]*)""",
    re.IGNORECASE,
)

# Rewriting a lifecycle field from an ops script overrides a decision the
# StrategyLifecycleManager made from real evidence.
FORBIDDEN_LIFECYCLE_WRITE = re.compile(
    r"""["']lifecycle["']\s*[:=]\s*["'](?P<value>PROBATION|ACTIVE|PAUSED|RETIRED|BASELINE)["']""",
    re.IGNORECASE,
)

FORBIDDEN_ENABLE_WRITE = re.compile(
    r"""["']enabled["']\s*[:=]\s*(?:True|true|1)""",
    re.IGNORECASE,
)

WRITE_CALL = re.compile(r"""write_text|json\.dump|\bopen\s*\(.*["']w""")


def _source(name: str) -> str:
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} is not present")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("script", OPS_SCRIPTS)
def test_ops_scripts_never_raise_a_hard_risk_limit(script):
    text = _source(script)
    for match in FORBIDDEN_ASSIGNMENT.finditer(text):
        value = match.group("value")
        # Reading a limit for display is fine; writing one is not. Assignments
        # are keyed by a bare number, which no read-only line produces.
        raise AssertionError(
            f"{script} assigns a risk limit ({match.group(0).strip()!r}). "
            "Risk limits may only change through Guardian + StrategyAdmission."
        )


@pytest.mark.parametrize("script", OPS_SCRIPTS)
def test_ops_scripts_never_rewrite_a_strategy_lifecycle(script):
    text = _source(script)
    for match in FORBIDDEN_LIFECYCLE_WRITE.finditer(text):
        raise AssertionError(
            f"{script} writes lifecycle={match.group('value')!r}. "
            "Lifecycle is owned by StrategyLifecycleManager."
        )


@pytest.mark.parametrize("script", OPS_SCRIPTS)
def test_ops_scripts_never_force_enable_a_strategy(script):
    text = _source(script)
    for match in FORBIDDEN_ENABLE_WRITE.finditer(text):
        raise AssertionError(
            f"{script} writes enabled=True. Re-enabling a strategy the lifecycle "
            "manager retired bypasses the admission gate."
        )


@pytest.mark.parametrize("script", OPS_SCRIPTS)
def test_ops_scripts_do_not_write_runtime_state(script):
    """Combined with the checks above: these scripts must be observability only."""
    text = _source(script)
    for match in WRITE_CALL.finditer(text):
        line = text[: match.start()].count("\n") + 1
        source_line = text.splitlines()[line - 1]
        # Writing its own report/heartbeat output is allowed; writing strategy
        # or knowledge files is not.
        if re.search(r"strateg|knowledge|journal|reflection|heartbeat", source_line, re.IGNORECASE):
            if "REVIEW_DIR" not in source_line and "report_file" not in source_line:
                raise AssertionError(f"{script}:{line} writes runtime state: {source_line.strip()!r}")


def test_auto_fix_script_is_read_only_by_construction():
    """auto-fix.sh previously rewrote the four core strategy files in place."""
    text = _source("auto-fix.sh")
    assert "drift_count" in text, "auto-fix.sh should be a detect-and-report drift check"
    assert "DRIFT" in text
    for forbidden in ("max_position_value\"] =", "lifecycle\"] =", "enabled\"] ="):
        assert forbidden not in text, f"auto-fix.sh still contains a repair: {forbidden}"


def test_auto_reviewer_uses_real_liveness_not_the_status_string():
    text = _source("auto_reviewer.py")
    assert "daemon_not_live" in text, "reviewer must judge liveness, not just the status string"
    assert ".get(\"live\")" in text or "get('live')" in text


def test_ops_scripts_use_an_absolute_conda_path():
    """`conda` is not on PATH here; bare `conda` silently produced exit 0."""
    for script in OPS_SCRIPTS + ("monitor.sh", "observe.sh", "check-market-open.sh", "run_forever.sh", "minictrl"):
        text = _source(script)
        bare = re.findall(r"(?<![/\w-])conda run", text)
        assert not bare, f"{script} invokes bare `conda run` {len(bare)} time(s); use CONDA_BIN"


def test_minictrl_propagates_real_exit_codes():
    """The bug this project spent a review cycle hiding: `set -e` without
    `pipefail` means a `| tee` pipeline reports tee's status, so a missing
    interpreter or an unreachable broker printed a green banner and exited 0."""
    text = _source("minictrl")
    assert "set -uo pipefail" in text
    assert "esac" in text
    # No `|| true` or `|| echo` swallowing a subcommand's status.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("agent ") or stripped.startswith("exec "):
            assert "|| true" not in stripped, f"exit code swallowed: {stripped}"
            assert "|| echo" not in stripped, f"exit code swallowed: {stripped}"


def test_every_ops_shell_script_uses_pipefail():
    """`set -e` alone lets a `| tee` pipeline report success."""
    import glob

    for path in sorted(glob.glob("*.sh")) + ["minictrl"]:
        text = _source(path)
        assert "pipefail" in text, f"{path} does not enable pipefail"


def test_the_service_unit_bounds_its_restarts():
    text = _source("tools/min-agent.service.in")
    assert "StartLimitBurst=" in text
    assert "StartLimitIntervalSec=" in text
    assert "Restart=always" in text
    # Credentials must never be baked into a unit file.
    for secret in ("ALPACA_API_KEY=", "ALPACA_SECRET_KEY="):
        assert secret not in text, f"{secret} must come from an EnvironmentFile, not the unit"


def test_minictrl_and_doctor_exist_for_fast_iteration():
    import os

    assert os.access("minictrl", os.X_OK), "minictrl must be executable"
    text = _source("src/min_agent/cli.py")
    assert '"--doctor"' in text
    assert "run_doctor" in text


def test_every_recorded_defect_is_still_fixed():
    """tools/audit_defects.py re-verifies all 15 defects from the recovery plan.

    A regression in any of them is a regression in the whole point of the
    refactor, so the audit runs as part of the suite rather than being a script
    someone has to remember to invoke.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "tools/audit_defects.py"],
        cwd=root, capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(root / "src")},
    )
    assert result.returncode == 0, f"defect audit failed:\n{result.stdout[-3000:]}"
    assert "checks pass across 15 defects" in result.stdout
