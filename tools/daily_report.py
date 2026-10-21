#!/usr/bin/env python3
"""One trading day's validation report, written after the close.

What it answers, from the journal and from `doctor` (run without the broker, so it never
places or reads an order):

- did the agent start trading by itself at the open - the first and last cycle of the day;
- who decided - the LLM, the rule, the fallback - and what happened to each decision;
- where the model overrode the strategy's rule, and the reason it gave;
- what short-selling decisions were made, and what the Guardian said (shorts run in shadow);
- the validation lines from doctor: the paired LLM-vs-rule value, PnL against holding SPY,
  the owner exit, and the overall result;
- the benchmark verdict.

It reads; it changes nothing. Written to runtime/autopoiesis/reports/<date>.txt and printed.
Run by the report timer (tools/report.timer) on weekdays at 16:30 New York time, or by hand with
`make daily-report` (optionally `DATE=YYYY-MM-DD`).
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

NEW_YORK = ZoneInfo("America/New_York")
#: doctor checks that carry the validation this report exists for.
VALIDATION_CHECKS = ("llm vs rule", "pnl vs holding", "pnl attribution", "decision quality")


def day_summary(records, day: date) -> dict:
    """Cycle counts, timings, decisions and overrides for one New York trading day."""
    todays = [
        r for r in records
        if r.snapshot.timestamp.astimezone(NEW_YORK).date() == day
    ]
    todays.sort(key=lambda r: r.snapshot.timestamp)
    outcomes = collections.Counter(
        (r.decision.decision_source, r.decision.action, r.execution.status) for r in todays
    )
    # A departure from the rule is the model's only when the model gave a reason for it. Measured on
    # 2026-10-21: 62 departures, and 26 of them carried no reason because they were not departures at
    # all - the accumulation gate's `selection_unavailable: ... has not passed the screen` and the
    # in-flight guard's `skip: an identical order ...`, both of which the *system* produced. Counting
    # them here charged the system's refusals to the model, which is the same collapse the record
    # fixed and the doctor now excludes.
    from autopoiesis.learning_evidence import model_departed_from_rule

    def _departure(record) -> bool:
        return model_departed_from_rule(record)

    overrides = [
        (r.snapshot.timestamp.astimezone(NEW_YORK).strftime("%H:%M"), r.decision.rule_action,
         r.decision.action, r.decision.override_reason or "")
        for r in todays if _departure(r)
    ]
    abstentions = [
        (r.snapshot.timestamp.astimezone(NEW_YORK).strftime("%H:%M"), r.decision.rule_action,
         r.decision.action, (r.decision.rationale or "")[:80])
        for r in todays
        if r.decision.rule_action is not None and r.decision.action != r.decision.rule_action
        and not _departure(r)
    ]
    shorts = [
        (r.snapshot.timestamp.astimezone(NEW_YORK).strftime("%H:%M"), r.decision.action,
         r.guardian.reason, r.execution.status)
        for r in todays if r.decision.action in {"SHORT", "COVER"}
    ]
    # Decisions the system changed before they reached the Guardian. Without this the report counts
    # a refused addition as a hold the model chose, which is the collapse the 2026-10-20 review named:
    # "model judged", "system intervened" and "what executed" were one field.
    interventions = [
        (r.snapshot.timestamp.astimezone(NEW_YORK).strftime("%H:%M"), r.snapshot.symbol,
         r.intervention.original_action, r.decision.action, r.intervention.reason)
        for r in todays if r.intervention is not None
    ]
    # Where the round went. A symbol costs about 105 seconds and the model call is about 45 of them;
    # the remainder is the Guardian, the in-flight guard and the journal write, which had no figure
    # before 2026-10-21 and therefore no target.
    measured = [r.timings for r in todays if getattr(r, "timings", None)]
    timing = {}
    for key in ("cycle_ms", "snapshot_ms", "decision_ms", "execution_ms"):
        values = [m[key] for m in measured if key in m]
        if values:
            timing[key] = round(sum(values) / len(values), 1)
    cited = [
        r for r in todays
        if any(k in ((r.decision.rationale or "") + " " + (r.decision.override_reason or "")).lower()
               for k in ("vol_scaled", "forecast_vol", "risk block", "risk is active", "risk status"))
    ]
    return {
        "risk_cited": len(cited),
        "risk_cited_trades": sum(1 for r in cited if r.decision.action in {"BUY", "SELL", "SHORT", "COVER"}),
        "cycles": len(todays),
        "first": todays[0].snapshot.timestamp.astimezone(NEW_YORK).strftime("%H:%M") if todays else None,
        "last": todays[-1].snapshot.timestamp.astimezone(NEW_YORK).strftime("%H:%M") if todays else None,
        "errors": sum(1 for r in todays if r.error),
        "outcomes": outcomes,
        "paired": sum(1 for r in todays if r.decision.rule_action is not None),
        "overrides": overrides,
        "abstentions": abstentions,
        "interventions": interventions,
        "timing": timing,
        "shorts": shorts,
    }


def render(day: date, summary: dict, checks: list[tuple[str, str, str]], verdict: str) -> str:
    lines = [f"autopoiesis daily validation report - {day.isoformat()} (New York)", "=" * 78]
    if summary["cycles"]:
        lines.append(
            f"trading      {summary['cycles']} cycle(s), first {summary['first']}, last "
            f"{summary['last']}, {summary['errors']} cycle error(s)"
        )
    else:
        lines.append("trading      NO CYCLES today - a holiday, or the daemon did not trade")
    lines.append(f"paired       {summary['paired']} decision(s) recorded the rule's action")
    lines.append("decisions    (source, action, execution): count")
    for (source, action, status), n in sorted(summary["outcomes"].items(), key=lambda kv: -kv[1]):
        lines.append(f"               {source:<22} {action:<6} {status:<10} {n}")
    lines.append(
        f"risk block   {summary['risk_cited']} decision(s) cite it in their reasons, {summary['risk_cited_trades']} of them trades "
        "(it is meant to size a position down, never to argue for one)"
    )
    lines.append(
        f"overrides    {len(summary['overrides'])} departure(s) from the rule by the model, with a "
        f"reason; {len(summary.get('abstentions') or [])} difference(s) the system produced are "
        "counted below, not here"
    )
    for when, rule, taken, reason in summary["overrides"][:20]:
        lines.append(f"               {when} rule {rule} -> {taken}: {reason[:90]}")
    lines.append(
        f"abstained    {len(summary.get('abstentions') or [])} decision(s) differ from the rule "
        "because the system refused or skipped them, not because the model chose so"
    )
    for when, rule, taken, why in (summary.get("abstentions") or [])[:10]:
        lines.append(f"               {when} rule {rule} -> {taken}: {why[:90]}")
    lines.append(
        f"intervened   {len(summary.get('interventions') or [])} decision(s) changed by the system "
        "before the Guardian (these are not the model's own holds)"
    )
    for when, symbol, original, taken, reason in (summary.get("interventions") or [])[:20]:
        lines.append(f"               {when} {symbol} {original} -> {taken}: {reason[:90]}")
    timing = summary.get("timing") or {}
    if timing.get("cycle_ms"):
        rest = timing["cycle_ms"] - timing.get("snapshot_ms", 0) - timing.get("decision_ms", 0) - timing.get("execution_ms", 0)
        lines.append(
            f"timing       mean cycle {timing['cycle_ms']:.0f}ms: snapshot {timing.get('snapshot_ms', 0):.0f}, "
            f"decision {timing.get('decision_ms', 0):.0f}, execution {timing.get('execution_ms', 0):.0f}, "
            f"guardian+journal {rest:.0f}"
        )
    lines.append(f"shorts       {len(summary['shorts'])} SHORT/COVER decision(s)")
    for when, action, reason, status in summary["shorts"][:20]:
        lines.append(f"               {when} {action} {status}: {reason[:90]}")
    lines.append("doctor")
    for name, status, detail in checks:
        lines.append(f"  [{status.upper():4}] {name:<18} {detail[:220]}")
    lines.append(f"benchmark    {verdict}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD in New York time; default today")
    args = parser.parse_args()
    day = date.fromisoformat(args.date) if args.date else datetime.now(NEW_YORK).date()

    from autopoiesis.config import AgentConfig
    from autopoiesis.doctor import run_doctor
    from autopoiesis.journal import JsonlJournal

    config = AgentConfig.from_env()
    summary = day_summary(JsonlJournal(config.journal_path).read_all(), day)
    report = run_doctor(config, skip_broker=True)
    checks = [
        (c.name, c.status.value, c.detail) for c in report.checks if c.name in VALIDATION_CHECKS
    ]
    fails = sum(1 for c in report.checks if c.status.value == "fail")
    warns = sum(1 for c in report.checks if c.status.value == "warn")
    checks.append(("result", "fail" if fails else "ok", f"{fails} fail, {warns} warn"))

    sys.path.insert(0, str(ROOT / "tools"))
    import benchmark

    # The agent-level block is what the verdict is now about, and the per-strategy rows are a
    # diagnostic beside it - see `benchmark._headline`. Calling it with only the report leaves
    # `agent` missing, which is how this entry point crashed while its unit tests stayed green:
    # the test called the helper with the new signature and this caller did not. A caller a timer
    # invokes and a test that does not call the caller are two different coverage claims.
    agent = benchmark._agent_level()
    verdict, _ = benchmark._headline(report, agent)

    text = render(day, summary, checks, verdict)
    out = config.journal_path.parent / "reports" / f"{day.isoformat()}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"written to {out}")
    print(json.dumps({"date": day.isoformat(), "cycles": summary["cycles"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
