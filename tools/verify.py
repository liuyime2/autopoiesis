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
    "test_knowledge_prune.py": (
        "data-integrity", "unit-integration",
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
    "test_research_trials.py": (
        "lifecycle-invariants", "data-integrity", "unit-integration",
    ),
    "test_research_backtest.py": (
        "point-in-time-no-leakage", "pnl-accounting", "lifecycle-invariants",
    ),
    "test_shadow.py": (
        "shadow-live-consistency", "guardian-bypass-prevention",
        "pnl-accounting", "broker-reconciliation",
    ),
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
    "doctor-checks-reachable",
    "test-coverage-map",
    "defect-regression-audit",
    "doctor",
    "fact-docs-current",
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


#: Phrases that assert a plan is not yet acted on. Any of these appearing in a
#: plan document means the document is describing a tree that no longer exists.
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
    plans = sorted((ROOT / "docs" / "superpowers" / "plans").glob("*.md"))
    if not plans:
        return Result("docs-not-stale", SKIP, "no plan documents to check")

    problems: list[str] = []
    for plan in plans:
        text = plan.read_text(errors="ignore").lower()
        stale = [c for c in STALE_STATUS_CLAIMS if c in text]
        if not stale:
            continue
        if "superseded" in text[:2000]:
            continue  # explicitly marked as a historical record
        problems.append(
            f"{plan.relative_to(ROOT)} still claims {stale[0]!r} and is not "
            "marked SUPERSEDED"
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
    targets += [ROOT / "tools" / "minictrl"] if (ROOT / "tools" / "minictrl").exists() else []
    for path in targets:
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if any(b in code for b in HOME_BINDS):
                violations.append(f"{path.name}:{line_no}: {line.strip()[:80]}")

    # The XDG roots the system actually reads must not point back into $HOME.
    home = str(Path.home())
    xdg = []
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(var)
        if not value:
            continue
        xdg.append(f"{var}={'INSIDE $HOME' if value.startswith(home) else 'outside'}")
        if value.startswith(home):
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


def check_config_example_names_are_real() -> Result:
    """Every variable in configs/paper.env.example must exist in the codebase.

    A configuration example is read by whoever is setting the system up, and a name that
    nothing reads is a setting they will change in the belief it took effect. The names
    are checked against config.py's own env reads plus the deployed env file's keys,
    which together are where a variable can legitimately come from.
    """
    example = ROOT / "configs" / "paper.env.example"
    if not example.exists():
        return Result("config-example-names", FAIL, "configs/paper.env.example is missing")
    names = set(re.findall(r"^(MIN_AGENT_[A-Z_]+|ALPACA_[A-Z_]+|OLLAMA_BASE_URL)=", example.read_text(), re.M))
    if not names:
        return Result("config-example-names", FAIL, "no variable names found in the example")
    known = (ROOT / "src" / "min_agent" / "config.py").read_text(encoding="utf-8")
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
        f"all {len(names)} variable names in the example are read by config.py or the env file",
    )


def check_docs_do_not_instruct_deleted_commands() -> Result:
    """No document may tell a reader to run a command that no longer exists.

    Found by the completion verifier, after the deliverables were already written and
    every target was green. `AUTO_REVIEW_README.md` documented `auto-fix.sh`,
    `check-market-open.sh` and `monitor.sh`; the two runbooks told a reader to
    `bash run_forever.sh`. Every one of those files was deleted as a duplicate, and
    nothing failed, because no check connected the documentation to the filesystem.

    A document that names a script is an executable instruction whether or not anyone
    runs it, and an instruction that cannot work is worse than a missing one: the reader
    concludes the tool is broken rather than that it moved. `docs/MIGRATION.md` is
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
            "Point them at the replacement in docs/MIGRATION.md.",
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

    return Result(
        "units-where-systemd-looks",
        FAIL if problems else PASS,
        "; ".join(problems[:4]) if problems else
        "every --user unit loads from a durable path and is enabled without a tmpfs link",
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
    journal = ROOT / "runtime" / "min_agent" / "journal.jsonl"
    if not journal.exists():
        return Result("shadow-stage-exercised", SKIP, "no journal to inspect")
    shadowed = 0
    intents = 0
    with journal.open(encoding="utf-8") as handle:
        for line in handle:
            if "SHADOWED" in line:
                shadowed += 1
            if "SHADOW_ORDER_INTENT" in line:
                intents += 1
    if shadowed:
        return Result(
            "shadow-stage-exercised", PASS,
            f"{shadowed} shadowed execution(s) on record across {intents} intent(s)",
        )
    return Result(
        "shadow-stage-exercised", WARN,
        f"the shadow mechanism is verified but the stage has never run: "
        f"{shadowed} shadowed execution(s), {intents} intent(s) on record",
        "MIN_AGENT_SHADOW is off; Phase 5 cannot be called MET on the strength of "
        "mechanism tests alone",
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
        return Result(
            "research-trial-ledger", FAIL,
            f"no research trial is recorded in {path.name}; a failed search would "
            "leave no evidence that it ran",
        )
    summary = trials.summarise(recorded)
    return Result(
        "research-trial-ledger", PASS,
        f"{summary['trials_run']} trial(s), {summary['passed']} passed, "
        f"{summary['failed']} failed; {summary['by_verdict']}",
    )


def check_data_integrity() -> Result:
    """Runtime state must parse, and every record must be internally consistent."""
    rc, out = _run([sys.executable, "tools/check_runtime_integrity.py"])
    return Result("data-integrity", PASS if rc == 0 else FAIL, _tail(out))


def check_shadow_live_consistency() -> Result:
    """Live must be unreachable, probation must be a real gate, shadow must exist.

    This check verifies that the shadow *mechanism* is real and switchable. It does
    **not** verify that the shadow *stage* has ever been run, and it must not be
    cited as if it did: `docs/superpowers/PHASES.md` recorded Phase 5 as MET with
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


def check_fact_docs_match_the_live_gate(live: list | None = None) -> Result:
    """The current-state blocks in the fact-level docs must match this run's gate.

    The independent verifier rejected a completion claim twice over documentation that
    had drifted: `PHASES.md` carried 1174 test executions against the gate's 1182, and
    `SYSTEM_AUDIT.md` section 49 quoted 26 classes and 1175 while the gate ran 27 and
    1182. Prose that reports its own verification results is a claim about the build,
    and if it is not checked it is a claim that decays silently.

    Scoped deliberately. Sections 17 onward record what the gate said *when that work
    was done* - 1111 or 1140 executions are correct for their moments - so only the
    blocks that describe the *present* are compared: the generated header and summary
    line in `PHASES.md`, section 49 of `SYSTEM_AUDIT`, and the `make verify` row in
    `STATUS.md`.
    """
    # The live summary is passed in rather than re-derived by spawning this same
    # script. The first version did spawn it, which re-entered this very check and
    # recursed until the command was killed - a gate that hangs the gate is worse
    # than no gate, and the recursion was mine.
    if not live:
        return Result("fact-docs-current", SKIP, "no live summary supplied")
    # A partial run (--only, or a hand-picked class list) reports a class and
    # execution count that describes the subset, not the build. The documents state
    # full-run figures, so comparing them to a subset is meaningless - and it made the
    # gate fail its own healthy neighbours whenever someone checked one class, which
    # teaches people to distrust the gate instead of reading it.
    if _partial_run:
        return Result(
            "fact-docs-current", SKIP,
            "partial run: this gate compares against full-run figures, which this "
            "run does not represent",
        )
    # +1 for this check, which appends itself to `results` immediately after being
    # called. Without it the gate under-reports the class count by one and the
    # documents then "match" a number that is one behind the run that produced it -
    # a green gate certifying a stale figure, which is the exact failure it was
    # written to prevent.
    classes = str(len(live) + 1)
    failed = str(sum(1 for r in live if r.status == FAIL))
    executions = str(sum(r.counts.get("passed", 0) for r in live))
    files = str(len({p.name for p in TESTS.glob("test_*.py")}))
    current = (
        f"{classes} classes, {failed} failed, {executions} test executions across {files} files"
    )

    problems: list[str] = []
    targets = {
        "SYSTEM_AUDIT.md §49": (ROOT / "docs" / "superpowers" / "SYSTEM_AUDIT.md",
                                "**Reviewed and confirmed.**"),
        "PHASES.md header": (ROOT / "docs" / "superpowers" / "PHASES.md",
                             "`make verify` — **"),
        "PHASES.md summary row": (ROOT / "docs" / "superpowers" / "PHASES.md",
                                  "| `make verify`, "),
    }
    for label, (path, anchor) in targets.items():
        if not path.exists():
            problems.append(f"{label}: file absent")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not line.startswith(anchor) and anchor not in line:
                continue
            window = " ".join(lines[i : i + 2])
            if f"{classes} classes" not in window or f"{executions} test executions" not in window:
                problems.append(f"{label} does not state {current}")
            break
        else:
            problems.append(f"{label}: anchor not found")
    if problems:
        return Result(
            "fact-docs-current", FAIL,
            "; ".join(problems[:3]),
            "a document that reports the build's own results must be checked against "
            "the build, or it decays into a claim nobody re-verified",
        )
    return Result(
        "fact-docs-current", PASS,
        f"the current-state blocks in the fact-level docs all state {current}",
    )


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
REPOSITORY_CHECK_CLASSES: frozenset[str] = frozenset({
    # Inspects this repository: test-file accounting, dependency declarations, docs
    # freshness, the production/research boundary, hardcoded paths.
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
    "fact-docs-current",
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
        probe = ROOT / "docs" / "superpowers" / "plans" / "zz_stale_probe.md"
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
    print("$HOME paths in production code, and units systemd cannot find.")
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
    results.append(check_docs_not_stale())
    results.append(check_production_research_separation())
    results.append(check_home_independence())
    results.append(check_units_are_where_systemd_looks())
    results.append(check_the_running_daemon_matches_the_worktree())
    results.append(check_docs_do_not_instruct_deleted_commands())
    results.append(check_the_example_still_runs())
    results.append(check_config_example_names_are_real())
    results.append(check_unit_environment_files_exist())
    results.append(check_replay_audit())
    results.append(check_shadow_cannot_count_as_executed())
    results.append(check_shadow_stage_has_actually_run())
    results.append(check_research_trial_ledger())
    results.append(check_doctor_checks_are_all_reachable())
    results.append(check_syntax_import())
    results.append(check_data_integrity())
    results.append(check_shadow_live_consistency())

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
        results.append(run_pytest_class(cls))

    global _partial_run
    _partial_run = bool(wanted)
    if not wanted:
        results.append(check_defect_audit())
        results.append(check_doctor())

    # Runs last, with the results already collected: the fact-level documents state
    # this run's own class and execution counts, so the check that keeps them honest
    # has to read those counts rather than spawn a second copy of this script.
    results.append(check_fact_docs_match_the_live_gate(results))

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
