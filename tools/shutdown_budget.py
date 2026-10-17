"""Derive the service's stop budget from the stages that can be in flight at SIGTERM.

What this is for. The daemon has been SIGKILLed by systemd twice: `TimeoutStopSec=30` and a
curriculum generation measured at 141.9 seconds, so the stop window was already 4.73x too small
before anything else went wrong. The diagnostic that found it could not state a correct budget,
because broker HTTP had no timeout at all and the daemon's maximum blocking duration was therefore
unknown. The broker layer is now bounded; what is still missing is the number.

A budget read off the wall is a budget nobody can check. So the stages are enumerated here with the
timeout each one declares, the maximum is computed, a stated buffer is added, and the result is
compared with what the unit template actually sets. The unit template does not carry the number - it
carries a marker this tool substitutes and checks, so a change to a stage timeout that outgrows the
stop window fails the gate rather than the daemon.

**What is bounded and what is not.** A stage with a declared timeout is bounded. A stage with no
network call and no subprocess is bounded by the work it does, which for a journal write is a file
size and for a validation is a record - both of which this tool reports as unbounded-in-principle
rather than pretending to measure them. The derived budget is therefore a *lower bound* on the
daemon's real stop time, and the tool says so rather than presenting it as exact. That distinction is
the difference between a budget and a guess that has been formatted as a budget.

Nothing here measures anything. Every number is read from the source that declares it, so the tool is
reproducible offline and cannot be right on one machine and wrong on another.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "autopoiesis"
UNIT_TEMPLATE = ROOT / "tools" / "autopoiesis.service.in"

#: Where the derived budget is written in the unit template. The template does not carry the number:
#: it carries this marker, so a stage timeout that outgrows the stop window changes the derived
#: budget and leaves the marker stale - which is a detectable difference rather than a silent one.
BUDGET_MARKER = "@STOP_BUDGET_SECONDS@"
#: Fallback used only when the marker is absent, so the tool can still compare against something.
DEFAULT_BUDGET = 30

#: Added to the largest declared stage timeout. Not a measurement: it covers the work that has no
#: declared bound at all - the in-flight stage returning, its heartbeat write, the loop noticing the
#: stop flag, and process teardown. Stated here rather than picked because it is the number a
#: reviewer can argue with, which is the only property a buffer needs.
STOP_BUFFER_SECONDS = 30


@dataclass
class Stage:
    """One blocking operation that can be in flight when SIGTERM arrives."""

    name: str
    seconds: float | None
    source: str
    note: str = ""

    @property
    def bounded(self) -> bool:
        return self.seconds is not None


@dataclass
class Inventory:
    stages: list[Stage] = field(default_factory=list)

    @property
    def bounded(self) -> list[Stage]:
        return [s for s in self.stages if s.bounded]

    @property
    def unbounded(self) -> list[Stage]:
        return [s for s in self.stages if not s.bounded]

    @property
    def largest(self) -> Stage | None:
        """The bounded stage with the longest declared timeout, which is what a stop budget waits for."""
        bounded = [s for s in self.stages if s.seconds is not None]
        if not bounded:
            return None
        # The value is not None here - that is the filter above - and narrowing it once beats a
        # cast that would hide a future None slipping through the same filter.
        return max(bounded, key=lambda s: s.seconds if s.seconds is not None else 0.0)

    @property
    def budget(self) -> float:
        """Largest declared stage timeout plus the stated buffer.

        A lower bound, because the unbounded stages have no declared ceiling to add. The tool says
        so where it reports rather than presenting this as the daemon's maximum.
        """
        largest = self.largest
        if largest is None or largest.seconds is None:
            return STOP_BUFFER_SECONDS
        return largest.seconds + STOP_BUFFER_SECONDS


def _literal(source: str, name: str) -> float | None:
    """A module-level numeric constant's value, read from the source rather than imported.

    Reading the AST instead of importing keeps this tool offline and side-effect free: importing
    `llm_decision` pulls in pydantic models and the SDK, and a tool that computes a budget should
    not need a working broker to run. The cost is that it reads assignments, not computed values -
    stated as a limit rather than hidden.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                    return float(node.value.value)
    return None


