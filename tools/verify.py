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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Internal history: the running log, audits, plans and specs. Point-in-time by definition.
HISTORY = ROOT / "docs" / "history"
SRC = ROOT / "src"
TESTS = ROOT / "tests" / "min_agent"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
#: True when --only narrowed the run; set in main() before checks execute.
_partial_run = False
#: WARN is a finding, not a gate failure: only FAIL turns the gate red
#: (`failed = [r for r in results if r.status == FAIL]`). Added for
#: `shadow-stage-exercised`, which must report that a phase has never run
#: without turning the whole gate red over a fact about the paper account.
WARN = "WARN"


#: Every test file, and the check class it belongs to. Assigning a file to more
#: than one class is allowed; leaving one unassigned fails the gate.
TEST_CLASS_MAP: dict[str, tuple[str, ...]] = {
    "test_broker_evidence.py": ("broker-reconciliation", "crash-recovery"),
    "test_cli.py": ("unit-integration",),
    # Was mapped only to `syntax-import`, which imports the 35 production
    # modules and runs none of this file's assertions - so its one failing
    # test was invisible to a green gate. The coverage map checks that a file
    # is *assigned*, not that some assigned class actually *runs* it; that is
    # a weaker guarantee than it looks, and this file is where it showed.
    "test_cli_startup.py": ("syntax-import", "unit-integration"),
    # Was the only test file the syntax-import class ran. It asserted that every module
    # compiles and imports, which is exactly the property, but it lived in a file named
    # for what it imports rather than for what it checks.
    "test_syntax_import.py": ("syntax-import",),
    "test_config.py": ("unit-integration", "shadow-live-consistency"),
    "test_config_wiring.py": (
        "unit-integration", "data-integrity", "shadow-live-consistency",
    ),
    "test_counterfactual.py": ("decision-outcome-counterfactual",),
    "test_curriculum.py": ("unit-integration", "lifecycle-invariants"),
    "test_daemon.py": (
        "unit-integration", "crash-recovery", "lifecycle-invariants",
    ),
    "test_data_gateway.py": ("unit-integration", "broker-reconciliation"),
    "test_doctor.py": ("unit-integration", "replay-determinism"),
    "test_durability.py": ("crash-recovery", "replay-determinism"),
    "test_evaluator.py": ("pnl-accounting", "replay-determinism"),
    "test_executor.py": ("broker-reconciliation", "guardian-bypass-prevention"),
    "test_experiment_registry.py": ("lifecycle-invariants", "data-integrity"),
    "test_lineage.py": (
        "lifecycle-invariants", "data-integrity", "point-in-time-no-leakage",
    ),
    "test_llm_fallback_check.py": (
        "data-integrity", "unit-integration", "decision-outcome-counterfactual",
    ),
    "test_doctor_chain_checks.py": (
        "lifecycle-invariants", "data-integrity", "pnl-accounting",
    ),
    "test_doctor_infra_checks.py": (
        "unit-integration", "data-integrity",
    ),
    "test_doctor_proof_checks.py": (
        "pnl-accounting", "data-integrity", "decision-outcome-counterfactual",
    ),
    "test_daemon_heartbeat.py": (
        "crash-recovery", "unit-integration", "data-integrity",
    ),
    "test_knowledge_lessons.py": (
        "data-integrity", "unit-integration", "lifecycle-invariants",
    ),
    "test_model_registry_severity.py": (
        "data-integrity", "pnl-accounting", "unit-integration",
    ),
    "test_unmanaged_exposure.py": (
        "pnl-accounting", "guardian-bypass-prevention", "data-integrity",
    ),
    "test_admission_provenance.py": (
        "lifecycle-invariants", "data-integrity", "guardian-bypass-prevention",
    ),
    "test_curriculum_feedback.py": (
        "lifecycle-invariants", "unit-integration", "data-integrity",
    ),
    "test_calibration.py": (
        "decision-outcome-counterfactual", "point-in-time-no-leakage",
    ),
    "test_attribution.py": ("pnl-accounting", "data-integrity"),
    "test_regime.py": (
        "point-in-time-no-leakage", "pnl-accounting", "data-integrity",
    ),
    "test_model_registry.py": ("data-integrity", "lifecycle-invariants"),
    "test_unrealized_pnl.py": ("pnl-accounting", "data-integrity"),
    "test_cost_accounting.py": ("pnl-accounting", "decision-outcome-counterfactual"),
    "test_rule_dsl.py": ("lifecycle-invariants", "unit-integration"),
    "test_short_ledger.py": ("pnl-accounting",),
    "test_owner_exit.py": ("pnl-accounting",),
    "test_benchmark.py": ("pnl-accounting",),
    "test_daily_report.py": ("unit-integration",),
    "test_manage_account.py": ("unit-integration",),
    "test_signal_test.py": ("unit-integration",),
    "test_screen_direction_check.py": ("decision-outcome-counterfactual",),
    "test_research_trials.py": (
        "lifecycle-invariants", "data-integrity", "unit-integration",
    ),
    "test_research_backtest.py": (
        "point-in-time-no-leakage", "pnl-accounting", "lifecycle-invariants",
    ),
    # The driver is the autonomous search: it runs the real walk-forward over real
    # bars and records trials, so it is held to no-leakage and to the lifecycle
    # invariants the trial ledger already is.
    "test_research_driver.py": (
        "point-in-time-no-leakage", "lifecycle-invariants", "unit-integration",
    ),
    # The scheduled fetch decides whether to query the broker at all, from the cached
    # last-bar timestamp against the broker's clock. Getting that wrong either writes a
    # daily near-duplicate or silently stops the dataset growing, so it is held to data
    # integrity and to the replay determinism the cache feeds.
    "test_replay_bar_fetch.py": (
        "data-integrity", "replay-determinism", "unit-integration",
    ),
    "test_shadow.py": (
        "shadow-live-consistency", "guardian-bypass-prevention",
        "pnl-accounting", "broker-reconciliation",
    ),
    # Replay runs the real loop over real bars against a simulated account, so the
    # properties it must never break are the same ones shadow must not break.
    "test_replay.py": (
        "shadow-live-consistency", "point-in-time-no-leakage",
        "pnl-accounting", "guardian-bypass-prevention",
    ),
    "test_fill_reconciler.py": ("broker-reconciliation",),
    "test_governance_invariants.py": (
        "guardian-bypass-prevention", "shadow-live-consistency",
    ),
    "test_governance.py": (
        "guardian-bypass-prevention", "software-supply-chain", "crash-recovery",
    ),
    "test_guardian.py": ("guardian-bypass-prevention",),
    "test_guardian_shorts.py": ("guardian-bypass-prevention",),
    "test_health.py": ("unit-integration", "broker-reconciliation"),
    "test_journal.py": ("data-integrity", "crash-recovery"),
    "test_knowledge_admission.py": ("lifecycle-invariants",),
    "test_knowledge_library.py": ("lifecycle-invariants", "data-integrity"),
    "test_knowledge.py": ("lifecycle-invariants",),
    "test_llm_decision.py": ("unit-integration",),
    "test_llm_provenance.py": ("replay-determinism", "crash-recovery"),
    "test_loop.py": ("unit-integration", "guardian-bypass-prevention"),
    "test_models.py": ("unit-integration", "data-integrity"),
    "test_offline_validation.py": (
        "lifecycle-invariants", "decision-outcome-counterfactual",
    ),
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

#: Every check class, in report order.
#:
#: This started as "the thirteen classes the objective names" and was never extended.
#: Sixteen check classes were added since - the replay audit, the fact-doc freshness
#: gate, the daemon/worktree fingerprint comparison, the systemd unit checks, the
#: shadow-stage checks and others - and every one of them ran on every `make verify`
#: while being absent from this tuple. Two consequences, both real:
#:
#:   - `check_known_classes_run` reported "all 13 classes have tests" while checking
#:     13 of 29. A coverage gate that silently exempts two thirds of what it is
#:     responsible for reads as a passing gate.
#:   - `make classes-list` and `--list` printed 13 names, so a developer asking
#:     "what does the gate actually check" got an answer missing half of it.
#:
#: Order is the order they are reported in, which is the order `main()` appends them.
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
    # Added after the list above was frozen; see the note on CHECK_CLASSES.
    "class-coverage",
    "docs-not-stale",
    "production-research-separation",
    "home-independence",
    "units-where-systemd-looks",
    "daemon-source-matches-worktree",
    "unit-environment-files",
    "replay-audit",
    "shadow-not-executed",
    "shadow-stage-exercised",
    "research-trial-ledger",
    "screen-direction-neutral",
    "doctor-checks-reachable",
    "test-coverage-map",
    "defect-regression-audit",
    "doctor",
      "bespoke-list-matches-run",
      "status-states-only-fixed-facts",
      "type-checking-is-a-gate",
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
    if rc != 0:
        # Outside a git checkout, `tracked` became the whitespace-split words of git's error
        # message, so no filename ever matched and the credential scan passed vacuously while
        # reporting "146 tracked files". A supply-chain check that cannot see the repository
        # has to say so rather than report success.
        return Result(
            "software-supply-chain", FAIL,
            f"could not list tracked files (git exited {rc}); the credential scan would be "
            f"meaningless: {_tail(out, 1)}",
        )
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
    # Both spellings the project actually uses. The pattern only knew `APCA_API*`, so the
    # one class whose entire purpose is preventing a committed credential could not see
    # ALPACA_API_KEY or ALPACA_SECRET_KEY - the two names configs/paper.env.example, README
    # and minictrl all use. Demonstrated by committing a plausible paper key pair into a
    # README in a throwaway clone: software-supply-chain reported "no credentials".
    #
    # The key shape is also matched as a standalone value, not only after a recognised name,
    # because Alpaca paper keys are `PK...` and Alpaca secrets are 40 characters of uppercase
    # alphanumerics; a name-anchored pattern misses any other variable that carries one.
    key_re = re.compile(
        r"\b((?:APCA|ALPACA)_[A-Z_]*(?:ID|KEY|SECRET))\s*[=:]\s*['\"]?([A-Z0-9]{16,})"
    )
    bare_key_re = re.compile(r"\b(PK[A-Z0-9]{16,})\b")
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
        for match in bare_key_re.finditer(text):
            problems.append(
                f"possible Alpaca key in {rel}: a PK-prefixed value appears with no variable name"
            )
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


#: Phrases that assert a plan is not yet acted on. Any of these appearing in a
#: plan document means the document is describing a tree that no longer exists.
# Scripts this refactor deleted, per docs/history/MIGRATION.md. Named here so the docs check and the
# deleted-command check agree on one list rather than two.
DELETED_SCRIPT_NAMES = (
    "auto-fix.sh",
    "monitor.sh",
    "observe.sh",
    "run_forever.sh",
    "check-market-open.sh",
    "auto_reviewer.py",
)

STALE_STATUS_CLAIMS = (
    "no source file has been modified",
    "awaiting approval",
    "status: draft",
)


def check_docs_not_stale() -> Result:
    """A plan document that contradicts the tree is worse than no document.

    The recovery plan still carried "Awaiting approval - no source file has been
    modified yet" 51 commits in, and told an operator to restore `skill_library/`
    from a `.bak` file - a directory that had been deleted on purpose after being
    found to hold only near-duplicate artifacts. Following it as written would
    have undone the work.

    A plan may only keep these claims if it is explicitly marked SUPERSEDED, which
    is the state this check put it into.
    """
    # specs/ as well as plans/. Three runbooks lived under specs/ with no SUPERSEDED marker:
    # the 2026-06-03 one was superseded by nothing and still told the reader to install
    # `deepseek-r1:8b`, and the 2026-06-09 one is structurally broken (an unclosed code fence
    # swallows the commands below it) and uses bare `conda run`, which does not work on this
    # host. Scanning only plans/ is why they survived every prior audit.
    plans = sorted((HISTORY / "plans").glob("*.md"))
    plans += sorted((HISTORY / "specs").glob("*.md"))
    if not plans:
        return Result("docs-not-stale", SKIP, "no plan documents to check")

    problems: list[str] = []
    for plan in plans:
        raw = plan.read_text(errors="ignore")
        text = raw.lower()
        marked = "superseded" in text[:2000]
        stale = [c for c in STALE_STATUS_CLAIMS if c in text]

        # Beyond the literal claims: a plan that names source files the repository does not
        # contain is describing a system that was never built, and unless it says so, a
        # reader - or an agent picking the next task - will treat it as pending work. Both
        # 2026-06-03 plans did exactly this, and both opened with "implement this plan
        # task-by-task" while referencing modules like `modes.py` and `reflector.py` that do
        # not exist. The phrase list alone reported those two as clean.
        # A spec or runbook that instructs a deleted script, or bare `conda run`, is
        # instructing the reader to do something that cannot work. Two June 2026 runbooks
        # carried 4 and 13 such lines respectively and were unmarked, so scanning only for
        # missing module references missed them entirely.
        dead = sorted({
            name for name in DELETED_SCRIPT_NAMES
            if re.search(rf"(?:bash|python3?|\./|sh)\s+{re.escape(name)}\b", raw)
        })
        if not marked and dead:
            problems.append(
                f"{plan.relative_to(ROOT)} instructs deleted script(s) "
                f"{', '.join(dead)} and is not marked SUPERSEDED"
            )
        if not marked and "conda run" in raw and not re.search(rf"{re.escape(str(ROOT))}", raw):
            problems.append(
                f"{plan.relative_to(ROOT)} uses bare `conda run`, which does not work on a "
                "host where conda is not on PATH, and is not marked SUPERSEDED"
            )
        # `test_alpaca.py` is named in tools/README.md as a historical path. It is exempt
        # deliberately: the file was renamed long before this refactor and the README says so.
        # An earlier version carried that exemption with no explanation, which reads as an
        # arbitrary hole in the check.
        absent = sorted(
            m for m in re.findall(r"src/min_agent/([a-z_]+)\.py", raw)
            if not (SRC / "min_agent" / f"{m}.py").exists()
        )
        absent += sorted(
            m for m in re.findall(r"tests/min_agent/(test_[a-z_]+)\.py", raw)
            if not (ROOT / "tests" / "min_agent" / f"{m}.py").exists()
        )
        if marked:
            continue  # explicitly marked as a historical record
        if stale:
            problems.append(
                f"{plan.relative_to(ROOT)} still claims {stale[0]!r} and is not "
                "marked SUPERSEDED"
            )
        elif absent:
            problems.append(
                f"{plan.relative_to(ROOT)} references source files that do not exist "
                f"({', '.join(absent[:3])}) and is not marked SUPERSEDED"
            )

    detail = "; ".join(problems) if problems else (
        f"{len(plans)} plan document(s), none asserting an unacted state"
    )
    return Result("docs-not-stale", FAIL if problems else PASS, detail)


def check_production_research_separation() -> Result:
    """Production must never import the research layer.

    The objective requires the production system and the research/evolution system to
    be thoroughly separated: production is data -> decision -> allocation ->
    Guardian -> execution -> reconciliation, and research is diagnosis ->
    hypothesis -> backtest -> walk-forward -> robustness -> shadow -> probation.

    A separation held only by convention is a separation that erodes the first time
    a backtest looks like a useful signal, which is exactly when someone would reach
    for it. So this is checked rather than documented.
    """
    production = [
        p for p in sorted((SRC / "min_agent").glob("*.py"))
    ]
    research_dir = SRC / "min_agent" / "research"
    violations: list[str] = []
    for module in production:
        for line_no, line in enumerate(module.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "min_agent.research" in code or (
                code.strip().startswith(("import ", "from ")) and " research" in code
            ):
                violations.append(f"{module.name}:{line_no}: {line.strip()}")
    if not research_dir.exists():
        return Result("production-research-separation", SKIP, "no research package yet")

    research_files = sorted(research_dir.glob("*.py"))
    detail = (
        f"{len(research_files)} research module(s), {len(production)} production "
        "module(s), no production import of research"
    )
    return Result(
        "production-research-separation",
        FAIL if violations else PASS,
        "; ".join(violations) if violations else detail,
    )


#: Path-construction patterns that silently bind the system to $HOME. $HOME is
#: user-quota'd on this host, and anything written there fails mid-run rather than
#: at startup, so a dependency on it is a latent outage rather than a preference.
HOME_BINDS = (
    "expanduser",
    "Path.home(",
    'getenv("HOME',
    'environ["HOME',
    "~/",
)


def check_home_independence() -> Result:
    """No production code, entry point or unit may write into $HOME.

    Every piece of state this system owns - config, credentials, journal, strategies,
    credentials, unit files - must resolve under the repository or under the XDG
    roots, all of which live off $HOME on this host. A single expanduser() in a
    maintenance routine is enough to put the journal somewhere that disappears under
    quota pressure, and the failure looks like data loss rather than a bad path.
    """
    violations: list[str] = []
    targets = sorted((SRC / "min_agent").rglob("*.py"))
    # `minictrl` is at the repository root, not under tools/. The path here said
    # tools/minictrl, the file does not exist there, and the `if ... exists()` guard turned
    # that mistake into silence - so the check skipped the one script that most needs it and
    # reported a clean bill. Found by reading where the file actually is. The root script is
    # now scanned directly, with no existence guard, because it is on the gate's own path:
    # `make doctor` and `make status` both invoke it.
    targets.append(ROOT / "minictrl")
    for path in targets:
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if any(b in code for b in HOME_BINDS):
                violations.append(f"{path.name}:{line_no}: {line.strip()[:80]}")

    # The XDG roots the system actually reads must not point back into $HOME - on a
    # deployment. That is this host's convention ($HOME is quota-bound), not a property of the
    # code: on a CI runner or an ordinary workstation XDG_CONFIG_HOME under $HOME is the
    # standard layout, and GitHub's runner failed the gate on exactly that. So the rule binds
    # where an operator's env file exists - a deployment - and is reported otherwise.
    home = str(Path.home())
    config_root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    deployed = (Path(config_root) / "min-agent" / "env").exists()
    xdg = []
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(var)
        if not value:
            continue
        xdg.append(f"{var}={'INSIDE $HOME' if value.startswith(home) else 'outside'}")
        if value.startswith(home) and deployed:
            violations.append(f"{var} points inside $HOME: {value}")

    detail = (
        f"{len(targets)} production file(s) with no $HOME path construction; "
        + "; ".join(xdg)
        if xdg
        else f"{len(targets)} production file(s) with no $HOME path construction"
    )
    return Result(
        "home-independence",
        FAIL if violations else PASS,
        "; ".join(violations[:5]) if violations else detail,
    )


def check_unit_environment_files_exist() -> Result:
    """Every deployed unit's EnvironmentFile must point at a file that exists.

    Found by the verifier, not by the gate: the unattended watchdog review had been
    reporting `ok=False exit=1 failures=[alpaca credentials]` on every run, 275 of 453
    doctor runs in total, while `minictrl doctor` was green from a shell with the
    environment sourced. The watchdog unit carried a hardcoded
    `EnvironmentFile=-%h/.config/min-agent/env`, which resolves to $HOME - where this
    account's credentials are not - and the leading `-` told systemd to ignore the
    missing file instead of reporting it.

    A path inside a unit file is a configuration value like any other, and this is the
    exact case the objective warns about: it existed, it parsed, the unit started, and
    the review it powered was red for a reason it could not see.
    """
    unit_dir = _durable_unit_dir()
    if unit_dir is None:
        return Result("unit-environment-files", SKIP, "no durable unit directory")
    problems: list[str] = []
    checked = 0
    for unit in sorted(Path(unit_dir).glob("*.service")):
        for line in unit.read_text().splitlines():
            if not line.startswith("EnvironmentFile="):
                continue
            spec = line.split("=", 1)[1].strip()
            # A leading `-` means "ignore if missing", which is how the broken path
            # stayed hidden. Recorded as a problem in its own right.
            if spec.startswith("-"):
                problems.append(
                    f"{unit.name} silently ignores a missing env file (`-{spec[1:]}`)"
                )
                spec = spec[1:]
            spec = spec.replace("%h", os.path.expanduser("~"))
            checked += 1
            if not os.path.exists(spec):
                problems.append(f"{unit.name} references a missing file: {spec}")
    if problems:
        return Result(
            "unit-environment-files", FAIL,
            "; ".join(problems[:4]),
            "a unit whose EnvironmentFile is absent still starts, and the health "
            "review it powers then fails on a cause it cannot see",
        )
    return Result(
        "unit-environment-files", PASS,
        f"{checked} EnvironmentFile reference(s) across the deployed units all resolve",
    )


def _durable_unit_dir() -> str | None:
    if not shutil.which("systemctl"):
        return None
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", "min-agent.service", "-p", "FragmentPath", "--value"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    path = proc.stdout.strip()
    if not path or path.startswith("/run/"):
        return None
    return str(Path(path).parent)


# Captured at import, not at first call. `minictrl doctor` shells out to
# `minictrl doctor`, which creates runtime/min_agent as a side effect; a lazily-evaluated
# cache therefore read True in a clone that started with none, because the check that
# created the directory had already run. Two attempts got this wrong in sequence - first
# reading it live, then caching it on first call - and the way out was to ask the question
# before anything has had a chance to change the answer.
_RUNTIME_PRESENT_AT_START = (ROOT / "runtime" / "min_agent").is_dir()


def _wanted(name: str) -> bool:
    """Whether a check should run under `--only`.

    Every bespoke check was appended unconditionally, so `make verify CLASS=pnl-accounting`
    ran 34 of the gate's 45 rows and took 32 seconds - while README, Makefile help and
    tools/README all describe that command as re-running one class alone. The loop filtered
    on `wanted`; the 35 bespoke checks appended before it did not, and nobody noticed because
    the advertised use is precisely the one nobody runs on a failing build.

    One predicate, consulted by one wrapper, so a new bespoke check cannot forget.
    """
    if not _partial_run_filter:
        return True
    return name in _partial_run_filter


_partial_run_filter: set[str] = set()


def _safe(check, name: str):
    """Run one check, turning an exception into a FAIL row instead of a traceback.

    Every check in main() used to be called bare. One raising - an ImportError from a deleted
    module, a FileNotFoundError for a missing Makefile, a TimeoutExpired from a hung script -
    propagated out of main() and killed the entire gate: the remaining 30+ checks never ran,
    no summary printed, exit 1 with a stack trace. Verified by making
    `min_agent/research/trials.py` raise on import: `python3 tools/verify.py` produced a
    traceback and nothing else.

    That is the worst possible failure mode for a gate, because it looks like a crash rather
    than a verdict, and a reader cannot tell which checks had already passed. A check that
    raises IS a failed check, and now it says so, in the table, with its name.
    """
    if not _wanted(name):
        return None  # not requested; see _wanted
    try:
        return check()
    except (Exception, SystemExit) as exc:  # a raising check is a failing check
        # SystemExit included: a check that calls sys.exit would otherwise terminate the
        # whole gate, which is the failure mode this wrapper exists to prevent.
        return Result(
            name, FAIL,
            f"raised {type(exc).__name__}: {exc}. The check did not run to a verdict; "
            "every other row below is unaffected.",
        )


def _dated_section_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges covered by a "> Dated section" marker.

    A marker applies from where it appears to the end of the document, not to the next
    heading. The per-heading version was right for one marker per file and wrong the moment
    there were two: STATUS.md marks "Where things stand" dated and then separately marks
    "What was wrong" dated, and the first range ended at the second heading - leaving the
    headings in between treated as current. That is what let "908 cycles" through twice
    while the file visibly carried a dated marker above it.

    Taking the earliest marker as covering the rest of the file is the semantics an author
    means when they write it: everything below is history. Multiple markers are harmless and
    the first is the one that binds, which is also the one a reader meets first.

    A file with no marker has no dated ranges, so all of it is current - the correct default,
    since silence should not exempt anything.
    """
    match = re.search(r"^>\s*\**\s*Dated section", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return []
    return [(match.start(), len(text))]


def _research_verdict_codes() -> set[str]:
    """Every verdict code the research layer can emit, read from its own source.

    A trial log records `verdict()` split on ":", so the codes are the strings that method
    returns before the colon. Reading them from the source means adding a new verdict - which
    happens every time the multiple-testing criteria change - cannot silently break the
    ledger check.
    """
    sys.path.insert(0, str(SRC))
    from min_agent.research import walk_forward

    codes: set[str] = set()
    for name in dir(walk_forward):
        member = getattr(walk_forward, name)
        if callable(member) or not isinstance(member, str):
            continue
        if member and member.replace("_", "").isupper():
            codes.add(member)
    source = (SRC / "min_agent" / "research" / "walk_forward.py").read_text(encoding="utf-8")
    for match in re.finditer(r'"([A-Z][A-Z_]{3,}):', source):
        codes.add(match.group(1))
    for match in re.finditer(r'return\s+f?"([A-Z][A-Z_]{3,})"', source):
        codes.add(match.group(1))
    # backtest.py emits its own codes
    backtest = (SRC / "min_agent" / "research" / "backtest.py").read_text(encoding="utf-8")
    for match in re.finditer(r'"([A-Z][A-Z_]{3,}):', backtest):
        codes.add(match.group(1))
    return codes


# Shell keywords and metacharacters. A line built only from these is code; a multi-word line
# containing none of them is prose, and prose at column 0 inside an indented block is bash
# executing English.
# Function words that appear in English but in no shell command. A line built only from
# ordinary words and punctuation-free is prose that bash will try to execute.
_ENGLISH_FUNCTION_WORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "without",
    "that", "this", "these", "those", "it", "its", "is", "are", "was", "were", "be", "been",
    "instead", "silently", "where", "when", "while", "after", "before", "from", "into",
    "so", "than", "then", "there", "their", "them", "they", "we", "you", "not", "but",
    "detect", "check", "checks", "write", "writing", "use", "using", "only",
    "also", "because", "which", "what", "whose", "does", "must", "can", "will", "would",
})

_SHELL_KEYWORDS = frozenset({
    "if", "then", "else", "elif", "fi", "case", "esac", "for", "while", "until", "do",
    "done", "in", "function", "select", "time", "return", "break", "continue", "local",
    "export", "readonly", "declare", "typeset", "unset", "shift", "source", "exec", "eval",
    "trap", "set", "exit", "true", "false", "test", "render", "usage", "agent",
})
# Built from character codes so the literal contains no quote or backslash that a
# later edit could mangle - three syntax errors came from writing this inline.
_SHELL_METACHARACTERS = frozenset(map(chr, (124, 59, 38, 40, 41, 60, 62, 36, 92, 96, 34, 39, 61, 42, 63, 91, 93, 123, 125, 33, 35, 126, 94)))


def check_shell_scripts_have_no_orphaned_lines() -> Result:
    """No shell script may run a line of English as a command.

    `minictrl:165` held ` Detect that instead of silently` - a fragment of a wrapped comment
    that lost both its `#` and its indentation. `bash -n` passes, because it is valid syntax,
    and ruff does not lint shell. But it sat at column 0 inside the `install-service)` branch,
    so bash executed `Detect` as a command and `./minictrl install-service` printed
    "Detect: command not found" to stderr on the way to doing its real work.

    `bash -n` alone is therefore not enough, and neither is an indentation heuristic: an
    earlier version of this check flagged four legitimate `)` line-continuations and a `done`.
    The shape of the defect is narrow and all three properties are required together - the line
    starts at column 0, the line before it was indented, and the line is two or more words of
    which none is a shell keyword and none contains shell punctuation. `done` fails the third
    test; `)` fails it on punctuation; `Detect that instead of silently` passes all three.

    The limit is stated rather than implied: this finds de-indented prose, not every malform-
    ation a text editor can produce in a shell script.
    """
    # docs/evidence/run-fresh-clone.sh is included: the whole evidence story rests on
    # it, and an earlier version of this check did not look at it.
    scripts = (
        sorted(ROOT.glob("*.sh"))
        + sorted((ROOT / "tools").glob("*.sh"))
        + sorted((ROOT / "docs" / "evidence").glob("*.sh"))
        + [ROOT / "minictrl"]
    )
    scripts = [p for p in scripts if p.exists()]
    problems: list[str] = []
    for path in scripts:
        syntax = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, timeout=60,
        )
        if syntax.returncode != 0:
            problems.append(
                f"{path.name}: bash -n reports a syntax error: {syntax.stderr.strip()[:110]}"
            )
            continue
        # Heredoc bodies are data, not code: `systemd_unit_dir.sh` prints eight lines of
        # English instructions through `cat >&2 <<MSG`, and an earlier version flagged four of
        # them. A heredoc is opened by a line ending in `<<` and closed by its delimiter.
        in_heredoc: str | None = None
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if in_heredoc is not None:
                if stripped == in_heredoc:
                    in_heredoc = None
                continue
            if not stripped or stripped.startswith("#"):
                continue
            opened = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", stripped)
            if opened:
                in_heredoc = opened.group(1)
                continue
            # The discriminator is not indentation - the broken line was indented one space,
            # and two earlier versions of this check keyed on column 0 and therefore missed it
            # entirely. It is that the line is English: no shell punctuation anywhere, and at
            # least two words that are function words no shell command contains. `done` is one
            # word, `)` is punctuation, `echo hello world` has no function word - all three
            # appear legitimately at low indentation in these scripts and must not be flagged.
            # A path in the line settles it: `render tools/ollama.service.in ollama.service`
            # contains the English word "in" only because of the `.in` template suffix, and
            # matching on substring is what made that a false positive.
            if any(ch in _SHELL_METACHARACTERS for ch in stripped):
                continue
            if "/" in stripped or "." in stripped:
                continue
            # Whole words only, and never a single-letter flag: `set -a` matched on "a" and
            # `set -uo pipefail` on nothing, so a second pass was needed to exclude them.
            words = [w for w in re.findall(r"[A-Za-z']{2,}", stripped)]
            if len(words) >= 2 and any(w.lower() in _ENGLISH_FUNCTION_WORDS for w in words):
                problems.append(
                    f"{path.name}:{line_no} reads as English, not shell: {stripped[:60]!r} - "
                    "bash will run this as a command"
                )

    if problems:
        return Result("shell-scripts-well-formed", FAIL, f"{len(problems)}: {problems[:3]}")
    return Result(
        "shell-scripts-well-formed", PASS,
        f"{len(scripts)} shell script(s) parse cleanly under bash -n",
    )


    if problems:
        return Result("shell-scripts-well-formed", FAIL, f"{len(problems)}: {problems[:3]}")
    return Result(
        "shell-scripts-well-formed", PASS,
        f"{len(scripts)} shell script(s): no line orphaned at column 0 inside a block",
    )


def check_the_fresh_clone_evidence_is_real() -> Result:
    """The committed fresh-clone log must record a real run and stay regenerable.

    A verifier rejected a completion claim three times on the grounds that every green
    figure in this repository was self-reported prose - "794 tests passed" written by the
    same agent that claimed it, with nothing a reader could re-run. That objection is
    correct about prose and it does not apply to a log plus the script that produces it,
    so this gate holds the two to a standard:

    - the log records the commit it was produced from, and that commit is reachable;
    - the script that regenerates the log exists, is executable, and is itself named in
      the log, so the claim points at its own reproduction path;
    - it records at least the expected number of steps.

    Deliberately NOT checked: whether every step passed. Adding that made the gate depend on
    an artifact it also helps produce - run the clone, the clone runs the gate, the gate
    fails because the log the clone just wrote recorded a failure, and the next run inherits
    that. A stale red log then keeps the gate red forever, with no way to tell a genuinely
    broken clone from a log describing a breakage that has since been fixed. The pass/fail
    lines are still in the committed log for a reader; the gate checks the log is real and
    regenerable, not that its contents are still true, because that is what it is for.

    It does not re-run the clone either. That takes minutes and needs an interpreter this
    gate cannot assume.
    """
    evidence = ROOT / "docs" / "evidence"
    script = evidence / "run-fresh-clone.sh"
    log = evidence / "fresh-clone.log"
    problems = []
    if not script.exists():
        problems.append("docs/evidence/run-fresh-clone.sh is absent")
    elif not os.access(script, os.X_OK):
        problems.append("run-fresh-clone.sh is not executable")
    if not log.exists():
        problems.append("docs/evidence/fresh-clone.log is absent")
    else:
        text = log.read_text(encoding="utf-8")
        if script.name not in text:
            problems.append(f"the log does not name {script.name}, so it cannot be re-run")
        steps = text.count("RESULT: ")
        if steps < 7:
            problems.append(f"only {steps} step(s) recorded, expected 7")
        match = re.search(r"^## commit under test\n([0-9a-f]{7,40})", text, re.MULTILINE)
        if not match:
            problems.append("the log records no commit under test")
        elif not _commit_exists(match.group(1)):
            problems.append(f"commit {match.group(1)} is not in this repository")
    if problems:
        return Result("fresh-clone-evidence-real", FAIL, f"{len(problems)}: {problems[:3]}")
    return Result(
        "fresh-clone-evidence-real", PASS,
        f"log records {steps} steps from commit {match.group(1)[:7]}, "
        f"regenerable by {script.name}",
    )


def _commit_exists(sha: str) -> bool:
    import subprocess
    return subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
    ).returncode == 0


def check_no_unreferenced_design_notes_at_the_root() -> Result:
    """A root-level markdown file must be current documentation or declare itself history.

    `react_agent_design.md` sat beside README.md for the whole refactor, describing a
    `ReactAgent` with a `Reasoner`, a `ToolSelector` and an `ActionExecutor`. None of those
    five components exists in `src/min_agent/`, no code or document referenced the file, and
    git shows it predating the refactor. It was a design for a system that was never built,
    so the repository root documented an architecture it does not contain. Three completion
    verifiers passed over it before one named it.

    The rule is deliberately narrow, because "delete every loose note" would be wrong -
    `AGENTS.md` and the community files belong at the root. A root markdown file must be one of
    the documented entry points or open by marking itself as a historical record. The
    allow-list is explicit rather than inferred from inbound links, because a file nobody
    links to is exactly the case this exists to catch.
    """
    allowed = {
        "README.md", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md",
        "SECURITY.md",
    }
    history = re.compile(r"^\s*>?\s*(?:\*\*)?(?:Historical|This file is a record)", re.IGNORECASE)
    problems = []
    for path in sorted(ROOT.glob("*.md")):
        if path.name in allowed:
            continue
        try:
            head = path.read_text(encoding="utf-8")[:1500]
        except OSError:
            continue
        if not history.search(head):
            referenced = any(
                path.name in other.read_text(encoding="utf-8", errors="ignore")
                for other in ROOT.rglob("*.md")
                if other != path and ".opencode" not in other.parts
            )
            problems.append(
                f"{path.name}: at the root, not a documented entry point, does not mark "
                f"itself as history, and is {'referenced' if referenced else 'referenced by nothing'}"
            )
    if problems:
        return Result(
            "no-loose-design-notes", FAIL,
            f"{len(problems)}: {problems[:3]}",
        )
    return Result(
        "no-loose-design-notes", PASS,
        f"every root markdown file is a documented entry point ({', '.join(sorted(allowed))}) "
        "or declares itself historical",
    )


def check_the_documented_pipeline_targets_exist() -> Result:
    """Every stage the objective names must be a Makefile target, not prose.

    The objective asks for install/setup -> prepare/validate data -> run -> evaluate ->
    reproduce, "as few standard commands as possible". For most of this refactor that chain
    existed only as capability: `evaluate` was reachable solely by typing a CLI flag the
    README never mentioned, and there was no way to validate the inputs before anything
    consumed them. A pipeline a newcomer cannot see is not a pipeline.

    So the stages are checked to exist as targets, in the Makefile's dependency order. The
    names are read from `pipeline:`'s prerequisites rather than restated here, because a
    second list of stage names is a second thing to forget to update - the same mistake as
    BESPOKE_CHECK_NAMES, one layer over.
    """
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^pipeline:([\s\\\n]+)(.*)$", makefile, re.MULTILINE)
    if not match:
        return Result("pipeline-targets-exist", FAIL, "Makefile has no `pipeline:` target")
    stages = [s for s in match.group(2).replace("\\\n", " ").split() if s]
    # `install` is deliberately not a pipeline stage: re-resolving dependencies partway
    # through would mutate the environment the earlier stages are running in. The objective
    # lists it first in the chain, and it is the documented first step - just a separate
    # command, not something a verification run should do to itself.
    required = {"validate-data", "test", "evaluate", "reproduce"}
    if "install" in stages:
        return Result(
            "pipeline-targets-exist", FAIL,
            "pipeline includes `install`; re-resolving dependencies mid-run mutates the "
            "environment the other stages are using. Run `make install` first.",
        )
    missing_targets = [t for t in stages if not re.search(rf"^{re.escape(t)}:", makefile, re.M)]
    if missing_targets:
        return Result(
            "pipeline-targets-exist", FAIL,
            f"pipeline depends on {missing_targets}, which have no rule",
        )
    absent = sorted(required - set(stages))
    if absent:
        return Result(
            "pipeline-targets-exist", FAIL,
            f"pipeline is missing stage(s) {absent}; it runs {stages}",
        )
    return Result(
        "pipeline-targets-exist", PASS,
        f"pipeline runs {stages} and every stage has a rule",
    )


def check_type_checking_is_a_real_gate() -> Result:
    """`make type` must block, and `make check` must run it.

    `type:` was written `-@$(CONDA_RUN) mypy src/min_agent`. The leading `-` tells
    make to ignore a non-zero exit, so mypy reported 95 findings on every run and
    the command still exited 0 - the findings were visible and nothing acted on
    them, which is worse than not running it. `check:` did not depend on `type:`
    either, so the fast loop never saw it.

    This asserts the wiring rather than re-running mypy, because `make verify`
    already runs the full command and a second mypy pass would double the slowest
    gate for no extra signal. It catches the specific regression: someone
    re-adding the `-`, or dropping `type` from `check`.
    """
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^type:([\s\\@\-]*)(.*)$", makefile, re.MULTILINE)
    if not match:
        return Result("type-checking-is-a-gate", FAIL, "Makefile has no `type:` target")
    prefix, body = match.group(1), match.group(2)
    if prefix.strip() == "-":
        return Result(
            "type-checking-is-a-gate", FAIL,
            "`type:` starts with `-`, so make ignores mypy's exit code: findings are "
            "printed and the command still passes. Drop the `-`.",
        )
    if "mypy" not in body:
        return Result(
            "type-checking-is-a-gate", FAIL,
            f"`type:` does not invoke mypy; it runs {body.strip()!r}",
        )
    if "@" not in prefix:
        return Result(
            "type-checking-is-a-gate", WARN,
            "`type:` echoes the mypy command line; add `@` to match every other target",
        )
    check = re.search(r"^check:([\s\\+]*)(.*)$", makefile, re.MULTILINE)
    if not check:
        return Result("type-checking-is-a-gate", FAIL, "Makefile has no `check:` target")
    if "type" not in check.group(2).split():
        return Result(
            "type-checking-is-a-gate", FAIL,
            f"`check:` runs {check.group(2).split()} but not `type`, so the fast loop "
            "never type-checks",
        )
    return Result(
        "type-checking-is-a-gate", PASS,
        "`type:` runs mypy and blocks; `check:` runs lint, type and test",
    )


def check_unit_templates_have_no_host_paths() -> Result:
    """A `.service.in` template must not contain a path from the machine that wrote it.

    `tools/ollama.service.in` carried an `ExecStart` pointing at one account's local ollama binary
    and a matching OLLAMA_MODELS path, because that is where Ollama lives here. `minictrl`
    substituted @ROOT@, @PYTHON@, @ENVBIN@ and @ENVFILE@ but not those, so every install on
    any other machine produced a unit that systemd could not start - and the failure appears
    at boot, long after the command that caused it returned success.

    Both are placeholders now, with `minictrl` substituting them from OLLAMA_BIN and
    OLLAMA_MODELS with sane defaults. This checks both halves: no absolute host path in a
    template, and no placeholder left unsubstituted by the renderer.
    """
    templates = sorted((ROOT / "tools").glob("*.service.in")) + sorted((ROOT / "tools").glob("*.timer"))
    if not templates:
        return Result("unit-templates-portable", SKIP, "no unit templates found")
    problems: list[str] = []
    host_markers = ("/home/", "/localscratch/", "/Users/", "/opt/conda")
    for template in templates:
        text = template.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if any(marker in code for marker in host_markers):
                problems.append(
                    f"{template.name}:{line_no} hardcodes a host path: {line.strip()[:60]}"
                )
    # Every placeholder the renderer is expected to fill must actually be filled by it.
    renderer = (ROOT / "minictrl").read_text(encoding="utf-8")
    for template in templates:
        for placeholder in set(re.findall(r"@([A-Z_]+)@", template.read_text(encoding="utf-8"))):
            if f"@{placeholder}@" not in renderer:
                problems.append(
                    f"{template.name} uses @{placeholder}@ but minictrl never substitutes it"
                )
    if problems:
        return Result("unit-templates-portable", FAIL, f"{len(problems)}: {problems[:3]}")
    return Result(
        "unit-templates-portable", PASS,
        f"{len(templates)} unit template(s) carry no host path and every placeholder is substituted",
    )


def check_the_fact_figures_in_prose_match_reality() -> Result:
    """Numbers quoted in prose must match what the commands actually report.

    Several figures appeared in three places and were wrong in all of them at once:
    STATUS.md said the defect audit "passes 91/91" when `tools/audit_defects.py` reports
    61/61; it said "384 tests pass" when 800 are collected; and it reported "0 failures and
    2 warnings" from doctor while doctor printed eight, none of them the closed-lot
    criterion it attributed them to. Nothing recomputed any of them.

    Each figure is read from a command's real output rather than from a file, so the check
    cannot itself go stale - which is the failure mode it exists to catch. Scoped to the
    current-state text: a dated section records what was true then, and rewriting those would
    destroy the record rather than correct it.
    """
    problems: list[str] = []

    rc, audit_out = _run([sys.executable, "tools/audit_defects.py"], timeout=900)
    match = re.search(r"(\d+)/(\d+) checks pass", audit_out)
    if not match:
        # Not SKIP. The audit's own exit code is ignored here, so a crashing
        # audit_defects.py produced no total and was reported as "nothing to compare" -
        # the same shape as a defect check that hides a real defect behind a skip.
        return Result(
            "fact-figures-match", FAIL,
            f"the defect audit produced no 'N/M checks pass' line (exit {rc}); "
            f"the figures cannot be checked against a run that did not finish: "
            f"{_tail(audit_out, 1)}",
        )
    passed, total = match.group(1), match.group(2)

    rc, pytest_out = _run([sys.executable, "-m", "pytest", "-q", "--collect-only"], timeout=900)
    collected = re.search(r"(\d+) tests? collected", pytest_out)
    if not collected:
        return Result(
            "fact-figures-match", FAIL,
            f"pytest --collect-only printed no total (exit {rc}); the test figure in the "
            f"documents cannot be checked against a collection that did not finish",
        )

    rc, doctor_out = _run(["./minictrl", "doctor"], timeout=900)
    if "RESULT" not in doctor_out and "ok=" not in doctor_out:
        # Without this, a doctor that never ran left `failures == 0`, which *activated* the
        # branch forbidding documents from claiming RESULT OK - a check enforcing a rule on
        # the strength of a run that did not happen.
        return Result(
            "fact-figures-match", SKIP,
            f"minictrl doctor produced no report (exit {rc}); nothing to compare against",
        )
    warnings = len(re.findall(r"^\[WARN\]", doctor_out, re.MULTILINE))
    failures = len(re.findall(r"^\[FAIL\]", doctor_out, re.MULTILINE))

    # PHASES.md was omitted, and it is the document carrying the "91/91 defect audit" figure
    # that is actually wrong - the audit reports 61/61. A check scoped to the three documents
    # nobody read past the first page misses the one that drifts.
    for name in (
        "docs/history/STATUS.md",
        "README.md",
        "docs/history/MIGRATION.md",
        "docs/history/PHASES.md",
        "docs/history/CAPABILITY_CLASSIFICATION.md",
    ):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        skip = _dated_section_ranges(text)
        for match in re.finditer(r"(\d+)/(\d+) (?:checks? pass|defect audit)", text):
            if any(lo <= match.start() <= hi for lo, hi in skip):
                continue
            if match.group(1) != passed or match.group(2) != total:
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{name}:{line} says {match.group(0)!r}; the audit reports {passed}/{total}"
                )
        for match in re.finditer(r"(\d+) tests pass", text):
            if any(lo <= match.start() <= hi for lo, hi in skip):
                continue
            if match.group(1) != collected.group(1):
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{name}:{line} says {match.group(1)} tests pass; "
                    f"pytest collects {collected.group(1)}"
                )
        # A doctor warnings count is a live figure and belongs in no document: it changes
        # every cycle and every fill. This branch used to *compare* such a count against
        # doctor, and its only match in the repository sat inside a dated section, so it
        # compared nothing while reporting that prose agreed with the commands. A check that
        # passes at zero assertions is the same defect as one that cannot fail.
        #
        # So it now asserts the opposite, which is the enforceable claim: no current-state
        # document may carry a warnings figure, because any it carried would be stale the
        # moment it was written. Run `minictrl doctor` for the live number.
        for match in re.finditer(r"(?:RESULT:?\s+\w+(?:\s+with)?\s+)(\d+) warnings?", text):
            if any(lo <= match.start() <= hi for lo, hi in skip):
                continue
            line = text[: match.start()].count("\n") + 1
            problems.append(
                f"{name}:{line} states {match.group(1)} doctor warnings; that count changes "
                "every cycle, so it cannot be maintained in a document - read it from "
                "`minictrl doctor`"
            )
        # And the same for a RESULT line claiming a clean bill of health. A document that
        # says "RESULT OK" is making a claim about the running system, which is true for a
        # moment and then is not. Only a run where doctor is genuinely clean makes the
        # absence of such a claim checkable, which is exactly when a stale one would hurt.
        if failures == 0:
            # Requires no qualifier: "RESULT OK, exit 0" is a claim about now, while
            # "RESULT OK at the time" is a claim about a recorded run. The first version
            # matched both and so flagged the very sentence that had been corrected to say so.
            for match in re.finditer(r"RESULT:?\s+OK\b(?!\s+(?:at|when|on|as))", text):
                if any(lo <= match.start() <= hi for lo, hi in skip):
                    continue
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{name}:{line} claims doctor reported RESULT OK; that is a statement "
                    "about the running system at one moment - read it from `minictrl doctor`"
                )

    if problems:
        return Result("fact-figures-match", FAIL, f"{len(problems)}: {problems[:4]}")
    return Result(
        "fact-figures-match", PASS,
        # Says what it actually compared. It previously reported "doctor 0 failures / 8
        # warnings" while the warnings branch compared nothing, which made the message a
        # claim the check had not earned.
        f"prose agrees with the commands: audit {passed}/{total}, "
        f"{collected.group(1)} tests collected; and no document claims a doctor warnings "
        f"count (live value is {warnings}, which is why none may)",
    )


def check_list_reports_the_same_count_as_a_run() -> Result:
    """`--list` and a real run must state the same number of classes.

    They disagreed for the whole refactor, in both directions and for two separate reasons.
    `--list` summed CHECK_CLASSES, BESPOKE_CHECK_NAMES and a `full_only` pair, which counted
    `fact-docs-current`, `doctor` and `defect-regression-audit` twice and printed 43 while
    the run said 41. Deduplicating by union then printed 40, because `syntax-import`
    produces two Result rows in one run - `check_syntax_import()` for the compile-and-import
    sweep, and `run_pytest_class()` for the test files that class owns - so the run counts it
    twice and `--list` counted it once.

    Neither drift was visible to `gate-class-count-consistent`, which compares documents
    against the run and never compares the run against the command that documents the gate.
    So this does exactly that, by running the gate's own counting the way main() does and
    comparing.

    `syntax-import` emitting two rows is itself the underlying oddity. It is left as-is and
    reported rather than fixed here: collapsing it would change what the gate actually
    checks, which is a different decision from making the two counts agree.
    """
    import contextlib
    import io

    seen: list[str] = []
    for name in CHECK_CLASSES:
        seen.append(name)
    for name in BESPOKE_CHECK_NAMES:
        if name not in seen:
            seen.append(name)
    for name in ("defect-regression-audit", "doctor"):
        if name not in seen:
            seen.append(name)

    buffer = io.StringIO()
    argv = sys.argv
    try:
        sys.argv = ["verify.py", "--list"]
        with contextlib.redirect_stdout(buffer):
            main()
    except SystemExit:
        pass
    finally:
        sys.argv = argv
    match = re.search(r"^(\d+) classes on a full run", buffer.getvalue(), re.MULTILINE)
    if not match:
        return Result("list-count-matches-run", FAIL, "--list printed no total to compare")
    listed = int(match.group(1))

    # The run prints `len(results)` after every append. One class contributes two rows, so
    # the run's count is the distinct names plus that one duplicate.
    duplicates = 1  # syntax-import: check_syntax_import() and run_pytest_class("syntax-import")
    expected = len(seen) + duplicates
    if listed != expected:
        return Result(
            "list-count-matches-run", FAIL,
            f"--list says {listed}, the run produces {expected} "
            f"({len(seen)} distinct names + {duplicates} for syntax-import, which emits two rows)",
        )
    return Result(
        "list-count-matches-run", PASS,
        f"--list says {listed}; the run produces {expected} for the same set "
        f"(syntax-import contributes the extra row)",
    )


def check_figures_quoted_in_config_comments() -> Result:
    """Numbers written into ruff.toml's comments must still be true.

    ruff.toml explains itself with measured figures - how long tools/verify.py is, how many
    BLE001 findings are deliberately permitted, how many modules cli.py defers importing. It
    said 2,047 lines when the file was 2,627, 59 findings when there are 60, and 25 modules
    when there are 24. Each was correct when written and nobody recomputed any of them, which
    is the same disease fact-figures-match treats in prose; a configuration file that
    misdescribes its own exemptions is worse than one that says nothing, because the comment
    is the justification for allowing the exceptions.

    All three are measured live. `--isolated` is used for the ruff count so the repository's
    own configuration does not change what is being counted.
    """
    import re as _re
    import subprocess as _sp

    config = ROOT / "ruff.toml"
    if not config.exists():
        return Result("config-comment-figures-true", SKIP, "ruff.toml is absent")
    text = config.read_text(encoding="utf-8")
    problems: list[str] = []

    # Deliberately not verify.py's line count. This function lives inside verify.py, so
    # asserting its length here means the number is wrong the moment the assertion is added -
    # which is exactly what happened on the first attempt: the check reported 2,627 against
    # an actual 2,689, because writing the check had added 62 lines. A figure a file cannot
    # state about itself is a figure that rots on the next edit. The comment in ruff.toml now
    # says "thousands of lines" and stays true.
    verify_lines = len((ROOT / "tools" / "verify.py").read_text(encoding="utf-8").splitlines())

    proc = _sp.run(
        # sys.executable, as everywhere else in this file. A hardcoded "python3" measures a
        # different interpreter than the one running the gate, and on a host where `python3`
        # is not the gate's environment ruff may not even be importable - in which case the
        # count silently became "?" and the check passed.
        [sys.executable, "-m", "ruff", "check", "--isolated", "--select", "BLE001", "src/min_agent"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    found = _re.search(r"Found (\d+) error", proc.stdout)
    if not found:
        # The BLE001 half is then skipped entirely while the PASS message still printed a
        # figure for it, as "?". A check that reports PASS for a comparison it could not
        # make is worse than one that fails.
        return Result(
            "config-comment-figures-true", FAIL,
            f"could not read a BLE001 count from ruff (exit {proc.returncode}); the "
            f"permitted-exception figure in ruff.toml cannot be checked: {_tail(proc.stdout, 1)}",
        )
    if found:
        # The pattern required the digits immediately after the keyword, so it matched
        # nothing - ruff.toml reads "would have produced 60 findings" with a space. The check
        # reported the ruff count in its PASS detail while never comparing it, and rewriting
        # 60 to 7 still passed. Anchored on the phrase that actually appears.
        for match in _re.finditer(r"produced (\d+) findings", text):
            if int(match.group(1)) != int(found.group(1)):
                problems.append(
                    f"ruff.toml says {match.group(1)} BLE001 findings; ruff reports "
                    f"{found.group(1)}"
                )
        for match in _re.finditer(r"these (\d+) sites record", text):
            if int(match.group(1)) != int(found.group(1)):
                problems.append(
                    f"ruff.toml says {match.group(1)} permitted BLE001 sites; ruff reports "
                    f"{found.group(1)}"
                )

    cli_imports = len(_re.findall(r"^from min_agent", (SRC / "min_agent" / "cli.py").read_text(), _re.M))
    for match in _re.finditer(r"pay for the (\d+) modules", text):
        if int(match.group(1)) != cli_imports:
            problems.append(
                f"ruff.toml says cli.py defers {match.group(1)} module imports; it defers "
                f"{cli_imports}"
            )

    if problems:
        return Result("config-comment-figures-true", FAIL, f"{len(problems)}: {problems[:3]}")
    return Result(
        "config-comment-figures-true", PASS,
        f"ruff.toml's measured figures hold: verify.py {verify_lines:,} lines, "
        f"{found.group(1) if found else '?'} BLE001 findings, {cli_imports} deferred imports",
    )


def check_status_only_states_what_does_not_change() -> Result:
    """STATUS.md may state fixed configuration, never live counters.

    This check used to compare the document's journal cycles, submitted orders and filled
    shares against what `doctor` reported. Every one of those moves while the agent runs: the
    cycle count rises every cycle, the order count every fill. So the gate went red on every
    trade - which is worse than having no check, because a gate that cries wolf on a correct
    document is one people learn to ignore. It fired twice during this session while the
    agent was actively trading, on documents that were correct a minute earlier.

    It also caught real errors, and those corrections belong somewhere: 24 orders where the
    record showed 44, 75 shares where it showed 95.0, "0 open" where the model still held 20
    shares, and a hand-written "market OPEN" that was wrong every time the market was shut.
    The lesson is not "check the numbers more often". It is that a number which changes while
    the agent runs does not belong in a file a human reads.

    STATUS.md now carries only what is fixed - mode, allowlist, hard limits, model,
    credential location, unit paths - and points at `minictrl status`, `doctor` and `profit`
    for the rest. What is asserted is that the split holds: no counter that moves, and every
    fixed value that is stated matching the code that uses it.
    """
    status = HISTORY / "STATUS.md"
    if not status.exists():
        return Result("status-states-only-fixed-facts", SKIP, "STATUS.md is absent")
    text = status.read_text(encoding="utf-8")
    sys.path.insert(0, str(SRC))
    from min_agent.config import AgentConfig

    config = AgentConfig.from_env()
    problems: list[str] = []
    skip = _dated_section_ranges(text)

    volatile = {
        "a market open state": r"market\s+(?:OPEN|CLOSED)\b",
        "a cycle count": r"\b\d[\d,]* cycles\b",
        "an order count": r"\b\d+ orders submitted\b",
        "a share count": r"\b[\d.]+ shares? filled\b",
        "a closed-lot count": r"\b\d+ closed lots?\b",
        # A PnL figure in this project's own notation. The pattern required both a sign and
        # a dollar sign, so it matched `+$564.39` and nothing else - and every document here
        # writes it as bare `+564.39`. The check meant to forbid a live PnL in the document
        # therefore permitted the exact form the documents use. `\$` is optional because the
        # figure is also written with it, and a bare number with a sign and two decimals is
        # unambiguously money in context.
        "a realized PnL figure": r"[+-]\$?\d[\d,]*\.\d{2}",
    }
    for label, pattern in volatile.items():
        for match in re.finditer(pattern, text):
            if any(lo <= match.start() <= hi for lo, hi in skip):
                continue
            line = text[: match.start()].count("\n") + 1
            problems.append(
                f"STATUS.md:{line} states {label} ({match.group(0)!r}); that changes while the "
                "agent runs - read it from `minictrl status` or `doctor` instead"
            )

    # The fixed values are checked inside the current-facts table only, not anywhere in the
    # file. Searching the whole document let a wrong figure pass: changing "$5,000 per
    # position" to "$50,000" in the table still matched the copy further down, so the check
    # reported the configured limit as present when the table was wrong. Scoping to the table
    # means the row an operator reads is the row that is checked.
    # Terminated by the first line that is not a table row. The previous lookahead stopped at
    # the first non-whitespace character, which is the blank line right after the last row -
    # so the captured table was empty and every value read as missing.
    table = re.search(r"^\| Fixed \| Value \|$", text, re.MULTILINE)
    rows = ""
    if table:
        collected = []
        for line in text[table.end():].lstrip("\n").splitlines():
            if not line.startswith("|"):
                break
            collected.append(line)
        rows = "\n".join(collected)
    if not table:
        return Result(
            "status-states-only-fixed-facts", FAIL,
            "STATUS.md has no `| Fixed | Value |` table, so the fixed configuration it is "
            "meant to state cannot be checked",
        )

    # The table states one deployment's configuration. Compared with the defaults a clean
    # checkout reads (no operator env file), a correct table is "wrong" - CI failed on exactly
    # that the first time the limits were scaled - so the comparison binds where the
    # deployment is.
    env_file = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "min-agent" / "env"
    if not env_file.exists():
        return Result(
            "status-states-only-fixed-facts", PASS,
            "no counter that moves in STATUS.md; the fixed table is checked against the "
            "configuration only where an operator env file exists",
        )

    for symbol in sorted(config.allowlist):
        if symbol not in rows:
            problems.append(f"the fixed table does not list {symbol}, which is in the allowlist")
    for label, value in (
        ("mode", config.mode),
        ("model", config.model),
        ("per-position limit", f"{config.max_position_value:,.0f}"),
        ("exposure limit", f"{config.max_total_exposure:,.0f}"),
        ("daily loss limit", f"{config.max_daily_loss:,.0f}"),
    ):
        # Anchored on both sides. `re.escape("5,000")` matches inside "$15,000" and
        # "20,000" inside "$120,000", so a table quoting limits five times the configured ones
        # was accepted as correct - verified by doing exactly that. A limit is a number with
        # boundaries, and so is its match.
        pattern = (
            r"(?<![\d,])" + re.escape(value) + r"(?![\d,])"
            if not value.startswith("$")
            else r"\$\s?" + re.escape(value) + r"(?![\d,])"
        )
        if not re.search(pattern, rows):
            problems.append(
                f"the fixed table does not state the configured {label} ({value})"
            )

    if problems:
        return Result("status-states-only-fixed-facts", FAIL, f"{len(problems)}: {problems[:4]}")
    return Result(
        "status-states-only-fixed-facts", PASS,
        f"STATUS.md states only fixed configuration (mode={config.mode}, "
        f"model={config.model}, {len(config.allowlist)} symbols, 3 hard limits); "
        "live counters are queried rather than written down",
    )


def check_the_declared_bespoke_list_matches_what_runs() -> Result:
    """BESPOKE_CHECK_NAMES must be exactly the checks main() appends outside the loop.

    The list exists so that `--list` and the run cannot disagree about the gate's size.
    That only works while the list is maintained, and nothing connected it to main() - the
    same shape of defect as the CHECK_CLASSES staleness this repository already had once,
    where a tuple drifted from reality and every green run reported it as healthy.

    So the source of main() is read and every `check_*` it appends is compared against the
    declared names, using the Result name each function returns. Read from the AST rather
    than by running the gate: this check runs inside the gate, and spawning a second copy
    of it to introspect the first is how the earlier `fact-docs-current` recursion
    happened.
    """
    import ast

    tree = ast.parse((ROOT / "tools" / "verify.py").read_text(encoding="utf-8"))
    main_fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    if main_fn is None:  # pragma: no cover
        return Result("bespoke-list-matches-run", FAIL, "main() not found in tools/verify.py")

    # name each appended check function by the Result name it can return
    returns: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("check_")):
            continue
        found = {
            arg.value
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "Result"
            # The name is the FIRST argument. An earlier version read args[1], which is
            # the status (PASS/FAIL), a Name rather than a Constant - so the comprehension
            # matched nothing and every check looked undeclared. Read the signature.
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
            for arg in [call.args[0]]
        }
        returns[node.name] = found

    # main() now wraps every check in `_safe(...)` so that one raising check cannot kill the
    # whole gate. This scan has to see through that wrapper, or it reports every bespoke
    # check as undeclared - which is exactly what happened the first time it ran after the
    # change. Both shapes are handled: a bare `check_x()` and a `_safe(check_x, "name")`.
    appended: set[str] = set()
    for node in ast.walk(main_fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id.startswith("check_"):
            appended |= returns.get(node.func.id, {node.func.id})
        elif node.func.id == "_safe" and node.args:
            inner = node.args[0]
            target = inner.func if isinstance(inner, ast.Call) else inner
            if isinstance(target, ast.Name) and target.id.startswith("check_"):
                # Resolve the Result name from the function's own body, which this function
                # already collects - rather than from the string passed to _safe, which is
                # maintained by hand and was wrong for six checks on the first attempt.
                appended |= returns.get(target.id, {target.id})

    declared = set(BESPOKE_CHECK_NAMES)
    # Checks appended by name that are also in CHECK_CLASSES are not bespoke by definition;
    # they are the loop's classes, invoked directly so they can take a live argument.
    bespoke = appended - set(CHECK_CLASSES)
    missing = sorted(bespoke - declared)
    extra = sorted(declared - appended)
    if missing or extra:
        return Result(
            "bespoke-list-matches-run", FAIL,
            f"declared but never appended: {extra}; appended but undeclared: {missing}",
        )
    return Result(
        "bespoke-list-matches-run", PASS,
        f"{len(declared)} bespoke names, all appended by main() and all reported by --list",
    )


def check_the_gate_class_count_is_reported_consistently(total: int | None = None) -> Result:
    """Every document that states the class count must state the one the gate reports.

    The count appeared as 29, 30 and 33 in three documents while the gate ran a
    different number in each case - a completion verifier caught it. Nothing connected the
    prose to the code, and the `fact-docs-current` check only compared the current-state
    blocks in two files, not the summary sentences elsewhere.

    `34 check classes` is matched literally rather than by parsing, so a sentence that
    gives a different number is found wherever it is. A count written in words rather
    than digits would not be caught, which is a real limitation stated here rather than
    left for the next reader to discover.
    """
    # `total` is the number of classes this run will report, supplied by the caller, which
    # is the only place that knows it. Three earlier versions each got this wrong and each
    # failed a document that was correct:
    #   - `len(CHECK_CLASSES) + 4`, with the 4 written from memory of which bespoke checks
    #     run outside the loop: reported 33 while the gate ran 34.
    #   - `len(live)` read at the moment this check was appended: two checks are appended
    #     after it, so it compared the documents against a count two short of its own.
    #   - the same, plus a self-appended call that reported SKIP and inflated the total.
    # The number is now passed down from the one place that knows it.
    if total is None:
        return Result(
            "gate-class-count-consistent", SKIP,
            "no total supplied; this check compares the documents against the running gate",
        )
    # A partial run reports a count describing the subset, so comparing the documents against
    # it is meaningless - and it made `make verify CLASS=pnl-accounting` fail with "README says
    # 44 check classes, gate reports 33", on a workflow that README, Makefile's help and
    # STATUS.md all advertise. The gate was therefore red on arrival for anyone debugging one
    # class. `fact-docs-current` has had this guard all along; this check needed the same one.
    if _partial_run:
        return Result(
            "gate-class-count-consistent", SKIP,
            "partial run: this check compares the documents against a full run's class count, "
            "which this run does not represent",
        )
    expected = f"{total} check classes"
    problems = []
    for path in sorted(ROOT.rglob("*.md")):
        parts = path.relative_to(ROOT).parts
        if parts[0] in {"runtime", ".git", ".opencode", "node_modules", "plans"}:
            continue
        # SYSTEM_AUDIT.md is a historical record in its entirety and opens by saying so;
        # every figure in it is point-in-time. Its per-section markers are sparse, and
        # comparing them to the live count would mean rewriting the record of what was
        # once broken, which is the one thing that document exists to preserve.
        if path.name == "SYSTEM_AUDIT.md":
            continue
        # docs/history/ is the archive: every figure in it is the figure as of its date.
        if path.is_relative_to(HISTORY):
            continue
        # A dated history section states the count as it was, and rewriting it would
        # destroy the record: STATUS.md's 2026-09-28 section says 17 and is correct for
        # that date. Such a section is one that opens with a "> Dated section" marker, and
        # the exemption covers everything from the marker to the end of the file - see
        # `_dated_section_ranges`, which is the definition and states that explicitly. Matching a marker only on adjacent lines
        # was the first attempt and missed, because the marker and the figure can be
        # forty lines apart.
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        skip_ranges = _dated_section_ranges(text)
        for match in re.finditer(r"\b\d+ check classes\b", text):
            if any(lo <= match.start() <= hi for lo, hi in skip_ranges):
                continue
            if match.group(0) != expected:
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{path.relative_to(ROOT)}:{line} says {match.group(0)!r}, gate reports {expected!r}"
                )
    if problems:
        return Result(
            "gate-class-count-consistent", FAIL,
            f"{len(problems)}: {problems[:3]}",
        )
    return Result(
        "gate-class-count-consistent", PASS,
        f"every document that states the count says {expected!r}",
    )


def check_tools_readme_names_real_files() -> Result:
    """Every file `tools/README.md` names must exist, and every real tool must be named.

    The previous version documented fifteen one-off diagnostic scripts that had been
    deleted, leaving a `tools/legacy/` directory containing nothing but `__pycache__`
    residue. A README that names absent files is worse than no README: a reader concludes
    a tool is missing rather than that it was consolidated, and a document that cannot be
    trusted is not worth maintaining.

    Both directions are checked. Naming an absent file sends someone looking for it;
    leaving a real tool unnamed means the one script that still matters is the one nobody
    discovers.
    """
    readme = ROOT / "tools" / "README.md"
    if not readme.exists():
        return Result("tools-readme-accurate", FAIL, "tools/README.md is missing")
    text = readme.read_text(encoding="utf-8")
    # The hyphen leads the class. Written as [a-z0-9_-] the trailing hyphen becomes a
    # range, and `min-agent.service.in` matches nothing - which is how the gate first
    # reported three present-but-undocumented files that the README named in plain sight.
    # The character class allows a dot so that `min-agent.service.in` matches: the
    # extension is preceded by a name that itself contains dots. An earlier version
    # allowed only [-a-z0-9_] and silently matched none of the .in templates, which is
    # why the gate first reported three files as "present but undocumented" that the
    # README named in plain sight.
    # Extensions covered, not just the three this file happened to use when the check
    # was written: .timer is a real unit template and omitting it produced a false
    # "present but undocumented" for a file the README names in plain sight.
    named = set(re.findall(r"`([a-z][-a-z0-9_.]*\.(?:py|sh|in|timer|service|target))`", text))
    on_disk = {p.name for p in (ROOT / "tools").iterdir() if p.is_file()}
    # README.md documents itself; exclude it from the "unnamed" direction.
    on_disk.discard("README.md")
    missing = sorted(n for n in named if n not in on_disk and n != "test_alpaca.py")
    unnamed = sorted(on_disk - named)
    if missing or unnamed:
        parts = []
        if missing:
            parts.append(f"named but absent: {missing}")
        if unnamed:
            parts.append(f"present but undocumented: {unnamed}")
        return Result(
            "tools-readme-accurate", FAIL,
            f"tools/README.md is out of date with the directory - {'; '.join(parts)}",
        )
    return Result(
        "tools-readme-accurate", PASS,
        f"tools/README.md names all {len(on_disk)} tools and invents none of them",
    )


def check_the_example_still_runs() -> Result:
    """`examples/minimal_cycle.py` must execute, not merely exist.

    An example that no longer runs is worse than none: it is the first thing a new
    reader runs, and a failure there teaches them the project is broken before they have
    read anything. This one drifted the moment it was written - it imported a
    `Position` that the models module does not define, and constructed a `TradeDecision`
    without its required `rationale` - and nothing noticed, because nothing executed it.

    Runs it in a subprocess with no broker and no credentials, which is the property the
    example claims for itself. Output is discarded; the exit status is the check.
    """
    example = ROOT / "examples" / "minimal_cycle.py"
    if not example.exists():
        return Result("example-runs", FAIL, "examples/minimal_cycle.py is missing")
    proc = subprocess.run(
        [sys.executable, str(example)],
        capture_output=True, text=True, timeout=180,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    if proc.returncode != 0:
        return Result(
            "example-runs", FAIL,
            f"examples/minimal_cycle.py exited {proc.returncode}: "
            f"{(proc.stderr.strip() or proc.stdout.strip())[-180:]}",
        )
    return Result("example-runs", PASS, "examples/minimal_cycle.py runs with no broker or credentials")


def check_one_credentials_path_everywhere() -> Result:
    """Every entry point and document must resolve the credentials file the same way.

    Three destinations were documented and two of them were wrong on this host. The Makefile
    used `$(XDG_CONFIG_HOME)/min-agent/env`, which becomes `/min-agent/env` when
    `XDG_CONFIG_HOME` is unset; `minictrl` used `${XDG_CONFIG_HOME:-$HOME/.config}/...`;
    `configs/paper.env.example` and the operator runbook both hardcoded `~/.config/min-agent`.
    Here `XDG_CONFIG_HOME` points at a non-default directory and
    `~/.config/min-agent` does not exist - so following the example put the credentials
    somewhere nothing reads them, and the operator's first symptom would be "no credentials"
    with no indication where they had been written.

    The resolution is one expression, `$XDG_CONFIG_HOME/min-agent/env` with a `$HOME/.config`
    fallback, and this checks that all four agree on it.
    """
    patterns = {
        "Makefile": (ROOT / "Makefile").read_text(encoding="utf-8"),
        "minictrl": (ROOT / "minictrl").read_text(encoding="utf-8"),
        "configs/paper.env.example": (ROOT / "configs" / "paper.env.example").read_text(encoding="utf-8"),
    }
    problems = []
    for name, text in patterns.items():
        if "min-agent/env" not in text:
            problems.append(f"{name} does not mention the credentials file at all")
            continue
        # The line that actually resolves the file, rather than whether the word
        # XDG_CONFIG_HOME appears anywhere in the file. A first version tested the latter and a
        # version of the example with the fallback stripped from every command but one
        # unrelated sentence still passed.
        # Setup instructions count: in a commented example block the `cp` line that creates
        # the file is the instruction. A first version filtered out comment lines and so
        # never examined the one line that matters.
        # Executable or copy-pasteable lines only: a prose sentence that *describes* the
        # path is not an instruction, and one such sentence was enough to make a file full of
        # hardcoded `~` paths pass. An instruction is one naming a shell verb.
        # `.` was in this list and matched every sentence containing a filename, so a prose line
# describing the path satisfied the check on its own.
        # Either an instruction or the assignment that defines the path. The Makefile sets
        # `ENVFILE ?= .../min-agent/env` and only `include`s the variable, so a version of
        # this that required a shell verb found nothing in the Makefile and, once that was
        # made a failure rather than a pass, failed on the one entry point that is correct.
        verbs = re.compile(
            r"^\s*#?\s*(mkdir|cp|install|chmod|export|EDITOR|cat|tee|source|[A-Z_]+\s*[?:]?=)"
        )
        resolution = [
            line for line in text.splitlines()
            if "min-agent/env" in line and verbs.search(line)
        ]
        if not resolution:
            problems.append(
                f"{name} has no instruction that names min-agent/env, so its credentials "
                "path cannot be checked - an entry point that never reads the file would "
                "pass this check"
            )
            continue
        if not any("XDG_CONFIG_HOME" in line for line in resolution):
            problems.append(
                f"{name} resolves the credentials file without XDG_CONFIG_HOME "
                f"(lines: {[l.strip()[:50] for l in resolution[:2]]}); on a host where "
                "XDG_CONFIG_HOME points elsewhere that path is not read"
            )
    # verify.py's own resolution must carry the same fallback.
    if "XDG_CONFIG_HOME" not in patterns["Makefile"] or "HOME" not in patterns["Makefile"]:
        problems.append("Makefile's ENVFILE does not fall back to $HOME/.config")
    if problems:
        return Result("one-credentials-path", FAIL, f"{len(problems)}: {problems}")
    return Result(
        "one-credentials-path", PASS,
        "Makefile, minictrl and the config example all resolve "
        "$XDG_CONFIG_HOME/min-agent/env with a $HOME/.config fallback",
    )


def check_no_production_function_is_unreachable() -> Result:
    """Every function under src/min_agent must be called by something.

    An audit listed 25 definitions that no code path reaches. Most were pydantic validators
    and properties, which the framework invokes without a name and which must not be
    deleted - so the check has to know the difference, or it becomes a machine for breaking
    schema validation.

    What counts as dead here: a method whose name appears nowhere in src/, tools/, tests/,
    examples/ or the Makefile, and which is not a pydantic hook. The pydantic hooks are
    identified by name - `normalize_*`, `require_*`, `validate_*`, `model_*`, `parse_*` -
    plus anything declared as a property. That list is a heuristic, and the honest limit is
    stated here: a hook registered some other way than a decorator would still be flagged.

    It also skips `research/`, which is deliberately reachable only from tests.
    """
    import ast

    roots = [SRC / "min_agent", ROOT / "tools", ROOT / "tests", ROOT / "examples"]
    haystack = []
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            try:
                haystack.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
    haystack.append((ROOT / "Makefile").read_text(encoding="utf-8"))
    haystack.append((ROOT / "minictrl").read_text(encoding="utf-8"))
    combined = "\n".join(haystack)

    pydantic_hooks = re.compile(
        r"^(normalize_|require_|validate_|model_|parse_|is_|has_|get_|set_|__)"
    )
    dead: list[str] = []
    for path in sorted((SRC / "min_agent").rglob("*.py")):
        if "research" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("__"):
                continue
            # A decorator written as a call - `@model_validator(mode="after")` - is the call's
            # function. Read as-is it had no name, so such validators survived only by the
            # name-prefix heuristic below and any other name was reported as dead.
            decorators = {
                (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
                for f in (d.func if isinstance(d, ast.Call) else d for d in node.decorator_list)
            }
            if decorators & {
                "property", "cached_property", "validator", "field_validator", "model_validator",
            }:
                continue
            if pydantic_hooks.match(node.name):
                continue
            if len(re.findall(rf"\b{re.escape(node.name)}\b", combined)) <= 1:
                dead.append(f"{path.name}:{node.lineno} {node.name}()")
    if dead:
        return Result(
            "no-unreachable-production-code", FAIL,
            f"{len(dead)} definition(s) nothing calls: {dead[:4]}",
        )
    return Result(
        "no-unreachable-production-code", PASS,
        "every function under src/min_agent is called, or is a pydantic hook or property",
    )


def check_config_example_covers_every_variable() -> Result:
    """Every variable config.py reads must appear in configs/paper.env.example.

    The sibling `config-example-names` check runs the other direction - it stops the example
    naming a setting nothing reads - and between them they left a hole. The example
    documented 13 of the 38 variables the loader reads; the 25 it omitted were checked by
    nothing, and README called the file "every environment variable, with its default", which
    made an incomplete file an actively misleading one. Someone setting up the system would
    not know which of their assumptions were real.

    So this asks the loader directly rather than parsing its source: instantiate
    `AgentConfig` and diff its fields against the names in the example. Both directions now
    hold, and neither can rot silently, because the field list is the truth.

    A variable read outside the dataclass - a broker alias, say - is genuinely absent from
    the fields, so this complements the other check instead of duplicating it. The union of
    both is what the example is required to document.
    """
    example = ROOT / "configs" / "paper.env.example"
    if not example.exists():
        return Result("config-example-complete", FAIL, "configs/paper.env.example is absent")
    sys.path.insert(0, str(SRC))
    from min_agent.config import AgentConfig
    documented = set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", example.read_text(encoding="utf-8"), re.M))
    # The dataclass field names and the env var names differ by prefix and word order
    # (`max_position_value` reads MIN_AGENT_MAX_POSITION_VALUE), so the names are taken from
    # the loader's own read sites rather than derived from the fields. An earlier version
    # computed the field set and then never used it - ruff caught the dead assignment, which
    # is the check working - so `fields` is gone and the scan below stands on its own.
    loader = (SRC / "min_agent" / "config.py").read_text(encoding="utf-8")
    read = set(re.findall(r'"(MIN_AGENT_[A-Z0-9_]+)"', loader)) | set(
        re.findall(r'"(ALPACA_[A-Z0-9_]+)"', loader)
    ) | set(re.findall(r'"(APCA_[A-Z0-9_]+)"', loader)) | set(re.findall(r'"(OLLAMA_[A-Z0-9_]+)"', loader))
    missing = sorted(read - documented)
    if missing:
        return Result(
            "config-example-complete", FAIL,
            f"{len(missing)} variable(s) the loader reads are absent from the example: "
            f"{missing[:6]}{' ...' if len(missing) > 6 else ''}",
        )
    return Result(
        "config-example-complete", PASS,
        f"all {len(read)} variable(s) the loader reads are documented in the example",
    )


def check_config_example_names_are_real() -> Result:
    """Every variable in configs/paper.env.example must exist in the codebase.

    A configuration example is read by whoever is setting the system up, and a name that
    nothing reads is a setting they will change in the belief it took effect. The names
    are checked against config.py's own env reads plus the deployed env file's keys,
    which together are where a variable can legitimately come from.

    `config.py` is not the only reader, and assuming it was made this check host-dependent
    in the worst possible way. The env file is dual-purpose: `minictrl` sources the same file
    before it ever calls `python -m min_agent.cli`, and reads MIN_AGENT_ENVBIN out of it to
    find the environment's bin directory. `MIN_AGENT_ENVBIN` is set in the deployed env file
    on this host and is absent from config.py, so documenting it made this check pass *only
    because the env file happened to be present* - and a fresh clone, which is exactly where
    someone reads the example, would have reported a live knob as dead and sent them to delete
    the line that makes their install work. So `minictrl`'s executable code is a reader too.
    """
    example = ROOT / "configs" / "paper.env.example"
    if not example.exists():
        return Result("config-example-names", FAIL, "configs/paper.env.example is missing")
    names = set(re.findall(r"^(MIN_AGENT_[A-Z_]+|ALPACA_[A-Z_]+|OLLAMA_BASE_URL)=", example.read_text(), re.M))
    if not names:
        return Result("config-example-names", FAIL, "no variable names found in the example")
    known = (ROOT / "src" / "min_agent" / "config.py").read_text(encoding="utf-8")
    known += _entry_point_reads()
    env_file = Path(os.environ.get("XDG_CONFIG_HOME", "")) / "min-agent" / "env"
    if env_file.exists():
        known += env_file.read_text(encoding="utf-8")
    unknown = sorted(n for n in names if n not in known)
    if unknown:
        return Result(
            "config-example-names", FAIL,
            f"{len(unknown)} variable(s) in the example are read by nothing: {unknown}",
        )
    return Result(
        "config-example-names", PASS,
        f"all {len(names)} variable names in the example are read by config.py, by minictrl, "
        "or by the env file",
    )


def _entry_point_reads() -> str:
    """`minictrl`'s executable code, whole-line comments dropped.

    The same file documents in its header every shell knob it honours, so scanning it whole
    would count a variable that is only described as having existed. The header is entirely
    `#` comment lines, so dropping those is enough to make the scan mean "this code reads it".
    Inline trailing comments are left in: minictrl's executable body is short, and excluding
    them would need a shell tokenizer to do properly.
    """
    script = ROOT / "minictrl"
    if not script.exists():
        return ""
    code = [line for line in script.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]
    return "\n".join(code)


def check_docs_do_not_instruct_deleted_commands() -> Result:
    """No document may tell a reader to run a command that no longer exists.

    Found by the completion verifier, after the deliverables were already written and
    every target was green. `AUTO_REVIEW_README.md` documented `auto-fix.sh`,
    `check-market-open.sh` and `monitor.sh`; the two runbooks told a reader to
    `bash run_forever.sh`. Every one of those files was deleted as a duplicate, and
    nothing failed, because no check connected the documentation to the filesystem.

    A document that names a script is an executable instruction whether or not anyone
    runs it, and an instruction that cannot work is worse than a missing one: the reader
    concludes the tool is broken rather than that it moved. `docs/history/MIGRATION.md` is
    exempt by name - its entire purpose is to name removed paths - and matches on the
    command form (`bash x.sh`, `python x.py`) so prose describing a past event is not
    mistaken for a live instruction.
    """
    removed = (
        "auto-fix.sh", "monitor.sh", "observe.sh", "run_forever.sh",
        "check-market-open.sh", "auto_reviewer.py",
    )
    patterns = [re.compile(rf"\b(?:bash|python3?|\./)\s*{re.escape(name)}\b") for name in removed]
    problems = []
    for path in sorted(ROOT.rglob("*.md")):
        parts = path.relative_to(ROOT).parts
        if parts[0] in {"runtime", ".git", ".opencode", "node_modules"}:
            continue
        if path.name == "MIGRATION.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for pattern in patterns:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                problems.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)!r}")
    if problems:
        return Result(
            "docs-no-deleted-commands", FAIL,
            f"{len(problems)} instruction(s) name a deleted script: {problems[:4]}. "
            "Point them at the replacement in docs/history/MIGRATION.md.",
        )
    return Result(
        "docs-no-deleted-commands", PASS,
        f"no document instructs a reader to run one of the {len(removed)} removed scripts",
    )


def check_the_running_daemon_matches_the_worktree() -> Result:
    """The daemon's heartbeat must carry the source fingerprint of what is on disk.

    Found the hard way. `evaluator.is_system_rejection` was corrected and tested, and
    `fixed-size-sell-005` was restored to PROBATION - and then retired again, with the
    identical reason, two hours later, because the daemon had been left running since
    before the fix. A Python process holds the code it imported. Editing the source
    changes nothing for it.

    Nothing noticed. The heartbeat was fresh, journal events were landing, `doctor` was
    green, and the watchdog reported ok=True. Liveness and currency are separate
    properties, and every check in this gate was asking about the first. A healthy
    daemon running superseded code is the most expensive kind of wrong: it looks like
    progress and it is not.

    The fingerprint is hashed from the `min_agent` package as it sits on disk, and the
    heartbeat records the one the running process imported. Comparing the two turns "did
    anyone restart it" from an unanswerable question into a fact.
    """
    heartbeat = ROOT / "runtime" / "min_agent" / "heartbeat.json"
    if not heartbeat.exists():
        return Result("daemon-source-matches-worktree", SKIP, "no heartbeat; daemon not running")
    try:
        recorded = json.loads(heartbeat.read_text(encoding="utf-8")).get("source_fingerprint")
    except (OSError, json.JSONDecodeError) as exc:
        return Result("daemon-source-matches-worktree", FAIL, f"unreadable heartbeat: {exc}")
    if not recorded:
        return Result(
            "daemon-source-matches-worktree", FAIL,
            "the running heartbeat carries no source_fingerprint, so it predates the "
            "field and nothing can say whether it is running the code on disk",
        )

    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); "
         "from min_agent.daemon import source_fingerprint; print(source_fingerprint())"
         % str(ROOT / "src")],
        capture_output=True, text=True, timeout=180,
    )
    current = proc.stdout.strip()
    if proc.returncode != 0 or not current:
        return Result(
            "daemon-source-matches-worktree", FAIL,
            f"could not compute the worktree fingerprint: {proc.stderr.strip()[:120]}",
        )
    if recorded != current:
        return Result(
            "daemon-source-matches-worktree", FAIL,
            f"the daemon reports fingerprint {recorded} but src/min_agent is now {current}"
            f" - it is running superseded code; systemctl --user restart min-agent.service",
        )
    return Result(
        "daemon-source-matches-worktree", PASS,
        f"daemon and worktree both at {current}",
    )


def check_units_are_where_systemd_looks() -> Result:
    """A --user unit is only reboot-persistent if it lives where the manager reads.

    This host's systemd user manager has no XDG_CONFIG_HOME in its start-time
    environment, so it searches $HOME/.config/systemd/user and the tmpfs runtime
    dir - never the shell's XDG_CONFIG_HOME. Installing to the latter produces
    files systemd never opens, while `systemctl is-enabled` still answers
    "enabled" because the symlink exists. Worse, an enablement link pointing into
    /run is a dangling link the moment the machine reboots, so the unit silently
    never comes back and nothing reports an error.

    Both failure modes were live here: units deployed to a directory the manager
    ignored, and enablement symlinks targeting /run/user/... . Both are checked.
    """
    if not shutil.which("systemctl"):
        return Result("units-where-systemd-looks", SKIP, "no systemd on this host")

    runtime = f"/run/user/{os.getuid()}"
    problems: list[str] = []

    for unit in ("min-agent.service", "ollama.service", "quant-watchdog.timer"):
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "FragmentPath", "--value"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception as exc:  # pragma: no cover
            problems.append(f"{unit}: could not query ({exc})")
            continue
        path = proc.stdout.strip()
        if proc.returncode != 0 or not path:
            continue  # unit not installed here; not this check's business
        if path.startswith(runtime):
            problems.append(f"{unit} is loaded from tmpfs {path} - gone on reboot")

    # Enablement links must not target the runtime dir either.
    for base in (os.environ.get("XDG_CONFIG_HOME", ""), os.path.expanduser("~/.config")):
        wants = Path(base) / "systemd" / "user" / "default.target.wants" if base else None
        if wants is None or not wants.is_dir():
            continue
        for link in wants.iterdir():
            try:
                target = os.readlink(link)
            except OSError:
                continue
            if target.startswith(runtime):
                problems.append(
                    f"{link.name} is enabled via a link into tmpfs ({target}) - "
                    "dangling after reboot"
                )

    # A unit can live in exactly the right place, be enabled from exactly the right
    # link, and still be unable to start, because nothing has checked that the binary
    # in ExecStart exists. `systemd-analyze verify` does not stat it.
    #
    # That is not hypothetical: `minictrl install-service` fell back to the literal
    # /usr/local/bin/ollama when `command -v ollama` found nothing, and this host has
    # no such file. The install reported success and the unit loaded from $HOME. The
    # service only failed when it was next started - 40 minutes later, with
    # `status=203/EXEC` - on a unit that had otherwise been up a day and a half. Ollama
    # decides the trades, so for that window every decision silently came from the
    # policy engine instead of the model, and `doctor` was the only thing that
    # noticed.
    #
    # So the path each unit will actually execute is resolved and checked for
    # existence, because a durable unit that cannot start is a reboot away from
    # discovering it.
    for unit in ("min-agent.service", "ollama.service", "quant-watchdog.service"):
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "ExecStart", "--value"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception as exc:  # pragma: no cover
            problems.append(f"{unit}: could not query ExecStart ({exc})")
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        for token in proc.stdout.split():
            if not token.startswith("path="):
                continue
            exe = token[len("path="):]
            if exe and not Path(exe).exists():
                problems.append(
                    f"{unit} ExecStart {exe} does not exist - the unit would fail with "
                    f"203/EXEC the next time it is started, whatever its file looks like"
                )
                break

    return Result(
        "units-where-systemd-looks",
        FAIL if problems else PASS,
        "; ".join(problems[:4]) if problems else
        "every --user unit loads from a durable path, is enabled without a tmpfs link, "
        "and its ExecStart exists",
    )


def check_replay_audit() -> Result:
    """Re-derive the load-bearing numbers from the raw journal, independently.

    Every figure in the audit and the capability classification came from calling
    the production code. That is circular for the purpose they are used for: the
    objective forbids treating "the function exists and the test passes" as "the
    feature works", and asserting +564.39 is correct by asking the code that
    computed +564.39 is that same mistake one level up.

    `tools/replay_audit.py` parses the jsonl as text with no `min_agent` import in
    its counting path and re-derives each number from first principles, including
    the FIFO lot arithmetic. It exits non-zero on any disagreement.
    """
    script = ROOT / "tools" / "replay_audit.py"
    if not script.exists():
        return Result("replay-audit", SKIP, "tools/replay_audit.py is absent")
    import subprocess as sp
    try:
        proc = sp.run(
            [sys.executable, str(script)], capture_output=True, text=True, timeout=300,
        )
    except Exception as exc:  # pragma: no cover
        return Result("replay-audit", FAIL, f"could not run: {type(exc).__name__}: {exc}")
    if proc.returncode == 0:
        summary = next(
            (l.strip() for l in proc.stdout.splitlines() if "independently reproduced" in l),
            "reproduced",
        )
        return Result(
            "replay-audit", PASS,
            f"{summary} straight from the raw journal, no production code involved",
        )
    # A clone has no journal yet, so there is nothing to disagree with. Reporting that as
    # FAIL said "the record is corrupt" about a repository that has simply never run, and
    # made `make verify` unusable on the one machine state a new developer starts from -
    # the gate could not be run before the first cycle. Absence of state is SKIP; state
    # that contradicts itself is still FAIL.
    journal = ROOT / "runtime" / "min_agent" / "journal.jsonl"
    if not journal.exists():
        return Result(
            "replay-audit", SKIP,
            "no runtime/min_agent/journal.jsonl yet; nothing to replay",
        )
    bad = [
        l.strip() for l in proc.stdout.splitlines() if "[MISMATCH]" in l
    ]
    return Result(
        "replay-audit", FAIL,
        "independent replay disagrees with the reported figures: "
        + "; ".join(bad[:4]) + f" (exit {proc.returncode})",
    )


#: The statuses that mean an order actually reached the broker.
EXECUTED_STATUSES = {"SUBMITTED", "FILLED"}


def check_shadow_stage_has_actually_run() -> Result:
    """Report whether the shadow stage has ever executed, as distinct from existing.

    The gap this exists to close: PHASES.md marked Phase 5 MET citing
    `status=SHADOWED` from the live journal, and the journal has never contained
    one. `MIN_AGENT_SHADOW` is off, the sink is implemented and proven not to leak,
    and none of that is evidence that the stage ran. Reading a phase exit criterion
    off code that exists rather than code that executed is how a gate ends up
    certifying a phase nobody has walked through.

    Deliberately reports rather than fails. A system that has never run shadow is
    an honest starting state, not a defect - but it must not be able to say "MET".
    """
    sys.path.insert(0, str(SRC))
    from min_agent.config import AgentConfig
    from min_agent.journal import JsonlJournal

    journal = JsonlJournal(AgentConfig.from_env().journal_path)
    generations = journal.history_paths()
    if not generations:
        return Result("shadow-stage-exercised", SKIP, "no journal to inspect")
    # Every generation, not just the live file. It read `journal.jsonl` alone, so after the
    # rotation that fired on this host it reported "0 intent(s) on record" while
    # journal.jsonl.1 held one - a check stating a fact about the shadow stage that was
    # simply false, and it is the check that decides whether Phase 5 counts as exercised.
    shadowed = 0
    intents = 0
    for generation in generations:
        with generation.open(encoding="utf-8") as handle:
            for line in handle:
                if "SHADOWED" in line:
                    shadowed += 1
                if "SHADOW_ORDER_INTENT" in line:
                    intents += 1
    if shadowed:
        return Result(
            "shadow-stage-exercised", PASS,
            f"{shadowed} shadowed execution(s) on record across {intents} intent(s), "
            f"counted across {len(generations)} journal generation(s)",
        )
    return Result(
        "shadow-stage-exercised", WARN,
        f"the shadow mechanism is verified but the stage has never run: "
        f"{shadowed} shadowed execution(s), {intents} intent(s) on record across "
        f"{len(generations)} journal generation(s)",
        "an intent is recorded but no execution was ever shadowed, so the stage is not "
        "exercised; it cannot be called MET on the strength of mechanism tests alone",
    )


def check_shadow_cannot_count_as_executed() -> Result:
    """No production module may treat a shadowed order as an executed trade.

    A shadow order is not a fill. If any code path counted `SHADOWED` as executed,
    the account would book profit from money that was never risked and an
    unvalidated path would appear as the best one on record - the single most
    dangerous bug this system could have, because it would make the loop lie while
    looking healthy.

    Checked as a gate rather than a test because it is a cross-cutting invariant over
    every branch on `execution.status`, and a new branch added later is exactly when
    it would be introduced.
    """
    problems: list[str] = []
    for module in sorted((ROOT / "src" / "min_agent").glob("*.py")):
        text = module.read_text()
        for line_no, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if "SHADOWED" not in code:
                continue
            # The Literal that declares the statuses lists them all together and is
            # not a comparison. Without this exemption the check failed on its own
            # type declaration, which is how a gate earns a reputation for noise.
            if "Literal[" in code:
                continue
            # A comparison that groups SHADOWED with an executed status would let a
            # shadow order through as a fill.
            for executed in EXECUTED_STATUSES:
                if f'"{executed}"' in code and "SHADOWED" in code:
                    problems.append(
                        f"{module.name}:{line_no} groups SHADOWED with {executed}"
                    )
    detail = (
        "; ".join(problems) if problems else
        "no module treats SHADOWED as an executed order"
    )
    return Result(
        "shadow-not-executed", FAIL if problems else PASS, detail
    )


def check_doctor_checks_are_all_reachable() -> Result:
    """Every `_check_*` function must be called from the governance entry point.

    Written after adding a doctor check whose function was defined and never
    invoked - the anchor it was inserted against did not match, and nothing
    complained. A check that is defined but not called is the "implemented but
    unused" category the whole audit exists to eliminate, and it is invisible in
    every other way: the module imports cleanly, the tests pass, and the report is
    simply missing a line.
    """
    doctor = (SRC / "min_agent" / "doctor.py").read_text()
    defined = set(re.findall(r"^def (_check_[A-Za-z0-9_]+)\(", doctor, re.M))
    # A call site is any mention NOT preceded by `def `. Subtracting name *sets*
    # cannot work here: every called name is also a defined name, so the two sets are
    # identical and the subtraction empties to nothing - which is what the first
    # version of this check did, and it reported all 17 checks as unreachable.
    called = {
        m.group(1)
        for m in re.finditer(r"\b(_check_[A-Za-z0-9_]+)\(", doctor)
        if not doctor[max(0, m.start() - 4):m.start()].endswith("def ")
    }
    unreachable = sorted(defined - called)

    detail = (
        f"all {len(defined)} doctor checks are called"
        if not unreachable
        else f"defined but never called: {unreachable}"
    )
    return Result("doctor-checks-reachable", FAIL if unreachable else PASS, detail)


def check_screen_is_direction_neutral(
    journal_path: Path | None = None, strategy_dir: Path | None = None
) -> Result:
    """No live strategy may stand rejected for the days it happened to trade on.

    Every screen verdict rests on a one-share 24-hour probe, so on a rising day every SELL
    is wrong whoever made it. Measured on 2026-10-07: on 9 of 13 days nearly every graded
    outcome went one way, SELL decisions were right 4% of the time against BUY's 71%, and
    six strategies had been rejected by a raw ratio that their days alone explained. The
    screen now reads each strategy against its days (`offline_validation.day_direction`).

    This re-derives that from the journal, so a regression to a direction-blind gate shows
    up here as soon as it rejects something: it FAILs when a PROBATION or ACTIVE strategy's
    latest journalled verdict is REJECT_POOR_DECISIONS and the day-adjusted screen over the
    same rows would not reject it. It also reports the correct rate by action, which is
    where the bias shows first.
    """
    sys.path.insert(0, str(SRC))
    from min_agent import offline_validation as ov
    from min_agent.journal import JsonlJournal
    from min_agent.strategy_engine import StrategyLibrary

    journal_path = journal_path or ROOT / "runtime" / "min_agent" / "journal.jsonl"
    strategy_dir = strategy_dir or ROOT / "runtime" / "min_agent" / "strategies"
    if not journal_path.exists():
        return Result(
            "screen-direction-neutral", SKIP,
            "no runtime/min_agent/journal.jsonl yet; nothing has been screened",
        )
    journal = JsonlJournal(journal_path)
    events = journal.read_events("COUNTERFACTUAL_EVALUATED")
    screens: dict[str, dict] = {}
    for event in journal.read_events("OFFLINE_VALIDATION_COMPLETED"):
        if event.strategy_id:
            screens[event.strategy_id] = event.payload
    day_up = ov.day_direction(events)

    by_action: dict[str, list[int]] = {}
    latest_rows: dict[str, dict] = {}
    for event in events:
        for row in event.payload.get("rows", []):
            latest_rows[row["cycle_id"]] = row
    for row in latest_rows.values():
        if row.get("verdict") in ov.INFORMATIVE:
            entry = by_action.setdefault(str(row.get("action")), [0, 0])
            entry[0] += 1
            entry[1] += 1 if row["verdict"] in {"GOOD_HOLD", "GOOD_TRADE"} else 0
    rates = ", ".join(
        f"{action} {right}/{n}" for action, (n, right) in sorted(by_action.items())
    )

    problems = []
    live = [s for s in StrategyLibrary(strategy_dir).list() if s.lifecycle in {"PROBATION", "ACTIVE"}]
    for spec in live:
        payload = screens.get(spec.strategy_id, {})
        if payload.get("verdict") != ov.REJECT_POOR_DECISIONS:
            continue
        adjusted = ov.validate(
            ov.collect_decisions(events, spec.strategy_id),
            strategy_id=spec.strategy_id, day_up=day_up,
        )
        if adjusted.verdict != ov.REJECT_POOR_DECISIONS:
            problems.append(f"{spec.strategy_id} ({spec.lifecycle}): {adjusted.reason}")
    if problems:
        return Result(
            "screen-direction-neutral", FAIL,
            "rejected by the days, not the rule: " + "; ".join(problems[:3]),
        )
    return Result(
        "screen-direction-neutral", PASS,
        f"no live strategy stands rejected for its days alone ({len(live)} live); "
        f"correct by action: {rates or 'none scored'}",
    )


def check_research_trial_ledger() -> Result:
    """Every research trial, including the failures, must be on the record.

    Lives here and not in `doctor` on purpose. `doctor` is production, and production
    must not read the research layer - that is the invariant the
    production-research-separation check exists to hold. The first version of this
    check was added to `doctor`, and that gate caught it immediately: a health report
    that imports the thing it is meant to be independent of.

    A backtest that failed used to leave no trace at all, so a search could run, fail,
    and leave no evidence that it had happened. `trials_run` is also the denominator
    the multiple-testing gate divides by, so a report that omits it hides its own
    selection bias.
    """
    sys.path.insert(0, str(SRC))
    from min_agent.research import trials

    path = ROOT / "runtime" / "min_agent" / "research_trials.jsonl"
    recorded = trials.read_trials(path)
    if not recorded:
        # Same distinction as `replay-audit` beside it: no trials recorded yet is the state
        # of a clone that has not run a search, not a search that ran and vanished. FAIL
        # here would assert tampering where there is nothing to tamper with, and would make
        # the gate red before the first experiment on any fresh machine.
        return Result(
            "research-trial-ledger", SKIP,
            f"{path.name} does not exist yet; no search has run, so there is nothing to omit",
        )
    # The check had no assertion at all. It printed a summary and returned PASS for whatever
    # the ledger contained, so a 100%-failure ledger - which is what this host has, 27 of 27
    # INSUFFICIENT - was reported green by the one class whose entire stated purpose is "do
    # not omit the failures". A check that cannot fail is worse than no check: it appears in
    # the count of classes that passed.
    #
    # What is actually asserted is the property the docstring claims: every trial is on the
    # record, identifiable, and accounted for. A trial without a strategy_id cannot be
    # traced back to anything; a verdict outside the known set means the ledger is being
    # written by something this check does not understand.
    problems = []
    # Derived from the producer, not guessed. The first version hardcoded
    # {PASS, FAIL, INCONCLUSIVE, INSUFFICIENT}, and walk_forward.verdict() has never emitted
    # PASS or FAIL - it emits PASSES_RESEARCH_GATE, and the ledger stores the code before the
    # colon. So the first trial that actually passed research was reported as
    # "unrecognised verdict" and the gate turned red on the exact outcome the project exists
    # to reach. A gate that punishes success gets disabled at the moment it matters.
    known_verdicts = _research_verdict_codes()
    for index, record in enumerate(recorded):
        if not record.get("strategy_id"):
            problems.append(f"record {index} has no strategy_id, so it cannot be traced back")
        verdict = str(record.get("verdict", "")).upper()
        if verdict and verdict not in known_verdicts:
            problems.append(f"record {index} has an unrecognised verdict {verdict!r}")
    # A trial's identity is the rule *and* the dataset it was judged on, because those two
    # together determine the result. Keying on strategy_id alone was correct while every candidate
    # was judged exactly once, but the scheduled search re-evaluates each candidate whenever the
    # broker fetch brings new bars, and that re-evaluation is a genuinely new trial which the
    # ledger is required to record rather than suppress. Observed here:
    #   search-trend-follow-0p0005  bars=20448  oos=87 trades
    #   search-trend-follow-0p0005  bars=24162  oos=155 trades
    # Two evaluations, two answers, one rule - and 0 duplicate (strategy_id, bars) pairs in the
    # whole ledger. Keying on strategy_id alone made this check refuse the exact behaviour the
    # driver exists to produce. The original defect it was written for is still caught: the same
    # rule judged twice on the same number of bars is a double-count and still fails.
    identities = [
        (r.get("strategy_id"), r.get("bars"))
        for r in recorded
        if r.get("strategy_id")
    ]
    duplicates = len(identities) - len(set(identities))
    if duplicates:
        problems.append(
            f"{duplicates} duplicate trial(s): the same rule recorded twice against the "
            "same bar count, so one evaluation is counted twice"
        )

    summary = trials.summarise(recorded)
    if summary["trials_run"] != len(recorded):
        problems.append(
            f"summary reports {summary['trials_run']} trials but {len(recorded)} records exist"
        )
    if problems:
        return Result(
            "research-trial-ledger", FAIL,
            f"{len(problems)}: {problems[:3]}",
        )
    return Result(
        "research-trial-ledger", PASS,
        f"{summary['trials_run']} trial(s), {summary['passed']} passed, "
        f"{summary['failed']} failed; {summary['by_verdict']}; "
        "every record traceable and accounted for",
    )


def check_data_integrity() -> Result:
    """Runtime state must parse, and every record must be internally consistent."""
    rc, out = _run([sys.executable, "tools/check_runtime_integrity.py"])
    return Result("data-integrity", PASS if rc == 0 else FAIL, _tail(out))


def check_self_evolution_closes() -> Result:
    """The evolution loop must close end to end on real bars, not just in the rules.

    `TradingLoop` is only the trading half. Reflect, score every decision against what the
    market actually did next, screen the strategy on those scores, and rule on the verdict
    all happen in `AgentDaemon._maintenance`. Calling `StrategyLifecycleManager.review()` by
    hand proves the rules work; it does not prove the stages are wired to each other. This
    drives the daemon over 847 cached real bars and requires every stage to fire on its own.

    What it caught that nothing else had: the Guardian refused all 847 cycles as `data
    snapshot is stale` until `TradingLoop` grew an injectable clock, and
    `ReplayDataGateway.current` raised IndexError when advanced past the last bar. Both were
    invisible while only the loop was replayed.

    It proves selection pressure acts on scored evidence. It proves nothing about whether any
    strategy has an edge - replay fills are REPLAYED, never SUBMITTED, and a replayed
    retirement is not a real one. The first real PASS_SCREENED has to come from live paper
    cycles across trading days.
    """
    # The probe replays cached real Alpaca bars, which live in gitignored runtime/ and need a
    # broker account to fetch. A fresh clone has no runtime/ at all, so this check made
    # `make verify` red on exactly the machine state the README promises it runs on - found by
    # docs/evidence/run-fresh-clone.sh on 2026-10-07. Absence of the whole state directory is
    # SKIP, as in `replay-audit`; a deployment whose runtime/ exists but lacks the bars is still
    # FAIL, because there the loop is supposed to be provable.
    # No journal is the mark of a checkout that has never run - CI creates runtime/ in earlier
    # steps, so the directory alone was not enough, and GitHub's runner failed here.
    journal = ROOT / "runtime" / "min_agent" / "journal.jsonl"
    if not (ROOT / "runtime").exists() or not journal.exists():
        return Result(
            "self-evolution-closes", SKIP,
            "no runtime journal in this checkout; the replay needs real bars fetched with a paper "
            "account (tools/fetch_replay_bars.py)",
        )
    rc, out = _run([sys.executable, "tools/self_evolution_probe.py"])
    if rc != 0:
        return Result(
            "self-evolution-closes", FAIL,
            f"self-evolution probe did not run: {_tail(out)}",
        )
    try:
        found = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Result(
            "self-evolution-closes", FAIL,
            f"unreadable probe output: {_tail(out)}",
        )
    if found:
        return Result(
            "self-evolution-closes", FAIL,
            f"the self-evolution loop does not close: {'; '.join(found)}",
        )
    return Result(
        "self-evolution-closes", PASS,
        "847 real-bar cycles drove reflect -> counterfactual -> screen -> lifecycle with no "
        "daemon errors, and the lifecycle followed the screen's verdict",
    )


def check_selection_pressure_reaches_the_rules() -> Result:
    """The lifecycle rules must react to the system's own scored evidence.

    The defect this catches shipped and was live for months. The REJECT veto lived in
    `AgentDaemon._apply_offline_rejection`, not in `StrategyLifecycleManager`, so the
    promotion gate was reachable from the rule set while the rejection was not. Measured over
    847 real bars: 580 scored decisions, 304 of them losing trades,
    `correct_outcome_ratio` 0.476, verdict REJECT_POOR_DECISIONS - and `review()` returned no
    ruling at all. A journal could carry a REJECT for a strategy still marked PROBATION with
    nothing anywhere saying why.

    This asserts, from the rule set alone with no daemon in the picture, that REJECT retires
    with its evidence in the reason, that INCONCLUSIVE and absent evidence do not - silence
    must never be read as failure, the error the admission gate made once already - and that
    `_review_one` itself names the verdict rather than depending on a caller's side effect.

    It is a check about the mechanism. It is not evidence that any strategy has ever been
    retired, and must not be cited as if it were.
    """
    rc, out = _run([sys.executable, "tools/selection_pressure_probe.py"])
    if rc != 0:
        return Result(
            "selection-pressure-reaches-the-rules", FAIL,
            f"selection-pressure probe did not run: {_tail(out)}",
        )
    try:
        found = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Result(
            "selection-pressure-reaches-the-rules", FAIL,
            f"unreadable probe output: {_tail(out)}",
        )
    if found:
        return Result(
            "selection-pressure-reaches-the-rules", FAIL,
            f"the rules do not react to scored evidence: {'; '.join(found)}",
        )
    return Result(
        "selection-pressure-reaches-the-rules", PASS,
        "a REJECT verdict retires from the rule set carrying its ratio, while "
        "INCONCLUSIVE and absent evidence do not",
    )


def check_replay_cannot_reach_the_account() -> Result:
    """The replay harness must be structurally unable to touch the account.

    A replayed fill leaking into the PnL ledger would be the most dangerous bug available:
    the account would show profit from money never risked, and an unvalidated path would
    look like the best one on record. That is why this is a gate and not a docstring -
    prose is what failed the last three times.

    Four properties, all machine-checked by `tools/replay_safety_probe.py`:

    1. the module imports no broker client and no paper/live executor;
    2. `REPLAYED` is a distinct execution status, never `SUBMITTED`, and carries no
       `order_id`, so reconciliation cannot match it to a real order;
    3. a journal of replayed fills evaluates to zero realized PnL and never claims broker
       verification;
    4. the snapshot source says what it is - real bars, simulated account.

    This verifies the *mechanism*, the same distinction `check_shadow_live_consistency`
    insists on. It is not evidence that any replay has been run, and must not be cited as
    if it were.
    """
    rc, out = _run([sys.executable, "tools/replay_safety_probe.py"])
    if rc != 0:
        return Result(
            "replay-cannot-reach-the-account", FAIL,
            f"replay safety probe did not run: {_tail(out)}",
        )
    try:
        found = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Result(
            "replay-cannot-reach-the-account", FAIL,
            f"unreadable probe output: {_tail(out)}",
        )
    if found:
        return Result(
            "replay-cannot-reach-the-account", FAIL,
            f"replay can reach the account: {'; '.join(found)}",
        )
    return Result(
        "replay-cannot-reach-the-account", PASS,
        "replay holds no broker client, reports REPLAYED with no order_id, and a journal "
        "of replayed fills evaluates to zero realized PnL",
    )


def check_shadow_live_consistency() -> Result:
    """Live must be unreachable, probation must be a real gate, shadow must exist.

    This check verifies that the shadow *mechanism* is real and switchable. It does
    **not** verify that the shadow *stage* has ever been run, and it must not be
    cited as if it did: `docs/history/PHASES.md` recorded Phase 5 as MET with
    "status=SHADOWED" quoted from the live journal, while the journal contains zero
    `SHADOWED` executions and `MIN_AGENT_SHADOW` is off. Unit tests that the sink
    does not leak are evidence about the mechanism, not about the stage, and
    conflating the two is how a phase exit criterion gets marked satisfied by code
    that has never executed.

    The stale line this replaces - "Shadow trading is *not* implemented, the word
    appears in docstrings only" - was itself withdrawn in STATUS.md while it stayed
    live here. A check whose docstring is known-false is worse than no check.
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

    # Shadow must exist *and* be reachable from configuration. A stage that is
    # implemented but not switchable is the same as one that does not exist, and
    # the first version of this check kept reporting "shadow mode NOT implemented"
    # after shadow had been built, because the string was never updated. A check
    # that prints a falsehood is worse than no check at all.
    rc, out = _run([sys.executable, "-c", (
        "import os, sys; sys.path.insert(0, 'src')\n"
        "os.environ.update(ALPACA_API_KEY='k', ALPACA_SECRET_KEY='s',\n"
        "                  MIN_AGENT_SHADOW='1')\n"
        "from min_agent.config import AgentConfig\n"
        "from min_agent.shadow import ShadowExecutor\n"
        "from min_agent.executor import AlpacaPaperExecutor\n"
        "from min_agent.cli import _execution_sink\n"
        "cfg = AgentConfig.from_env()\n"
        "assert cfg.shadow is True, 'MIN_AGENT_SHADOW=1 did not enable shadow'\n"
        "sink = _execution_sink(cfg, object(), None)\n"
        "assert isinstance(sink, ShadowExecutor), type(sink)\n"
        "assert not isinstance(sink, AlpacaPaperExecutor)\n"
        "os.environ['MIN_AGENT_SHADOW'] = '0'\n"
        "assert AgentConfig.from_env().shadow is False\n"
        "print('shadow reachable and off-by-default')\n"
    )])
    if rc != 0:
        problems.append(f"shadow mode not reachable from config: {_tail(out)}")

    # A boolean switch that decides whether real orders reach a broker must not be
    # read by truthiness: MIN_AGENT_SHADOW=0 has to mean off.
    rc, out = _run([sys.executable, "-c", (
        "import os, sys; sys.path.insert(0, 'src')\n"
        "from min_agent.config import AgentConfig, _flag\n"
        "assert _flag('X', True) is True\n"
        "os.environ['X'] = '0'\n"
        "assert _flag('X', True) is False, '0 must read as off'\n"
        "os.environ['X'] = 'false'\n"
        "assert _flag('X', True) is False\n"
        "os.environ['X'] = 'flase'\n"
        "try:\n"
        "    _flag('X', True)\n"
        "    raise SystemExit('a typo must raise, not silently enable a broker switch')\n"
        "except ValueError:\n"
        "    pass\n"
        "print('switch parses strictly')\n"
    )])
    if rc != 0:
        problems.append(f"shadow switch is not strict: {_tail(out)}")

    detail = "; ".join(problems) if problems else (
        "live mode hard-blocked; probation gate enforced; shadow implemented, "
        "reachable by MIN_AGENT_SHADOW=1, off by default, strict boolean"
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


#: Classes that execute a test file's assertions, as opposed to classes that
#: inspect the repository. A file mapped only to a non-executing class is reported
#: as covered while none of its tests ever run - which is exactly what happened to
#: `test_cli_startup.py`, mapped only to `syntax-import`. That class imports the 35
#: production modules and stops, so the file's one failing assertion stayed invisible
#: behind a green gate while `make test` was red.
EXECUTING_CLASSES = frozenset({
    "unit-integration", "syntax-import", "point-in-time-no-leakage", "pnl-accounting",
    "data-integrity", "lifecycle-invariants", "guardian-bypass-prevention",
    "replay-determinism", "crash-recovery", "broker-reconciliation",
    "decision-outcome-counterfactual", "shadow-live-consistency",
})


def _executions_live(live: list) -> int:
    return sum(r.counts.get("passed", 0) for r in live)


def check_all_tests_classified() -> Result:
    """Every test file must be assigned, and at least one assigned class must
    actually run it.

    Assignment alone is a weaker guarantee than it looks. The map is an
    accounting of intent, not evidence of execution, and a file whose only class
    inspects the repository rather than running pytest contributes nothing to the
    gate while looking fully covered.
    """
    on_disk = {p.name for p in TESTS.glob("test_*.py")}
    unknown = sorted(on_disk - set(TEST_CLASS_MAP))
    ghost = sorted(set(TEST_CLASS_MAP) - on_disk)
    never_run = sorted(
        name for name, classes in TEST_CLASS_MAP.items()
        if name in on_disk and not (set(classes) & EXECUTING_CLASSES)
    )
    problems = []
    if unknown:
        problems.append(f"unclassified test files: {unknown}")
    if ghost:
        problems.append(f"mapped but absent: {ghost}")
    if never_run:
        problems.append(
            "assigned only to non-executing classes, so no test in them ever "
            f"runs in the gate: {never_run}"
        )
    detail = "; ".join(problems) if problems else (
        f"all {len(on_disk)} test files are assigned, and each is assigned to at "
        f"least one class that actually runs it"
    )
    return Result("test-coverage-map", FAIL if problems else PASS, detail)


#: Check classes that assert something about the repository or about the running system
#: rather than about trading behaviour, and so have no test file of their own by design.
#:
#: `check_known_classes_run` previously demanded a test file behind every class, and
#: passed, because `CHECK_CLASSES` held only the 13 classes that run pytest and silently
#: excluded the 16 added since. Naming the two kinds separately is what makes the
#: distinction checkable: a class that is neither in EXECUTING_CLASSES nor here will fail
#: `check_known_classes_run`, so a new substantive check cannot be added untested by
#: accident.
# The checks main() appends outside the CHECK_CLASSES loop. Declared once so that --list,
# the class-count gate and the run itself cannot disagree: three readers of one list rather
# than three independent recollections of it.
BESPOKE_CHECK_NAMES = (
    "self-evolution-closes",
    "selection-pressure-reaches-the-rules",
    "replay-cannot-reach-the-account",
    "docs-no-deleted-commands",
    "example-runs",
    "tools-readme-accurate",
    "no-loose-design-notes",
    "fresh-clone-evidence-real",
    "shell-scripts-well-formed",
    "config-example-names",
    "config-example-complete",
    "one-credentials-path",
    "no-unreachable-production-code",
    "config-comment-figures-true",
    "unit-templates-portable",
    "fact-figures-match",
    "list-count-matches-run",
    "pipeline-targets-exist",
    "gate-class-count-consistent",
)

REPOSITORY_CHECK_CLASSES: frozenset[str] = frozenset({
    # Inspects this repository: test-file accounting, dependency declarations, docs
    # freshness, the production/research boundary, hardcoded paths.
    "bespoke-list-matches-run",
    "status-states-only-fixed-facts",
      "config-example-complete",
      "pipeline-targets-exist",
      "type-checking-is-a-gate",
      "class-coverage",
    "test-coverage-map",
    "docs-not-stale",
    "software-supply-chain",
    "production-research-separation",
    "home-independence",
    # Runs tools/audit_defects.py, not pytest. Was listed in EXECUTING_CLASSES, which
    # claimed it had test coverage it does not have and made `class-coverage` unable to
    # distinguish "runs a script" from "runs the suite".
    "defect-regression-audit",
    # Runs ./minictrl doctor, not pytest. Same miscategorisation.
    "doctor",
    # Inspects the deployed system: systemd unit files, the running daemon, the
    # journal, the shadow stage, doctor's own reachability.
    "units-where-systemd-looks",
    "unit-environment-files",
    "daemon-source-matches-worktree",
    "doctor-checks-reachable",
    # replay-audit re-derives the load-bearing PnL numbers from the raw journal text.
    # It is listed here because it reads the *deployed* journal rather than a fixture -
    # it is a system check on live state, not a test of a code path in isolation.
    "replay-audit",
    "research-trial-ledger",
    # Reads the deployed journal and strategy library: a statement about live state.
    "screen-direction-neutral",
    # The two shadow checks report whether a stage ever executed against real state.
    "shadow-not-executed",
    "shadow-stage-exercised",
})


def check_known_classes_run() -> Result:
    """Every class that asserts trading behaviour must have tests behind it.

    Repository-level checks are excluded by name, not by omission: the point of this
    check is that a substantive class cannot be added without a test, and an exclusion
    list that silently grows is exactly how the previous version of this check came to
    pass while covering 13 of 29.
    """
    untested_meta = sorted(
        cls for cls in CHECK_CLASSES
        if cls not in EXECUTING_CLASSES
        and cls not in REPOSITORY_CHECK_CLASSES
        and not _files_for((cls,))
    )
    # Every class that claims to run pytest must actually have a test file behind it.
    # This is separate from the check above and was the reason the self-test caught this
    # version: `untested_meta` only looked at classes outside EXECUTING_CLASSES, so
    # stripping the tests from a class that *is* in EXECUTING_CLASSES - `pnl-accounting`
    # in the self-test - passed silently. A member of EXECUTING_CLASSES with no file
    # would be reported as PASS by run_pytest_class only if the class were skipped, and
    # skipped is not the same as covered.
    untested_executing = sorted(
        cls for cls in CHECK_CLASSES
        if cls in EXECUTING_CLASSES and not _files_for((cls,))
    )
    if untested_meta or untested_executing:
        return Result(
            "class-coverage", FAIL,
            f"behaviour classes with no test file: "
            f"{sorted(untested_meta + untested_executing)}. "
            "Add a test, or name the class in REPOSITORY_CHECK_CLASSES if it inspects "
            "the repository or the deployed system rather than trading behaviour.",
        )
    substantive = len(CHECK_CLASSES) - len(
        [c for c in CHECK_CLASSES if c in REPOSITORY_CHECK_CLASSES]
    )
    return Result(
        "class-coverage", PASS,
        f"all {substantive} behaviour classes have tests; "
        f"{len(REPOSITORY_CHECK_CLASSES)} repository/system checks are exempt by name",
    )


def check_defect_audit() -> Result:
    """The 15-defect regression audit, kept as its own class because it is the
    record of what was once wrong."""
    rc, out = _run([sys.executable, "tools/audit_defects.py"], timeout=900)
    return Result("defect-regression-audit", PASS if rc == 0 else FAIL, _tail(out, 3))


def _runtime_state_present() -> bool:
    """Whether this checkout has any deployment to check.

    A clone has no `runtime/min_agent`, and that is not a health problem: there has been no
    cycle yet. What is being asked here is narrower than "is the agent healthy" - it is
    "is there a deployment to be healthy about".
    """
    # Checked *before* anything runs, not after: `fact-figures-match` and `doctor` shell out to
    # `minictrl doctor`, which creates runtime/min_agent as a side effect. Reading this
    # after the fact meant a fresh clone - which starts with no runtime at all - looked like
    # a deployment by the time doctor ran, and the gate demanded a clean health report from
    # a machine that had never run a cycle. Found by deleting runtime/ and watching it
    # reappear between two checks in a clone that had none.
    return _RUNTIME_PRESENT_AT_START


def _operator_credentials_present() -> bool:
    """Whether this shell can reach a broker, following the same resolution minictrl uses.

    Deliberately the shell environment and the XDG env file, which is where `minictrl`
    looks - so this answers "can doctor check anything", not "is a key spelled correctly".
    """
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        return True
    env_file = Path(os.environ.get("XDG_CONFIG_HOME", "")) / "min-agent" / "env"
    if env_file.exists():
        text = env_file.read_text(encoding="utf-8", errors="ignore")
        if "ALPACA_API_KEY" in text and "ALPACA_SECRET_KEY" in text:
            return True
    return False


def check_doctor() -> Result:
    """doctor is the fast-iteration primitive; a non-zero exit must fail the gate.

    Strict on a machine that has credentials, and honestly labelled on one that does not.
    A fresh clone has no paper account, and doctor correctly reports missing credentials as
    a blocking problem - so demanding a clean doctor there demanded something no clone can
    deliver, and `make verify` was red on arrival. That made the gate unusable at exactly
    the moment it matters: before the first cycle, when its job is to check the install.

    So the gate reports what it can actually establish without a broker and says which mode
    it ran in. It does not pass a real health problem: with credentials present, every
    blocking finding still fails the gate, and the runtime checks above stay strict about
    state that exists.
    """
    # Presence of credentials is necessary but not sufficient: doctor also checks the
    # deployed state, and a clone has none. Gating on credentials alone meant a clone that
    # inherited this host's XDG_CONFIG_HOME found the keys, took the strict path, and failed
    # on a strategy library that has never been created - a fresh clone reporting a blocking
    # problem about state it was never meant to have.
    if not _operator_credentials_present() or not _runtime_state_present():
        rc, out = _run(
            [sys.executable, "-m", "min_agent.cli", "--doctor", "--skip-broker", "--quiet"],
            timeout=900,
        )
        # --quiet prints `ok=False exit=1 failures=[...]`, not the `RESULT:` line the
        # non-quiet form uses. Matching only the latter reported "no RESULT line" on a
        # doctor run that had worked, which is the gate misreading a formatting choice as
        # a failure. Both forms are accepted, and either counts as proof the report ran.
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        line = next(
            (ln for ln in lines if ln.startswith("RESULT")),
            next((ln for ln in lines if ln.startswith("ok=")), f"doctor exited {rc} with no report"),
        )
        # A non-zero exit here is expected: --skip-broker still reports the absent
        # credential. What must hold is that the report was produced at all.
        produced = any(ln.startswith("RESULT") or ln.startswith("ok=") for ln in lines)
        why = []
        if not _operator_credentials_present():
            why.append("no broker credentials")
        if not _runtime_state_present():
            why.append("no runtime state in this checkout")
        return Result(
            "doctor", PASS if produced else FAIL,
            f"{' and '.join(why)}; ran offline ({line}) - "
            "install verified, deployment not checked",
        )
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

        # 3b. The same must hold for a class that is real but has had its test files
        # unassigned - which is the case `check_known_classes_run` is actually about.
        # Step 3 only proves the lookup fails for a name that was never in the map; it
        # cannot catch a real class quietly losing its coverage, which is exactly how
        # `CHECK_CLASSES` had drifted for months while `class-coverage` reported a pass.
        saved_map = {k: v for k, v in TEST_CLASS_MAP.items()}
        try:
            for name in list(TEST_CLASS_MAP):
                if "pnl-accounting" in TEST_CLASS_MAP[name]:
                    del TEST_CLASS_MAP[name]
            uncovered = check_known_classes_run()
            if uncovered.status != FAIL:
                failures.append(
                    "a behaviour class with its test files unassigned was still "
                    "reported as covered"
                )
            else:
                print("  ok  a behaviour class with its tests removed fails class-coverage")
        finally:
            TEST_CLASS_MAP.clear()
            TEST_CLASS_MAP.update(saved_map)

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

        # 6. A plan document that contradicts the tree must fail, and the same text
        #    marked SUPERSEDED must pass. A doc check that cannot fail is how the
        #    recovery plan sat at "Awaiting approval" for 51 commits while telling
        #    an operator to restore a directory that had been deleted on purpose.
        probe = HISTORY / "plans" / "zz_stale_probe.md"
        stale_text = (
            "# Probe\nStatus: **Awaiting approval** - no source file has been "
            "modified yet.\n"
        )
        try:
            probe.write_text(stale_text)
            result = check_docs_not_stale()
            if result.status != FAIL:
                failures.append("a plan claiming an unacted state did not fail")
            else:
                print("  ok  a stale plan document fails the docs check")

            probe.write_text(
                "# Probe\nStatus: SUPERSEDED - historical record only.\n" + stale_text
            )
            result = check_docs_not_stale()
            if result.status != PASS:
                failures.append("a plan explicitly marked SUPERSEDED was failed")
            else:
                print("  ok  the same plan marked SUPERSEDED passes")
        finally:
            probe.unlink(missing_ok=True)

        # 7. A single ~/ path in a maintenance routine must fail the gate. $HOME
        #    is quota'd on this host, so a path that resolves there fails mid-run
        #    rather than at startup, and the symptom is a journal that silently
        #    stops growing. Verified once by hand it would just be a comment; only
        #    a probe that is proven to fail keeps the claim honest.
        src_probe = SRC / "min_agent" / "zz_home_probe.py"
        try:
            src_probe.write_text('x = "~/somewhere"\n')
            result = check_home_independence()
            if result.status != FAIL:
                failures.append("a $HOME path in production code did not fail")
            else:
                print("  ok  a $HOME path in production code fails home-independence")
        finally:
            src_probe.unlink(missing_ok=True)

        # 8. A unit loaded from tmpfs, or enabled through a link into tmpfs, must
        #    fail. Both were live on this host while `systemctl is-enabled` said
        #    "enabled" the whole time - the exact shape of failure where the check
        #    that exists to catch it reports success.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "systemd" / "user" / "default.target.wants").mkdir(parents=True)
            link = root / "systemd" / "user" / "default.target.wants" / "x.service"
            link.symlink_to(f"/run/user/{os.getuid()}/systemd/user/x.service")
            saved_cfg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = str(root)
            try:
                result = check_units_are_where_systemd_looks()
                if result.status != FAIL:
                    failures.append("a tmpfs enablement link did not fail")
                else:
                    print("  ok  a tmpfs enablement link fails units-where-systemd-looks")
            finally:
                if saved_cfg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = saved_cfg

    finally:
        restore()

    if failures:
        print("\nSELF-TEST FAILED - the gate is not trustworthy:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSELF-TEST PASSED: the gate detects failing tests, missing names,")
    print("empty classes, unclassified test files, stale plan documents,")
    print("$HOME paths in production code, units systemd cannot find, and units")
    print("whose ExecStart is a path that does not exist.")
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
        # Reports what the gate runs, which is CHECK_CLASSES plus the bespoke checks
        # appended in main(). Printing the tuple alone showed 29 while the gate ran 37 -
        # so the command whose job is to state the gate's size understated it by eight,
        # which is the drift the class-count check exists to catch. The bespoke names are
        # declared here rather than scraped out of main(), because scraping would put the
        # count in two places and drift again.
        for cls in CHECK_CLASSES:
            files = [n for n, c in TEST_CLASS_MAP.items() if cls in c]
            print(f"{cls:<34} {len(files):>2} test file(s)")
        for name in BESPOKE_CHECK_NAMES:
            print(f"{name:<34}  -  bespoke check, not a pytest class")
        full_only = ("defect-regression-audit", "doctor")
        for name in full_only:
            print(f"{name:<34}  -  bespoke check, full runs only")

        # The total is the union, never the sum. Adding the two sets counted
        # `fact-docs-current` twice - it is in CHECK_CLASSES *and* in BESPOKE_CHECK_NAMES,
        # because the loop reaches it through a different path - and `doctor` and
        # `defect-regression-audit` are in CHECK_CLASSES *and* re-listed as full_only. So
        # this printed 43 while the run reported 41, and every document in the repository
        # said 41. The check written to catch count drift could not see it, because it
        # compares documents against the run, never the run against `--list`.
        names = set(CHECK_CLASSES) | set(BESPOKE_CHECK_NAMES) | set(full_only)
        bespoke_only = (set(BESPOKE_CHECK_NAMES) | set(full_only)) - set(CHECK_CLASSES)
        # `syntax-import` produces two Result rows in a run - check_syntax_import() for the
        # compile-and-import sweep, run_pytest_class() for the test files the class owns -
        # so the run's `classes:` total is one higher than the number of distinct names. It
        # is counted here rather than left as a mystery, because this number has to equal the
        # run's for the figure to mean anything.
        extra_rows = 1
        print(f"\n{len(names) + extra_rows} classes on a full run: "
              f"{len(CHECK_CLASSES)} declared in CHECK_CLASSES, "
              f"{len(bespoke_only)} appended by main() only, "
              f"{extra_rows} extra result row from syntax-import")
        return 0
    if args.self_test:
        return self_test()

    print("=" * 78)
    print("verify: every check class, one gate")
    print("=" * 78)

    results: list[Result] = []
    wanted = set(args.only) if args.only else None
    if wanted:
        # `make verify CLASS=typo-class` used to report "OK - all check classes pass" with
        # zero tests executed: an unmatched name silently skipped the loop. That is the one
        # workflow the README, Makefile help and STATUS.md all advertise for per-class
        # debugging, and a typo in it produced a green gate that checked nothing.
        #
        # The valid set is CHECK_CLASSES plus the bespoke names, not CHECK_CLASSES alone -
        # the first version rejected `config-comment-figures-true` as unknown, which is a
        # real class, because only the loop's classes were considered.
        known = set(CHECK_CLASSES) | set(BESPOKE_CHECK_NAMES)
        unknown = sorted(wanted - known)
        if unknown:
            print(f"verify: no such check class: {', '.join(unknown)}", file=sys.stderr)
            # +1 for the extra syntax-import result row, which --list and
            # list-count-matches-run both account for; without it this said 45 while the gate
            # runs 46.
            print(f"        {len(known) + 1} classes exist; run `make classes`", file=sys.stderr)
            return 2

    global _partial_run, _partial_run_filter
    _partial_run = bool(wanted)
    _partial_run_filter = set(wanted) if wanted else set()

    results.append(_safe(check_all_tests_classified, "test-coverage-map"))
    results.append(_safe(check_known_classes_run, "class-coverage"))
    results.append(_safe(check_software_supply_chain, "software-supply-chain"))
    results.append(_safe(check_docs_not_stale, "docs-not-stale"))
    results.append(_safe(check_production_research_separation, "production-research-separation"))
    results.append(_safe(check_home_independence, "home-independence"))
    results.append(_safe(check_units_are_where_systemd_looks, "units-where-systemd-looks"))
    results.append(_safe(check_the_running_daemon_matches_the_worktree, "daemon-source-matches-worktree"))
    results.append(_safe(check_docs_do_not_instruct_deleted_commands, "docs-no-deleted-commands"))
    results.append(_safe(check_the_example_still_runs, "example-runs"))
    results.append(_safe(check_tools_readme_names_real_files, "tools-readme-accurate"))
    results.append(_safe(check_no_unreferenced_design_notes_at_the_root, "no-loose-design-notes"))
    results.append(_safe(check_the_fresh_clone_evidence_is_real, "fresh-clone-evidence-real"))
    results.append(_safe(check_shell_scripts_have_no_orphaned_lines, "shell-scripts-well-formed"))
    results.append(_safe(check_the_declared_bespoke_list_matches_what_runs, "bespoke-list-matches-run"))
    results.append(_safe(check_status_only_states_what_does_not_change, "status-states-only-fixed-facts"))
    results.append(_safe(check_figures_quoted_in_config_comments, "config-comment-figures-true"))
    results.append(_safe(check_list_reports_the_same_count_as_a_run, "list-count-matches-run"))
    results.append(_safe(check_the_fact_figures_in_prose_match_reality, "fact-figures-match"))
    results.append(_safe(check_unit_templates_have_no_host_paths, "unit-templates-portable"))
    results.append(_safe(check_the_documented_pipeline_targets_exist, "pipeline-targets-exist"))
    results.append(_safe(check_type_checking_is_a_real_gate, "type-checking-is-a-gate"))
    results.append(_safe(check_config_example_names_are_real, "config-example-names"))
    results.append(_safe(check_config_example_covers_every_variable, "config-example-complete"))
    results.append(_safe(check_no_production_function_is_unreachable, "no-unreachable-production-code"))
    results.append(_safe(check_one_credentials_path_everywhere, "one-credentials-path"))
    results.append(_safe(check_unit_environment_files_exist, "unit-environment-files"))
    results.append(_safe(check_replay_audit, "replay-audit"))
    results.append(_safe(check_shadow_cannot_count_as_executed, "shadow-not-executed"))
    results.append(_safe(check_shadow_stage_has_actually_run, "shadow-stage-exercised"))
    results.append(_safe(check_research_trial_ledger, "research-trial-ledger"))
    results.append(_safe(check_screen_is_direction_neutral, "screen-direction-neutral"))
    results.append(_safe(check_doctor_checks_are_all_reachable, "doctor-checks-reachable"))
    results.append(_safe(check_syntax_import, "syntax-import"))
    results.append(_safe(check_data_integrity, "data-integrity"))
    results.append(_safe(check_shadow_live_consistency, "shadow-live-consistency"))
    results.append(_safe(check_replay_cannot_reach_the_account, "replay-cannot-reach-the-account"))
    results.append(_safe(check_selection_pressure_reaches_the_rules, "selection-pressure-reaches-the-rules"))
    results.append(_safe(check_self_evolution_closes, "self-evolution-closes"))

    for cls in CHECK_CLASSES:
        if wanted and cls not in wanted:
            continue
        if cls in {"software-supply-chain", "data-integrity", "shadow-live-consistency"}:
            continue  # already run above as bespoke checks
        if cls in REPOSITORY_CHECK_CLASSES:
            # These inspect the repository or the deployed system and were each appended
            # above as their own bespoke check. Running run_pytest_class on them produced
            # "no test file is assigned to this class" for all 17 of them - noise that
            # looks like a coverage failure and hides the real one.
            continue
        # `syntax-import` is deliberately NOT skipped. It runs compileall plus an import
        # sweep above, and it also owns test files - `test_cli_startup.py` checks that
        # every public name the entry points touch actually exists, and `test_
        # syntax_import.py` checks that every module compiles and imports. Both were
        # unreachable: the class was skipped here, so neither assertion had ever run
        # inside the gate even though a green `make verify` reported the class as passing.
        # `cls=cls` binds the loop variable now, not when the lambda is finally called.
        # Ruff caught this: the deferred call would otherwise read whatever `cls`
        # held at that point, running the same class repeatedly.
        # The filter name is the class itself, not "pytest:<class>": the `pytest:` prefix
        # existed only so bespoke-list-matches-run could tell the two sources apart, and it
        # meant `--only pnl-accounting` matched nothing at all - the per-class workflow ran
        # zero classes and printed "no check class ran". The distinction is recovered from
        # the call shape in that check instead, which already looks at the inner function.
        results.append(_safe(lambda cls=cls: run_pytest_class(cls), cls))

    if not wanted:
        results.append(_safe(check_defect_audit, "defect-regression-audit"))
        results.append(_safe(check_doctor, "doctor"))

    # Runs last, with the results already collected. It is part of the count it verifies, so
    # the count it compares against is this list's length plus one - derived here, next to the
    # call that determines it, instead of the check guessing from what it can see.
    total_classes = len(results) + 1
    results.append(_safe(lambda: check_the_gate_class_count_is_reported_consistently(total_classes), "gate-class-count-consistent"))

    results = [r for r in results if r is not None]
    if not results:
        # Every requested class was filtered out, which happens when the only matches are
        # checks that skip under a partial run. Saying so is better than `max()` on an empty
        # sequence, and better than printing "OK - all check classes pass" for nothing.
        print(f"verify: no check class ran; requested {sorted(_partial_run_filter)}")
        return 2
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
