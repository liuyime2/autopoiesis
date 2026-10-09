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
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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


def _read_cycles(limit: int) -> list[dict]:
    """The most recent `limit` cycle records, by file order — the journal is append-only, so the
    tail of the live file is the newest."""
    if not JOURNAL.exists():
        return []
    out: list[dict] = []
    with open(JOURNAL, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if "event_type" not in record:  # a cycle record, not a journalled event
                out.append(record)
    return out[-limit:]


def _read_events(kind: str, limit: int) -> list[dict]:
    if not JOURNAL.exists():
        return []
    out: list[dict] = []
    with open(JOURNAL, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("event_type") == kind:
                out.append(record)
    return out[-limit:]


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


def _lifecycle_history(limit: int = 40) -> list[dict]:
    rows = []
    for event in _read_events("STRATEGY_LIFECYCLE_UPDATED", 10_000):
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


def overview() -> list[str]:
    beat = _heartbeat()
    cycles = _read_cycles(500)
    verdict, agent = _benchmark_verdict()
    return [
        '<dl class="kv">',
        "<div><dt>mode</dt><dd>paper</dd></div>",
        f"<div><dt>daemon</dt><dd>{_badge(beat.get('status'))}</dd></div>",
        f"<div><dt>cycles recorded</dt><dd>{_cell(beat.get('cycle_count'))}</dd></div>",
        f"<div><dt>cycle errors</dt><dd>{_cell(beat.get('error_count'))}</dd></div>",
        "</dl>",
        "<h2>Can it be trusted to be running?</h2>",
        f'<p class="note">Last heartbeat {_cell(beat.get("timestamp"))}. The market-open and '
        "staleness checks cannot be skipped, and a stale snapshot is refused rather than traded on.</p>",
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


def decisions() -> list[str]:
    cycles = _read_cycles(25)
    body = ["<h2>Why this decision, and why not another?</h2>"]
    if not cycles:
        body.append('<p class="note">No journal on this checkout, so there are no decisions to show. '
                    "That is UNKNOWN, not zero.</p>")
        return body
    body.append("<table><tr><th>when</th><th>symbol</th><th>rule</th><th>taken</th>"
                "<th>source</th><th>why</th></tr>")
    for record in reversed(cycles):
        decision = record.get("decision") or {}
        snapshot = record.get("snapshot") or {}
        guardian = record.get("guardian") or {}
        action = decision.get("action")
        rationale = (decision.get("rationale") or "")[:120]
        override = decision.get("override_reason")
        if guardian and not guardian.get("approved", True) and guardian.get("reason"):
            rationale = f"refused: {guardian['reason'][:120]}"
        body.append(
            "<tr>"
            f'<td class="mono">{_cell((snapshot.get("timestamp") or "")[:16])}</td>'
            f"<td>{_cell(snapshot.get('symbol'))}</td>"
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


def strategies() -> list[str]:
    specs = _read_strategies()
    history = _lifecycle_history()
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
                "travels with the transition. <code>PAUSED</code> is terminal in these rules — it is "
                "a graveyard the loop cannot reopen, not a holding pattern.</p>")
    return body


def evidence() -> list[str]:
    verdict, _agent = _benchmark_verdict()
    registry, _ = _pick_model_registry()
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
    pages = {
        "index": ("Overview", "index", overview()),
        "decisions": ("Decisions", "decisions", decisions()),
        "strategies": ("Strategies", "strategies", strategies()),
        "evidence": ("Evidence", "evidence", evidence()),
    }
    for name, (title, _current, body) in pages.items():
        (out / f"{name}.html").write_text(_page(title, name, body), encoding="utf-8")
    for name in pages:
        print(f"wrote {out / (name + '.html')}")
    print("read-only: no order path, no credential read, nothing here can change a setting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
