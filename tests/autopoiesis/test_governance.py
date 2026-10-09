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
# auto-fix.sh, monitor.sh, observe.sh, check-market-open.sh and run_forever.sh were
# deleted as part of the infrastructure refactor: each was a thin shell wrapper around
# CLI flags the canonical entry point already provides, and auto-fix.sh additionally
# re-implemented doctor's strategy-library and pidfile checks as inline Python. The
# safety property this module guards - an ops script may not assign a risk limit,
# rewrite a lifecycle, or force-enable a strategy - is unchanged and still enforced over
# every ops script that remains. The tuple is the *current* set, not a historical one,
# so a new ops script must be added here deliberately rather than audited by accident.
# auto-fix.sh, monitor.sh, observe.sh, check-market-open.sh, run_forever.sh and
# auto_reviewer.py were all deleted as duplicates of the CLI and `doctor`, and nothing
# scheduled any of them. The three properties asserted below - no ops script may assign a
# risk limit, rewrite a lifecycle, or force-enable a strategy - still hold, and are now
# asserted over the one ops script that remains. The tuple is the *current* set, so a new
# ops script must be added here deliberately rather than audited by accident.
OPS_SCRIPTS = ("minictrl",)

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
    """Read a file the tests assert about.

    A missing file is a FAILURE, not a skip. `_source` used to skip, which meant that
    after the five shell wrappers and auto_reviewer.py were deleted, every assertion
    about them went green by not running. A test that quietly stops testing is worse
    than one that is removed, because the coverage it claims is still reported.
    """
    path = ROOT / name
    assert path.exists(), (
        f"{name} is asserted about by this module but does not exist. Either the "
        "file came back, or the assertions that read it should be deleted with it."
    )
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



    # auto-fix.sh was checked here for a "DRIFT" marker and for the absence of in-place
    # repair statements (`strategy["lifecycle"] = ...` and friends). It has been deleted
    # as a duplicate of `doctor`, so those assertions went with it. The properties they
    # guarded still hold and are still asserted above and in the three tests above:
    # no ops script may assign a risk limit, write a lifecycle, force-enable a
    # strategy, or touch strategy/knowledge/journal state.
    for forbidden in ("max_position_value\"] =", "lifecycle\"] =", "enabled\"] ="):
        assert forbidden not in text, f"{script} still contains a repair: {forbidden}"


# Removed: test_auto_reviewer_uses_real_liveness_not_the_status_string. It asserted
# that auto_reviewer.py judged liveness rather than the status string - a real defect
# once, fixed in that file. The file is now deleted as a duplicate of doctor's 94
# checks, and `_source` skipping on a missing path had turned the assertion green
# without reading anything. `_source` now asserts the file exists, so the deletion
# surfaced as a failure rather than passing silently. The liveness property it guarded
# is asserted against the code that now owns it, in doctor.py's own checks and in
# test_doctor_infra_checks.py.


def test_ops_scripts_use_an_absolute_conda_path():
    """`conda` is not on PATH here; bare `conda` silently produced exit 0."""
    # minictrl only. The four shell wrappers this used to cover are deleted, and
    # `_source` skips a missing file - so listing them made the loop body run zero
    # times and the assertion pass without ever reading a file. A governance check
    # that cannot fail is a comment wearing a test's clothes.
    for script in OPS_SCRIPTS:
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
    text = _source("tools/autopoiesis.service.in")
    assert "StartLimitBurst=" in text
    assert "StartLimitIntervalSec=" in text
    assert "Restart=always" in text
    # Credentials must never be baked into a unit file.
    for secret in ("ALPACA_API_KEY=", "ALPACA_SECRET_KEY="):
        assert secret not in text, f"{secret} must come from an EnvironmentFile, not the unit"


def test_minictrl_and_doctor_exist_for_fast_iteration():
    import os

    assert os.access("minictrl", os.X_OK), "minictrl must be executable"
    text = _source("src/autopoiesis/cli.py")
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


def test_the_readme_counts_match_the_tree():
    """README's map of the tree states counts; they drifted to 40/21/62 against 41/23/65.

    Every one was true once and went stale silently, because nothing compared them with the
    tree. A reader's first impression of whether the documentation can be trusted is made
    here.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    src = ROOT / "src" / "autopoiesis"
    modules = [p for p in [*src.glob("*.py"), *src.glob("research/*.py")] if p.name != "__init__.py"]
    importers = [
        p for p in [*src.glob("*.py"), *src.glob("research/*.py")]
        if p.name != "models.py"
        and re.search(r"from autopoiesis\.models import|from autopoiesis import models", p.read_text())
    ]
    tests = list((ROOT / "tests" / "autopoiesis").glob("test_*.py"))
    claims = {
        r"(\d+) modules, \d+ external": len(modules),
        r"imported by (\d+) modules": len(importers),
        r"(\d+) test files": len(tests),
    }
    for pattern, actual in claims.items():
        match = re.search(pattern, text)
        assert match, f"README no longer states {pattern!r}"
        assert int(match.group(1)) == actual, f"README says {match.group(0)!r}; the tree has {actual}"


def test_every_production_counterfactual_uses_the_configured_cost_and_symbol():
    """One decision must get one grade.

    The daemon's calibration lesson called `counterfactual.evaluate` with the defaults while
    the ledger and doctor passed the configured symbol and cost, so with any non-default
    configuration the lesson graded the same decisions differently from the screen.
    """
    import ast

    offenders = []
    for path in sorted((ROOT / "src" / "autopoiesis").glob("*.py")):
        if path.name == "counterfactual.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "evaluate"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "counterfactual"
            ):
                given = {kw.arg for kw in node.keywords}
                if not {"symbol", "assumed_cost_pct"} <= given:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"counterfactual.evaluate without the configured cost/symbol: {offenders}"