def collect() -> Inventory:
    """Every blocking operation the daemon can be inside when SIGTERM arrives."""
    inventory = Inventory()
    broker_src = (SRC / "broker_client.py").read_text(encoding="utf-8")
    llm_src = (SRC / "llm_decision.py").read_text(encoding="utf-8")
    daemon_src = (SRC / "daemon.py").read_text(encoding="utf-8")

    inventory.stages.append(Stage(
        "daemon cooperative wait",
        _literal(daemon_src, "COOPERATIVE_WAIT_POLL_SECONDS"),
        "src/autopoiesis/daemon.py COOPERATIVE_WAIT_POLL_SECONDS",
        "default scheduler, round, broker backoff and evidence waits check the stop flag",
    ))

    # The broker layer: a urllib3 Timeout(total, connect, read). The `total` is the ceiling on the
    # whole request, which is what a stop budget needs.
    broker_total = None
    match = re.search(r"RequestTimeoutPolicy\(\s*total=([0-9.]+)", broker_src)
    if match:
        broker_total = float(match.group(1))
    inventory.stages.append(Stage(
        "broker HTTP request",
        broker_total,
        "src/autopoiesis/broker_client.py REQUEST_TIMEOUT",
        "connect/read are smaller bounds inside the same total",
    ))

    # The two model paths. Curriculum is the off-hours job and holds the largest declared timeout;
    # the decision path answers the trading loop and is configured.
    curriculum = _literal(llm_src, "CURRICULUM_TIMEOUT_S")
    inventory.stages.append(Stage(
        "curriculum generation (off-hours)",
        curriculum,
        "src/autopoiesis/llm_decision.py CURRICULUM_TIMEOUT_S",
        "measured 141.9s at num_ctx=8192 on this host, inside the 600s ceiling",
    ))

    decision_default = None
    config_src = (SRC / "config.py").read_text(encoding="utf-8")
    match = re.search(r'llm_timeout_seconds=_positive_int\(\s*"AUTOPOIESIS_LLM_TIMEOUT_SECONDS",\s*([0-9]+)',
                      config_src)
    if match:
        decision_default = float(match.group(1))
    inventory.stages.append(Stage(
        "model decision (trading loop)",
        decision_default,
        "src/autopoiesis/config.py AUTOPOIESIS_LLM_TIMEOUT_SECONDS",
        "operator-settable; a raise here must raise the budget too",
    ))

    # Probes and unit-invoked helpers. Bounded, and small, but they are blocking.
    doctor_src = (SRC / "doctor.py").read_text(encoding="utf-8")
    probe_values = [float(x) for x in re.findall(r"timeout=([0-9]+)", doctor_src)]
    inventory.stages.append(Stage(
        "doctor / CLI probes",
        min(probe_values) if probe_values else None,
        "src/autopoiesis/doctor.py request timeouts",
        f"{len(probe_values)} probe call site(s) share the smallest declared value; the CLI's own "
        "probes use the same bound",
    ))

    # The watchdog runs `systemctl` through a subprocess with a declared timeout.
    cli_src = (SRC / "cli.py").read_text(encoding="utf-8")
    subprocess_timeouts = [float(x) for x in re.findall(r"subprocess\.run\([^)]*timeout=([0-9]+)", cli_src)]
    inventory.stages.append(Stage(
        "watchdog systemctl subprocess",
        max(subprocess_timeouts) if subprocess_timeouts else None,
        "src/autopoiesis/cli.py subprocess.run timeout",
    ))

    # Stages with no declared bound. They are listed because an unbounded stage is the reason a
    # derived budget is a lower bound, and a reader deserves to see which ones those are.
    inventory.stages.append(Stage(
        "journal append and fsync",
        None,
        "src/autopoiesis/journal.py _append_json",
        "bounded by record size on a healthy filesystem; unbounded in principle",
    ))
    inventory.stages.append(Stage(
        "strategy and knowledge file writes",
        None,
        "src/autopoiesis/atomicio.py write_json_atomic",
        "atomic replace plus directory fsync; small, but not declared",
    ))
    return inventory


def unit_budget() -> int:
    """What the unit template declares as its stop window, or -1 when it carries the marker.

    A template that carries `@STOP_BUDGET_SECONDS@` has no number to read: the installer substitutes
    the derived one at install time, which is the whole point of the marker. -1 means "derived",
    not "missing" - and the caller distinguishes them, because a template with no `TimeoutStopSec`
    line at all falls back to systemd's default of 90s, which is also smaller than the budget.
    """
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"^TimeoutStopSec=(\S+)", text, re.MULTILINE)
    if not match:
        return 0  # no line: systemd's own default, not this tool's number
    value = match.group(1)
    if BUDGET_MARKER in value:
        return -1
    return int(value) if value.isdigit() else -1


