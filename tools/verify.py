#!/usr/bin/env python
"""One gate for the whole system. Every check class, and a count.

The objective names thirteen classes of check. Until now they were scattered
across `pytest`, `tools/audit_defects.py` and `doctor`, and nothing ran all of
them, so "the tests pass" was never the same claim as "the system is verified".
This runs every class and prints the tally.

Two properties matter more than the tally:

* **It must be able to fail.** `--self-test` runs the gate against a deliberately
  broken fixture and asserts the gate reports failure. A verification command
  that cannot fail is decoration, and is worse than no command because it is
  believed.

* **A new test cannot escape it.** Every test file must be assigned to a class.
  An unclassified file fails the gate. Without that, adding a test quietly moves
  the system from "verified" to "not checked" and nothing says so.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests" / "min_agent"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


#: Every test file, and the check class it belongs to. Assigning a file to more
#: than one class is allowed; leaving one unassigned fails the gate.
TEST_CLASS_MAP: dict[str, tuple[str, ...]] = {
    "test_broker_evidence.py": ("broker-reconciliation", "crash-recovery"),
    "test_cli.py": ("unit-integration",),
    "test_cli_startup.py": ("syntax-import",),
    "test_config.py": ("unit-integration", "shadow-live-consistency"),
    "test_counterfactual.py": ("decision-outcome-counterfactual",),
    "test_curriculum.py": ("unit-integration", "lifecycle-invariants"),
    "test_daemon.py": ("unit-integration", "crash-recovery"),
    "test_data_gateway.py": ("unit-integration", "broker-reconciliation"),
    "test_doctor.py": ("unit-integration", "replay-determinism"),
    "test_durability.py": ("crash-recovery", "replay-determinism"),
    "test_evaluator.py": ("pnl-accounting", "replay-determinism"),
    "test_executor.py": ("broker-reconciliation", "guardian-bypass-prevention"),
    "test_fill_reconciler.py": ("broker-reconciliation",),
    "test_governance_invariants.py": (
        "guardian-bypass-prevention", "shadow-live-consistency",
    ),
    "test_governance.py": (
        "guardian-bypass-prevention", "software-supply-chain", "crash-recovery",
    ),
    "test_guardian.py": ("guardian-bypass-prevention",),
    "test_health.py": ("unit-integration", "broker-reconciliation"),
    "test_journal.py": ("data-integrity", "crash-recovery"),
    "test_knowledge_admission.py": ("lifecycle-invariants",),
    "test_knowledge_library.py": ("lifecycle-invariants", "data-integrity"),
    "test_knowledge.py": ("lifecycle-invariants",),
    "test_llm_decision.py": ("unit-integration",),
    "test_llm_provenance.py": ("replay-determinism", "crash-recovery"),
    "test_loop.py": ("unit-integration", "guardian-bypass-prevention"),
    "test_models.py": ("unit-integration", "data-integrity"),
    "test_order_reconciler.py": ("broker-reconciliation", "crash-recovery"),
    "test_point_in_time.py": ("point-in-time-no-leakage",),
    "test_policy_engine.py": ("unit-integration",),
    "test_reflection_memory.py": ("lifecycle-invariants", "data-integrity"),
    "test_replay_determinism.py": ("replay-determinism",),
    "test_runtime_integrity.py": ("data-integrity",),
    "test_scheduler.py": ("crash-recovery", "data-integrity"),
    "test_score_latch.py": ("unit-integration", "shadow-live-consistency"),
    "test_strategy_admission.py": ("lifecycle-invariants",),
    "test_strategy_engine.py": ("lifecycle-invariants", "shadow-live-consistency"),
    "test_trade_counter.py": ("data-integrity",),
}

#: The thirteen classes the objective names, plus the counterfactual class added
#: with the decision-outcome ledger. Order is the order they are reported in.
CHECK_CLASSES: tuple[str, ...] = (
    "software-supply-chain",
    "syntax-import",
    "unit-integration",
    "data-integrity",
    "point-in-time-no-leakage",
    "pnl-accounting",
    "lifecycle-invariants",
    "guardian-bypass-prevention",
    "replay-determinism",
    "crash-recovery",
    "broker-reconciliation",
    "shadow-live-consistency",
    "decision-outcome-counterfactual",
)


@dataclass
class Result:
    name: str
    status: str
    detail: str
    duration: float = 0.0
    counts: dict = field(default_factory=dict)


def _run(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(SRC)},
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return proc.returncode, (proc.stdout + proc.stderr)


def _tail(text: str, lines: int = 12) -> str:
    parts = [ln for ln in text.strip().splitlines() if ln.strip()]
    return " | ".join(parts[-lines:])[:600]


# --------------------------------------------------------------------------
# Non-pytest checks
# --------------------------------------------------------------------------

def check_software_supply_chain() -> Result:
    """No credential may ever be committed, and the tree must not be tracking
    runtime state or the local env file."""
    problems: list[str] = []
    env_path = os.environ.get("XDG_CONFIG_HOME", "")
    rc, out = _run(["git", "ls-files"])
    tracked = set(out.split())
    for name in (".env", "env", ".env.local", "secrets.json"):
        if name in tracked:
            problems.append(f"{name} is tracked")
    if env_path and any(
        t.endswith("min-agent/env") for t in tracked
    ):
        problems.append("the runtime env file is tracked")

    # A tracked file containing a plausible Alpaca key is a real incident, not a
    # style note. Alpaca keys are 32 chars of [A-Z0-9].
    key_re = re.compile(r"\b(APCA_API[A-Z_]*(?:ID|KEY|SECRET))\s*[=:]\s*['\"]?([A-Z0-9]{16,})")
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT).as_posix()
        if not tracked or rel not in tracked or not path.is_file():
            continue
        if path.suffix in {".jsonl", ".json"} and "runtime" in rel:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in key_re.finditer(text):
            problems.append(f"possible credential in {rel}: {match.group(1)}")
            break

    detail = "; ".join(problems) if problems else (
        f"{len(tracked)} tracked files, no credentials, no tracked env file"
    )
    return Result("software-supply-chain", FAIL if problems else PASS, detail)


def check_syntax_import() -> Result:
    """Every module must byte-compile, and every public name the entry points
    touch must resolve at call time."""
    rc, out = _run([sys.executable, "-m", "compileall", "-q", "src", "tools", "tests"])
    if rc != 0:
        return Result("syntax-import", FAIL, _tail(out))

    modules = sorted(p.stem for p in (SRC / "min_agent").glob("*.py")
                     if p.stem != "__init__")
    probe = (
        "import importlib, sys\n"
        f"mods = {modules!r}\n"
        "for m in mods:\n"
        "    importlib.import_module('min_agent.' + m)\n"
        f"print('imported', len(mods), 'modules')\n"
    )
    rc, out = _run([sys.executable, "-c", probe])
    if rc != 0:
        return Result("syntax-import", FAIL, _tail(out))
    return Result("syntax-import", PASS, _tail(out, 1))


def check_data_integrity() -> Result:
    """Runtime state must parse, and every record must be internally consistent."""
    rc, out = _run([sys.executable, "tools/check_runtime_integrity.py"])
    return Result("data-integrity", PASS if rc == 0 else FAIL, _tail(out))


def check_shadow_live_consistency() -> Result:
    """Live must be unreachable, and probation must be a real gate.

    Shadow trading is *not* implemented - the word appears in docstrings only. The
    check therefore asserts the two things that do exist and reports the gap,
    rather than quietly passing a check that cannot be written yet.
    """
    problems: list[str] = []
    rc, out = _run([sys.executable, "-c", (
        "import os, sys\n"
        "sys.path.insert(0, 'src')\n"
        "os.environ['MIN_AGENT_MODE'] = 'live'\n"
        "from min_agent.config import AgentConfig\n"
        "try:\n"
        "    AgentConfig.from_env()\n"
        "    print('LIVE MODE WAS ACCEPTED')\n"
        "    sys.exit(1)\n"
        "except ValueError:\n"
        "    print('live mode correctly refused')\n"
    )])
    if rc != 0 or "correctly refused" not in out:
        problems.append("live mode is not hard-blocked")

    # Probation must be enforced: a strategy with too few cycles must not trade.
    # Probation lives on StrategySelector, not StrategyEngine. Asserting against
    # the wrong class made this check fail on a healthy system, which is the
    # failure mode that teaches people to ignore a red gate.
    rc, out = _run([sys.executable, "-c", (
        "import sys; sys.path.insert(0, 'src')\n"
        "from min_agent.strategy_engine import StrategySelector\n"
        "import inspect\n"
        "src = inspect.getsource(StrategySelector._needs_probation)\n"
        "assert 'min_probation_cycles' in src, src\n"
        "print('probation gate present')\n"
    )])
    if rc != 0:
        problems.append(f"probation gate missing from strategy selection: {_tail(out)}")

    detail = "; ".join(problems) if problems else (
        "live mode hard-blocked; probation gate enforced; "
        "shadow mode NOT implemented (recorded, not faked)"
    )
    return Result(
        "shadow-live-consistency", FAIL if problems else PASS, detail,
    )


# --------------------------------------------------------------------------
# Pytest-backed classes
# --------------------------------------------------------------------------

def _files_for(classes: tuple[str, ...]) -> list[str]:
    out = []
    for name, assigned in sorted(TEST_CLASS_MAP.items()):
        if any(c in assigned for c in classes):
            out.append(str(TESTS / name))
    return out


def run_pytest_class(cls: str) -> Result:
    files = _files_for((cls,))
    if not files:
        return Result(cls, FAIL, "no test file is assigned to this class")
    started = time.time()
    rc, out = _run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *files])
    duration = time.time() - started
    counts = {}
    m = re.search(r"(\d+) passed", out)
    if m:
        counts["passed"] = int(m.group(1))
    m = re.search(r"(\d+) failed", out)
    if m:
        counts["failed"] = int(m.group(1))
    m = re.search(r"(\d+) skipped", out)
    if m:
        counts["skipped"] = int(m.group(1))
    return Result(cls, PASS if rc == 0 else FAIL, _tail(out), duration, counts)


def check_all_tests_classified() -> Result:
    """Every test file must belong to a class. A new file that does not is the
    system quietly moving out of verification coverage."""
    on_disk = {p.name for p in TESTS.glob("test_*.py")}
    unknown = sorted(on_disk - set(TEST_CLASS_MAP))
    ghost = sorted(set(TEST_CLASS_MAP) - on_disk)
    problems = []
    if unknown:
        problems.append(f"unclassified test files: {unknown}")
    if ghost:
        problems.append(f"mapped but absent: {ghost}")
    detail = "; ".join(problems) if problems else (
        f"all {len(on_disk)} test files are assigned to a check class"
    )
    return Result("test-coverage-map", FAIL if problems else PASS, detail)


def check_known_classes_run() -> Result:
    """Every declared class must actually have tests behind it."""
    empty = [c for c in CHECK_CLASSES if not _files_for((c,))]
    if empty:
        return Result("class-coverage", FAIL, f"classes with no tests: {empty}")
    return Result("class-coverage", PASS, f"all {len(CHECK_CLASSES)} classes have tests")


def check_defect_audit() -> Result:
    """The 15-defect regression audit, kept as its own class because it is the
    record of what was once wrong."""
    rc, out = _run([sys.executable, "tools/audit_defects.py"], timeout=900)
    return Result("defect-regression-audit", PASS if rc == 0 else FAIL, _tail(out, 3))


def check_doctor() -> Result:
    """doctor is the fast-iteration primitive; a non-zero exit must fail the gate."""
    # minictrl is a shell script, not Python. Running it through sys.executable
    # made every run fail with a SyntaxError that had nothing to do with health.
    rc, out = _run(["./minictrl", "doctor"], timeout=900)
    line = next(
        (ln for ln in out.splitlines() if ln.startswith("RESULT")), "no RESULT line"
    )
    return Result("doctor", PASS if rc == 0 else FAIL, line.strip())


# --------------------------------------------------------------------------
# Self-test: prove the gate can fail
# --------------------------------------------------------------------------

def self_test() -> int:
    """A gate that cannot fail is decoration. This asserts the gate does fail, on
    fixtures built to be broken, and that the unclassified-file rule fires.

    It runs against this module's own functions, mutating the map and restoring it
    afterwards, so it is testing the real code path rather than a copy.
    """
    print("verify self-test: the gate must be able to fail\n")
    failures: list[str] = []
    canary = TESTS / "test_zz_self_test_canary.py"
    saved = dict(TEST_CLASS_MAP)

    def restore():
        canary.unlink(missing_ok=True)
        TEST_CLASS_MAP.clear()
        TEST_CLASS_MAP.update(saved)

    try:
        # 1. A class whose tests fail must be reported FAIL.
        canary.write_text(
            "def test_canary_that_must_fail():\n    assert False, 'canary'\n"
        )
        TEST_CLASS_MAP["test_zz_self_test_canary.py"] = ("unit-integration",)
        result = run_pytest_class("unit-integration")
        if result.status != FAIL:
            failures.append(
                f"a failing test did not make its class FAIL (was {result.status})"
            )
        else:
            print("  ok  a failing test makes its class FAIL")

        # 2. A test importing a name that does not exist must be reported FAIL.
        canary.write_text(
            "def test_missing_name():\n    from min_agent import _does_not_exist\n"
        )
        TEST_CLASS_MAP["test_zz_self_test_canary.py"] = ("syntax-import",)
        result = run_pytest_class("syntax-import")
        if result.status != FAIL:
            failures.append("a test importing a nonexistent name did not fail")
        else:
            print("  ok  a test importing a nonexistent name fails")

        # 3. A class with no tests must fail rather than pass vacuously.
        result = run_pytest_class("no-such-class")
        if result.status != FAIL:
            failures.append("a class with no tests did not fail")
        else:
            print("  ok  a class with no tests fails instead of passing vacuously")

        # 4. A test file nobody assigned to a class must fail the coverage map,
        #    so a new test cannot quietly leave the verified surface.
        #    The canary must be *un*assigned first - step 1 put it in the map, and
        #    leaving it there would test nothing. The first version of this
        #    self-test made exactly that mistake and reported a false pass.
        canary.write_text("def test_x():\n    assert True\n")
        TEST_CLASS_MAP.pop("test_zz_self_test_canary.py", None)
        result = check_all_tests_classified()
        if result.status != FAIL:
            failures.append("an unclassified test file did not fail the coverage map")
        else:
            print("  ok  an unclassified test file fails the coverage map")

        # 5. A declared class with no tests behind it must fail. The condition has
        #    to be created deliberately: with the canary popped the system really
        #    is healthy, so a PASS here would be correct and would test nothing.
        stripped = sorted(
            name for name, classes in TEST_CLASS_MAP.items()
            if "pnl-accounting" in classes
        )
        for name in stripped:
            TEST_CLASS_MAP.pop(name)
        result = check_known_classes_run()
        if result.status != FAIL:
            failures.append(
                "a class with every test file removed was still reported as covered"
            )
        else:
            print("  ok  a class with no tests is detected by class-coverage")
        TEST_CLASS_MAP.update({n: c for n, c in saved.items() if n in stripped})

    finally:
        restore()

    if failures:
        print("\nSELF-TEST FAILED - the gate is not trustworthy:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSELF-TEST PASSED: the gate detects failing tests, missing names,")
    print("empty classes and unclassified test files.")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true",
                        help="prove the gate can fail, then exit")
    parser.add_argument("--only", nargs="*", default=None,
                        help="run only these check classes")
    parser.add_argument("--list", action="store_true",
                        help="print the check classes and exit")
    args = parser.parse_args()

    if args.list:
        for cls in CHECK_CLASSES:
            files = [n for n, c in TEST_CLASS_MAP.items() if cls in c]
            print(f"{cls:<34} {len(files):>2} test file(s)")
        return 0
    if args.self_test:
        return self_test()

    print("=" * 78)
    print("verify: every check class, one gate")
    print("=" * 78)

    results: list[Result] = []
    wanted = set(args.only) if args.only else None

    results.append(check_all_tests_classified())
    results.append(check_known_classes_run())
    results.append(check_software_supply_chain())
    results.append(check_syntax_import())
    results.append(check_data_integrity())
    results.append(check_shadow_live_consistency())

    for cls in CHECK_CLASSES:
        if wanted and cls not in wanted:
            continue
        if cls in {"software-supply-chain", "syntax-import", "data-integrity",
                   "shadow-live-consistency"}:
            continue  # already run above as bespoke checks
        results.append(run_pytest_class(cls))

    if not wanted:
        results.append(check_defect_audit())
        results.append(check_doctor())

    width = max(len(r.name) for r in results)
    print()
    executions = 0
    for r in results:
        executions += r.counts.get("passed", 0)
        print(f"[{r.status:^4}] {r.name:<{width}}  {r.detail[:150]}")
    print()
    failed = [r for r in results if r.status == FAIL]
    # Labelled "executions", not "tests". A file mapped to two classes is run
    # twice, so summing the per-class counts would report a number larger than
    # the number of tests that exist - an inflated metric in a verification
    # report is exactly the kind of thing that stops being read.
    print(
        f"classes: {len(results)}   "
        f"passed: {len(results) - len(failed)}   failed: {len(failed)}   "
        f"test executions: {executions} across {len(TEST_CLASS_MAP)} test files"
    )
    if failed:
        print("\nFAIL - the gate is red:")
        for r in failed:
            print(f"  {r.name}: {r.detail[:400]}")
        return 1
    print("\nOK - all check classes pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
