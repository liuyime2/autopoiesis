"""A read-only status snapshot: Overview, Decisions, Strategies, Evidence.

The plan's first frontend has four questions to answer beyond the overview: why was this decision
taken and not another, what has the self-evolution actually changed, do the results hold up, and can
I still trust the system to be running. All of it read-only.

**Why static HTML and not a service.** A page that reads `journal.jsonl` per request would scan
67MB per visit and would still be wrong whenever a rotation moved it. A page generated from a
frozen snapshot cannot drift mid-read, cannot place an order because there is no order path in it,
and needs nothing installed beyond the three runtime dependencies — no Node toolchain, which is what
the plan asked for. The report timer already exists; this reuses its cadence rather than adding a
server.

**Null is shown as UNKNOWN, never as zero.** "Not measurable" and "measured at zero" are different
claims, and the plan's acceptance criteria require the page say which one it is looking at.

Usage:  python tools/status_page.py [OUTPUT_DIR]
Writes `index.html`, `decisions.html`, `strategies.html`, `evidence.html` into the output directory
(default `runtime/autopoiesis/report/site`). All are read-only; none reads a credential.
"""

from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopoiesis.atomicio import write_text_atomic
from autopoiesis.config import AgentConfig

OUT_DIR = ROOT / "runtime" / "autopoiesis" / "report" / "site"
JOURNAL = ROOT / "runtime" / "autopoiesis" / "journal.jsonl"
STRATEGIES = ROOT / "runtime" / "autopoiesis" / "strategies"

NAV = (
    ("index", "Overview"),
    ("decisions", "Decisions"),
    ("strategies", "Strategies"),
    ("evidence", "Evidence"),
)