def template_is_derived() -> bool:
    """Whether the template defers the budget to the derivation rather than declaring one.

    True is the required state: a number in the template is a number that can drift from the stage
    timeout it was computed from, and nothing but this check would notice. A marker cannot drift,
    because there is nothing in it to drift.
    """
    return BUDGET_MARKER in UNIT_TEMPLATE.read_text(encoding="utf-8")


def render_unit(budget: int) -> str:
    """The unit template with the derived budget substituted for the marker."""
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    if BUDGET_MARKER not in text:
        # Replace whatever the template declares, rather than assuming the line exists: a template
        # with no TimeoutStopSec at all gets systemd's default, which is 90s and is also smaller
        # than the derived budget.
        found = re.search(r"^TimeoutStopSec=.*$", text, re.MULTILINE)
        line = found.group(0) if found else ""
        text = text.replace(line, f"TimeoutStopSec={BUDGET_MARKER}")
    return text.replace(BUDGET_MARKER, str(budget))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--json", action="store_true", help="one object on stdout")
    parser.add_argument("--apply", action="store_true",
                        help="substitute the derived budget into the unit template")
    parser.add_argument("--budget-only", action="store_true",
                        help="print the derived number and nothing else, for a shell substitution")
    args = parser.parse_args(argv)

    inventory = collect()
    budget = round(inventory.budget)
    declared = unit_budget()
    largest = inventory.largest
    derived_template = template_is_derived()
    # Both states are acceptable and they mean different things: a template that defers to the
    # derivation is always in step by construction, and one that declares a number is acceptable
    # only while that number still covers the budget. Anything else - a number that is too small,
    # or no line at all - fails.
    ok = derived_template or (declared > 0 and declared >= budget)

    payload = {
        "derived_budget_seconds": budget,
        "unit_declares_the_marker": derived_template,
        "unit_declared_seconds": None if derived_template else declared,
        "largest_stage": None if not largest else {
            "name": largest.name, "seconds": largest.seconds, "source": largest.source,
        },
        "buffer_seconds": STOP_BUFFER_SECONDS,
        "stages": [
            {"name": s.name, "seconds": s.seconds, "source": s.source, "note": s.note}
            for s in inventory.stages
        ],
        "unbounded_stages": [s.name for s in inventory.unbounded],
        "covers_every_stage": ok,
    }
    if args.budget_only:
        # Nothing but the number: the operator script substitutes it with sed, and a tool that
        # printed anything else would put prose into a unit file.
        print(budget)
        return 0
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if ok else 1

    print("shutdown budget, derived from the stages that can be in flight at SIGTERM")
    print("-" * 78)
    for stage in inventory.stages:
        bound = f"{stage.seconds:g}s" if stage.bounded else "unbounded"
        print(f"  {stage.name:36s} {bound:>12}   {stage.source}")
    print("-" * 78)
    if largest is not None:
        print(f"largest declared stage : {largest.name} at {largest.seconds:g}s")
    print(f"stated buffer          : {STOP_BUFFER_SECONDS:g}s")
    print(f"derived stop budget    : {budget}s")
    window = "(derived at install time)" if derived_template else f"{declared}s"
    print(f"unit TimeoutStopSec    : {window}")
    if inventory.unbounded:
        print(f"unbounded stages       : {', '.join(s.name for s in inventory.unbounded)}")
        print("                          the derived budget is a lower bound, not the daemon's "
              "maximum")
    if ok and derived_template:
        print("OK - the template defers to the derived budget, so it cannot drift from it")
    elif ok:
        print(f"OK - the declared window covers the derived budget by {declared - budget}s")
    else:
        print(f"FAIL - the stop window is {budget - declared}s smaller than the derived budget; "
              "a SIGTERM during the largest stage ends in SIGKILL")
    if args.apply:
        UNIT_TEMPLATE.write_text(render_unit(budget), encoding="utf-8")
        print(f"wrote TimeoutStopSec={budget} into {UNIT_TEMPLATE.name}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
