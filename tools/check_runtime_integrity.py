#!/usr/bin/env python
"""Runtime state must parse, and the journal and the registry must agree.

A journal that cannot be re-read is not a record, it is a log the system cannot
learn from, and it fails silently: the daemon restarts happily and simply stops
remembering. More dangerous is the case this checks hardest - the journal and the
strategy registry are two sources of truth about the same thing, and when they
disagree the system has a history nobody can audit.

`doctor` needs a real journal to be interesting; this must work either way.

Every rule below was written by getting it *wrong* first. The first three
versions of this file reported failures that did not exist:

* it invented `entry_rules`/`exit_rules`/`risk_rules`, which `StrategySpec` has
  never had - the schema is `kind` + `parameters`;
* it treated a journal *event* carrying a `cycle_id` foreign key as a duplicate
  cycle, so 228 perfectly normal cycles looked like double-writes;
* it required `parameters` to be non-empty, but a `HOLD_BASELINE` legitimately
  has none.

A checker that cries wolf is worse than no checker, so each rule now names the
model field or the schema it is derived from.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RUNTIME = ROOT / "runtime" / "min_agent"
FINDINGS = ROOT / "docs" / "superpowers" / "known_state_findings.json"

#: From models.StrategyLifecycle. Getting this wrong flagged three healthy
#: strategies as having an unknown lifecycle.
LIFECYCLES = {"PROBATION", "ACTIVE", "PAUSED", "RETIRED", "BASELINE"}

#: From models.StrategySpec: every field the selector and the Guardian read.
REQUIRED_SPEC_FIELDS = (
    "strategy_id", "name", "kind", "symbols", "parameters",
    "max_position_value", "lifecycle", "created_at", "rationale",
)


def _is_cycle(record: dict) -> bool:
    """A cycle record, not an event.

    Events carry `cycle_id` as a foreign key - a reflection or an evaluation is
    *about* a cycle. Treating those as cycles made every cycle look duplicated.
    """
    return bool(record.get("cycle_id")) and "snapshot" in record and "decision" in record


def _read_lines(path: Path) -> tuple[list[dict], list[str]]:
    """(records, unreadable_line_count). A torn final line is tolerated: a crash
    mid-append is expected, and the journal is designed to survive it."""
    if not path.exists():
        return [], 0
    records: list[dict] = []
    torn = 0
    lines = path.read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                torn += 1
    return records, torn


def main(runtime: Path | None = None, findings: Path | None = None) -> int:
    runtime = runtime or RUNTIME
    findings = findings or FINDINGS
    problems: list[str] = []
    notes: list[str] = []
    journal_path = runtime / "journal.jsonl"
    records, torn = _read_lines(journal_path)
    cycles = [r for r in records if _is_cycle(r)]
    events = [r for r in records if not _is_cycle(r) and "event_type" in r]
    if torn:
        problems.append(f"{torn} unreadable journal line(s) before the end")
    if not journal_path.exists():
        notes.append("no journal yet")

    # 1. A cycle must appear exactly once. It is the unit every aggregate is
    #    built from, so a double-write double-counts the decision.
    seen: dict[str, int] = {}
    for record in cycles:
        seen[record["cycle_id"]] = seen.get(record["cycle_id"], 0) + 1
    dupes = sorted(c for c, n in seen.items() if n > 1)
    if dupes:
        problems.append(
            f"{len(dupes)} cycle record(s) written more than once: {dupes[:5]}"
        )

    # 2. Every strategy file must satisfy the schema the selector actually reads.
    strategies_dir = runtime / "strategies"
    declared: dict[str, dict] = {}
    if strategies_dir.exists():
        for path in sorted(strategies_dir.glob("*.json")):
            try:
                spec = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                problems.append(f"{path.name} is not valid JSON: {exc.msg}")
                continue
            missing = [f for f in REQUIRED_SPEC_FIELDS if f not in spec]
            if missing:
                problems.append(f"{path.name} is missing {missing}")
                continue
            if spec["lifecycle"] not in LIFECYCLES:
                problems.append(
                    f"{path.name} has lifecycle {spec['lifecycle']!r}, not in "
                    f"StrategyLifecycle {sorted(LIFECYCLES)}"
                )
            if not spec["max_position_value"]:
                problems.append(
                    f"{path.name} has max_position_value="
                    f"{spec['max_position_value']!r}, so nothing bounds it"
                )
            if str(spec["created_at"])[:4] == "2025":
                problems.append(
                    f"{path.name} claims created_at {spec['created_at']!r}; "
                    "system-owned fields must not be LLM-authored"
                )
            declared[spec["strategy_id"]] = spec
    else:
        notes.append("no strategy registry yet")

    # 3. The journal and the registry must agree about who exists.
    #
    #    A strategy that was *admitted* must have a file: `admit()` writes the
    #    file atomically before it returns accepted, so this must hold going
    #    forward. A strategy that was only *proposed and rejected* legitimately
    #    has no file - the first version of this check flagged those five as
    #    missing, which was the check being wrong, not the data.
    admitted: set[str] = set()
    rejected: set[str] = set()
    for record in events:
        if record.get("event_type") != "STRATEGY_ADMISSION_REVIEWED":
            continue
        sid = record.get("strategy_id")
        if not sid:
            continue
        (admitted if record.get("payload", {}).get("accepted") else rejected).add(sid)

    retired: set[str] = {
        r["strategy_id"] for r in events
        if r.get("event_type") == "STRATEGY_LIFECYCLE_UPDATED"
        and r.get("payload", {}).get("new_lifecycle") == "RETIRED"
        and r.get("strategy_id")
    }

    known: set[str] = set()
    if findings.exists():
        try:
            for entry in json.loads(findings.read_text()).get("known_missing", []):
                known.add(entry["strategy_id"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            problems.append(f"{findings.name} is malformed: {exc}")

    absent = sorted((admitted - retired) - set(declared) - known)
    if absent:
        problems.append(
            f"admitted strategies with no registry file and no recorded "
            f"retirement: {absent}"
        )
    if known:
        notes.append(
            f"{len(known)} previously-missing strategies recorded in "
            f"{findings.name} (see that file for why each is excused)"
        )
    if declared:
        notes.append(
            f"{len(declared)} strategies declared, {len(admitted)} admitted, "
            f"{len(rejected)} rejected at admission, {len(retired)} retired"
        )

    # 4. The knowledge library must not collapse into near-duplicates - the same
    #    zoo pathology the strategy registry had.
    knowledge = runtime / "knowledge" / "artifacts.jsonl"
    krecords, ktorn = _read_lines(knowledge)
    if ktorn:
        problems.append(f"{ktorn} unreadable knowledge line(s) before the end")
    bodies: dict[str, int] = {}
    for record in krecords:
        body = " ".join(
            str(record.get(k, ""))
            for k in ("claim", "statement", "summary", "text")
        ).strip().lower()
        if body:
            bodies[body] = bodies.get(body, 0) + 1
    repeats = [b for b, n in bodies.items() if n > 1]
    if repeats:
        problems.append(f"{len(repeats)} duplicated knowledge artifact(s)")
    if bodies:
        notes.append(
            f"{sum(bodies.values())} knowledge artifacts, {len(bodies)} distinct"
        )

    # 5. No state file may be half-written. A torn write is how a strategy
    #    silently disappears from the selector.
    for state in sorted(runtime.rglob("*.json")):
        try:
            json.loads(state.read_text())
        except json.JSONDecodeError as exc:
            # relative_to(ROOT) raises when the runtime tree is outside the repo,
            # which it is under a test tmpdir - and a checker that crashes is a
            # checker that silently passes nothing.
            try:
                shown = state.relative_to(ROOT)
            except ValueError:
                shown = state
            problems.append(f"{shown} is corrupt: {exc.msg}")

    if problems:
        print(f"runtime integrity: FAIL ({len(problems)} problem(s))")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(
        f"runtime integrity: OK ({len(cycles)} cycles, {len(events)} events, "
        f"{len(declared)} strategies)"
    )
    for note in notes:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