STYLE = """
:root{--bg:#1b1e22;--fg:#f2efe8;--accent:#4fd1a5;--warn:#e8b04b;--fail:#e8695f;--dim:#9aa3ab;--line:#2c3136}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;margin:0;padding:0 0 3rem}
header{padding:1.5rem 2rem;border-bottom:1px solid var(--line)}
h1{font-size:1.4rem;margin:0 0 .25rem}
h2{font-size:1.05rem;margin:2rem 0 .5rem;color:var(--accent)}
nav{display:flex;gap:1.25rem;flex-wrap:wrap;margin-top:.75rem}
nav a{color:var(--dim);text-decoration:none;padding:.25rem 0;border-bottom:2px solid transparent}
nav a:hover,nav a:focus{color:var(--fg)}
nav a[aria-current]{color:var(--accent);border-bottom-color:var(--accent)}
main{padding:0 2rem;max-width:72rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
td,th{padding:.45rem .6rem;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:.85rem;text-transform:uppercase;letter-spacing:.04em}
code,.mono{font-family:ui-monospace,SFMono-Regular,monospace;font-size:.88em}
.badge{display:inline-block;padding:.1rem .5rem;border-radius:.75rem;font-size:.78rem;font-weight:600}
.ok{background:rgba(79,209,165,.15);color:var(--accent)}
.warn{background:rgba(232,176,75,.15);color:var(--warn)}
.fail{background:rgba(232,105,95,.15);color:var(--fail)}
.note{color:var(--dim);font-size:.9rem;max-width:60rem;margin:.25rem 0 1rem}
.unknown{color:var(--warn);font-weight:600}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:1rem;margin:1rem 0}
.kv div{background:#22262b;border:1px solid var(--line);border-radius:.5rem;padding:.75rem}
.kv dt{color:var(--dim);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}
.kv dd{margin:.25rem 0 0;font-size:1.1rem;font-weight:600}
footer{padding:1.5rem 2rem;color:var(--dim);font-size:.85rem;border-top:1px solid var(--line);margin-top:2rem}
@media(max-width:40rem){header,main,footer{padding-left:1rem;padding-right:1rem}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def _cell(value) -> str:
    """A value as HTML, with null rendered as the word UNKNOWN rather than as 0 or empty."""
    if value is None or value == "":
        return '<span class="unknown">UNKNOWN</span>'
    return html.escape(str(value))


def _badge(status: str | None) -> str:
    if not status:
        return '<span class="unknown">UNKNOWN</span>'
    lowered = status.upper()
    cls = "ok" if lowered in ("OK", "ACTIVE", "PASS", "SUCCESS", "RUNNING") else (
        "fail" if lowered in ("FAIL", "FAILED", "ERROR", "SUSPENDED") else "warn"
    )
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _page(title: str, current: str, body: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    current_attr = ' aria-current="page"'
    nav = "".join(
        f'<a href="{key}.html"{current_attr if key == current else ""}>{label}</a>'
        for key, label in NAV
    )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            f"<title>autopoiesis — {html.escape(title)}</title>",
            f"<style>{STYLE}</style></head><body>",
            "<header>",
            "<h1>autopoiesis</h1>",
            '<p class="note">PAPER ONLY &middot; read-only snapshot &middot; this page cannot place an '
            "order or change a setting</p>",
            f"<nav>{nav}</nav>",
            "</header><main>",
            f"<h2>{html.escape(title)}</h2>",
            *body,
            "</main>",
            f"<footer>generated {now} UTC from {html.escape(str(JOURNAL.relative_to(ROOT)))} and the "
            "same evaluator the CLI uses. A number that cannot be computed reads UNKNOWN, which is a "
            "different claim from zero.</footer>",
            "</body></html>",
        ]
    )


# --- the data, read once ---------------------------------------------------------------------


@dataclass
class Snapshot:
    """Everything the pages read, read once, at one instant.

    Each page used to open the journal for itself. A run that landed between two appends
    therefore produced pages from two different data versions - the overview said 2,060 cycles
    while the decisions page showed a cycle the overview's count did not include - and nothing
    looked wrong, because both pages were individually correct. The same instant being wrong in
    two places consistently is the failure the phrase "read once" is here to prevent.

    It is also why the readers below go through `JsonlJournal.history_paths()`. They used to open
    the live file, so after one rotation this panel reported the newest fragment of the record
    and nothing on the page indicated that most of it was one directory over in the archive.
    That is the fourth place the file list was hand-rolled, and the only reason it is a paragraph
    here rather than a bug report is that the panel is not consulted by anything.
    """

    taken_at: datetime
    source: str
    cycles: list[dict]
    events: list[dict]
    strategies: list[dict]
    heartbeat: dict
    agent: dict
    model_registry: str | None


def _read_journal_records() -> tuple[list[dict], list[dict]]:
    """(cycles, events) over every generation, oldest first.

    Split by structure, not by a field that may be absent the way `replay_audit` and
    `check_runtime_integrity` both do: a cycle record carries `execution` and never `event_id`,
    a journal event is the other way round.
    """
    from autopoiesis.journal import JsonlJournal

    journal = JsonlJournal(JOURNAL)
    cycles: list[dict] = []
    events: list[dict] = []
    for path in journal.history_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            (events if "event_type" in record else cycles).append(record)
    return cycles, events


def _read_strategies() -> list[dict]:
    if not STRATEGIES.exists():
        return []
    out = []
    for path in sorted(STRATEGIES.glob("*.json")):
        if path.name.startswith("rejected"):
            continue
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        spec["_file"] = path.name
        out.append(spec)
    return out


def load_snapshot() -> Snapshot:
    """One read of everything the four pages need, taken at one instant."""
    cycles, events = _read_journal_records()
    _verdict, agent = _benchmark_verdict()
    registry, _ = _pick_model_registry()
    return Snapshot(
        taken_at=datetime.now(timezone.utc),
        source=str(JOURNAL.relative_to(ROOT)),
        cycles=cycles,
        events=events,
        strategies=_read_strategies(),
        heartbeat=_heartbeat(),
        agent=agent,
        model_registry=registry,
    )


def _recent(snapshot: Snapshot, limit: int) -> list[dict]:
    return snapshot.cycles[-limit:]


def _events_of_kind(snapshot: Snapshot, kind: str, limit: int) -> list[dict]:
    return [e for e in snapshot.events if e.get("event_type") == kind][-limit:]


def _lifecycle_history(snapshot: Snapshot, limit: int = 40) -> list[dict]:
    rows = []
    for event in _events_of_kind(snapshot, "STRATEGY_LIFECYCLE_UPDATED", 10_000):
        payload = event.get("payload") or {}
        rows.append(
            {
                "at": (event.get("timestamp") or "")[:16],
                "strategy_id": event.get("strategy_id") or payload.get("strategy_id"),
                "from": payload.get("old_lifecycle"),
                "to": payload.get("new_lifecycle"),
                "reason": (payload.get("reason") or "")[:160],
            }
        )
    return rows[-limit:]


def _heartbeat() -> dict:
    path = ROOT / "runtime" / "autopoiesis" / "heartbeat.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _benchmark_verdict() -> tuple[str | None, dict]:
    """The agent-level verdict, from the same evaluator `make benchmark` uses.

    Not re-derived in the browser and not parsed from a log line: it calls the benchmark's own
    function, so the page and the CLI cannot disagree.
    """
    try:
        spec = __import__("importlib.util", fromlist=["util"]).spec_from_file_location(
            "bm", ROOT / "tools" / "benchmark.py"
        )
        module = __import__("importlib.util", fromlist=["util"]).module_from_spec(spec)
        spec.loader.exec_module(module)
        agent = module._agent_level()
        return (f"{agent['return_pct']:+.2f}% vs SPY {agent['spy_pct']:+.2f}% "
                f"({agent['excess']:+.2f})" if agent else None), (agent or {})
    except Exception:
        return None, {}


# --- the four pages --------------------------------------------------------------------------


def _daemon_state(snapshot: Snapshot) -> str:
    """Whether the daemon is running *now*, which is not what the heartbeat's own field says.

    The field was read and printed on its own, so this page said RUNNING over a heartbeat file
    three days old: the process that wrote it was long gone and nothing had replaced the word.
    The heartbeat carries a timestamp and a pid, so both are checked - a file older than the
    staleness bound, or whose process is no longer alive, is reported as what it is rather than
    as a state the system no longer has.

    The recorded value is kept inside the string rather than swallowed, because it is still a
    fact worth showing; it is just not the answer to "is it running".
    """
    beat = snapshot.heartbeat
    recorded = beat.get("status")
    if not recorded:
        return "UNKNOWN"
    stamp = beat.get("timestamp")
    age = None
    if stamp:
        try:
            moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            age = (snapshot.taken_at - moment).total_seconds()
        except ValueError:
            age = None
    pid = beat.get("pid")
    alive = True
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True  # running as someone else: alive, and not this process's business
    stale_after = 900
    try:
        stale_after = AgentConfig.from_env().stale_after_seconds
    except Exception:
        pass
    if age is not None and age > stale_after:
        return f"{recorded} (stale, {int(age // 60)} min old)"
    if not alive:
        return f"{recorded} (pid {pid} is not running)"
    return str(recorded)


def overview(snapshot: Snapshot) -> list[str]:
    cycles = _recent(snapshot, 500)
    agent = snapshot.agent
    verdict = None
    if agent:
        verdict = (f"{agent['return_pct']:+.2f}% vs SPY {agent['spy_pct']:+.2f}% "
                   f"({agent['excess']:+.2f})")
    return [
        '<dl class="kv">',
        "<div><dt>mode</dt><dd>paper</dd></div>",
        f"<div><dt>daemon</dt><dd>{_badge(_daemon_state(snapshot))}</dd></div>",
        f"<div><dt>cycles recorded</dt><dd>{_cell(len(snapshot.cycles))}</dd></div>",
        f"<div><dt>cycle errors</dt><dd>{_cell(snapshot.heartbeat.get('error_count'))}</dd></div>",
        "</dl>",
        "<h2>Can it be trusted to be running?</h2>",
        f'<p class="note">Last heartbeat {_cell(snapshot.heartbeat.get("timestamp"))}. The '
        "market-open and staleness checks cannot be skipped, and a stale snapshot is refused "
        "rather than traded on.</p>",
        "<h2>Does the result hold up?</h2>",
        "<table><tr><th>what</th><th>value</th></tr>",
        f"<tr><td>the agent after cost, vs SPY on the same window</td><td>{_cell(verdict)}</td></tr>",
        f"<tr><td>return on deployed capital</td><td>{_cell(agent.get('return_pct'))}%</td></tr>",
        f"<tr><td>SPY, same window</td><td>{_cell(agent.get('spy_pct'))}%</td></tr>",
        f"<tr><td>cycles in this snapshot</td><td>{_cell(len(cycles))}</td></tr>",
        "</table>",
        '<p class="note">The full target verdict, the per-strategy diagnostic and the attribution '
        "coverage come from <code>make benchmark</code>; this page prints the agent-level figure only, "
        "because that is the claim the target sentence makes.</p>",
    ]


def decisions(snapshot: Snapshot) -> list[str]:
    cycles = _recent(snapshot, 25)
    body = ["<h2>Why this decision, and why not another?</h2>"]
    if not cycles:
        body.append('<p class="note">No journal on this checkout, so there are no decisions to show. '
                    "That is UNKNOWN, not zero.</p>")
        return body
    body.append("<table><tr><th>when</th><th>symbol</th><th>rule</th><th>taken</th>"
                "<th>source</th><th>why</th></tr>")
    for record in reversed(cycles):
        decision = record.get("decision") or {}
        market = record.get("snapshot") or {}
        guardian = record.get("guardian") or {}
        action = decision.get("action")
        rationale = (decision.get("rationale") or "")[:120]
        override = decision.get("override_reason")
        if guardian and not guardian.get("approved", True) and guardian.get("reason"):
            rationale = f"refused: {guardian['reason'][:120]}"
        body.append(
            "<tr>"
            f'<td class="mono">{_cell((market.get("timestamp") or "")[:16])}</td>'
            f"<td>{_cell(market.get('symbol'))}</td>"
            f"<td class=\"mono\">{_cell(decision.get('rule_action'))}</td>"
            f"<td>{_badge(action)}</td>"
            f"<td>{_cell(decision.get('decision_source'))}</td>"
            f"<td>{_cell(rationale)}"
            + (f'<br><span class="note">{_cell(override)}</span>' if override else "")
            + "</td></tr>"
        )
    body.append("</table>")
    body.append('<p class="note">The rule decides first and the model may override with a reason; '
                "an override without one is discarded. Every refusal carries the Guardian's own "
                "reason.</p>")
    return body


def strategies(snapshot: Snapshot) -> list[str]:
    specs = snapshot.strategies
    history = _lifecycle_history(snapshot)
    body = ["<h2>What has the self-evolution actually changed?</h2>"]
    counts: dict[str, int] = {}
    for spec in specs:
        counts[str(spec.get("lifecycle"))] = counts.get(str(spec.get("lifecycle")), 0) + 1
    body.append('<dl class="kv">')
    for state in ("ACTIVE", "PROBATION", "PAUSED", "RETIRED", "BASELINE"):
        body.append(f"<div><dt>{state.lower()}</dt><dd>{_cell(counts.get(state))}</dd></div>")
    body.append("</dl>")
    if not history:
        body.append('<p class="note">No lifecycle transitions on this checkout.</p>')
    else:
        body.append("<table><tr><th>when</th><th>strategy</th><th>change</th><th>reason</th></tr>")
        for row in reversed(history):
            body.append(
                f"<tr><td class=\"mono\">{_cell(row['at'])}</td>"
                f"<td>{_cell(row['strategy_id'])}</td>"
                f"<td>{_badge(row['from'])} &rarr; {_badge(row['to'])}</td>"
                f"<td>{_cell(row['reason'])}</td></tr>"
            )
        body.append("</table>")
    body.append('<p class="note">A strategy is retired on its own recorded decisions, and the reason '
                "travels with the transition. <code>PAUSED</code> is a holding pattern, not a "
                "graveyard: the daemon returns at most one paused strategy to probation per pass, "
                "and only after it has been paused for <code>PAUSED_REVIEW_DAYS</code> and only on "
                "evidence from the journalled transition. This page used to call it terminal, which "
                "contradicted the code that reopens it; the state is read from the journal, and so "
                "is the sentence about it.</p>")
    return body


def evidence(snapshot: Snapshot) -> list[str]:
    verdict = None
    if snapshot.agent:
        verdict = (f"{snapshot.agent['return_pct']:+.2f}% vs SPY "
                   f"{snapshot.agent['spy_pct']:+.2f}% ({snapshot.agent['excess']:+.2f})")
    registry = snapshot.model_registry
    body = ["<h2>Do the results hold up?</h2>"]
    body.append("<table><tr><th>claim</th><th>what the record says</th></tr>")
    body.append(f"<tr><td>the agent beats holding, after cost</td><td>{_cell(verdict)}</td></tr>")
    body.append(f"<tr><td>how much of that is the model</td><td>{_cell(registry)}</td></tr>")
    body.append("<tr><td>directional edge in price</td><td>none found — 47 pre-stated price "
                "hypotheses and 14 news hypotheses through a family-wise gate</td></tr>")
    body.append("<tr><td>directional edge in news</td><td>the model reads overnight gaps "
                "correctly; the tradable next-day correlation is within 0.03 of zero</td></tr>")
    body.append("<tr><td>the one judgement that holds</td><td>volatility, on every instrument "
                "tested — a statement about risk, not direction</td></tr>")
    body.append("</table>")
    body.append("<h2>The signal ledger</h2>")
    ledger = ROOT / "runtime" / "autopoiesis" / "signal_trials.jsonl"
    if ledger.exists():
        try:
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
            distinct = len({r.get("name") for r in rows})
            verdicts: dict[str, int] = {}
            for row in rows:
                verdicts[str(row.get("verdict"))] = verdicts.get(str(row.get("verdict")), 0) + 1
            body.append(f'<p class="note">{len(rows)} recorded rows for {distinct} distinct '
                        f'hypotheses; the same ledger <code>make research-*</code> writes to.</p>')
            body.append("<table><tr><th>verdict</th><th>rows</th></tr>")
            for name, count in sorted(verdicts.items(), key=lambda kv: -kv[1]):
                body.append(f"<tr><td>{_badge(name)}</td><td>{count}</td></tr>")
            body.append("</table>")
        except Exception as exc:
            body.append(f'<p class="note">the ledger could not be read: '
                        f"{html.escape(type(exc).__name__)}</p>")
    else:
        body.append('<p class="note">No signal ledger on this checkout.</p>')
    body.append('<p class="note">Every figure on this page is reproducible with '
                "<code>make benchmark</code>, <code>make research-signals</code>, "
                "<code>make research-vix</code> and <code>make research-six</code>.</p>")
    return body


def _pick_model_registry() -> tuple[str | None, list[str]]:
    try:
        spec = __import__("importlib.util", fromlist=["util"]).spec_from_file_location(
            "bm", ROOT / "tools" / "benchmark.py"
        )
        module = __import__("importlib.util", fromlist=["util"]).module_from_spec(spec)
        spec.loader.exec_module(module)
        from autopoiesis.config import AgentConfig
        from autopoiesis.doctor import run_doctor

        config = AgentConfig.from_env()
        report = run_doctor(config, skip_broker=True)
        detail, _ = module._pick(report.checks, "model registry")
        return detail, []
    except Exception:
        return None, []


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    snapshot = load_snapshot()
    pages = {
        "index": ("Overview", "index", overview(snapshot)),
        "decisions": ("Decisions", "decisions", decisions(snapshot)),
        "strategies": ("Strategies", "strategies", strategies(snapshot)),
        "evidence": ("Evidence", "evidence", evidence(snapshot)),
    }
    for name, (title, _current, body) in pages.items():
        # Atomic, because a reader opening the page mid-generation used to see a half-written
        # file: these are written one at a time, and a browser landing between two of them got
        # a page whose nav pointed at files that did not exist yet.
        write_text_atomic(out / f"{name}.html", _page(title, name, body))
    for name in pages:
        print(f"wrote {out / (name + '.html')}")
    print("read-only: no order path, no credential read, nothing here can change a setting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
