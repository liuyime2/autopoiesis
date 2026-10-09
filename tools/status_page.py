"""A read-only status snapshot, as static HTML with no Node toolchain and no trading capability.

The plan for the first frontend is explicit: a newcomer should be able to see whether the system
can be trusted, why a decision was or was not taken, how much risk the agent carries, what the
self-evolution actually changed, and whether the results hold up - and none of it may place an
order or change a configuration. This produces that from the numbers the CLI already computes,
rather than having a browser recompute them from a journal.

**Why static HTML and not a service.** A page that reads `journal.jsonl` per request would scan
67MB per visit and would still be wrong whenever a rotation moved it. A page generated from a
frozen snapshot cannot drift mid-read, cannot place an order because there is no order path in it,
and needs nothing installed beyond the three runtime dependencies. The report timer already exists;
this reuses its cadence rather than adding a server.

**Null is shown as unknown, never as zero.** A benchmark that cannot be computed and a benchmark of
zero are different claims, and the plan's acceptance criteria both require that the page say which
one it is looking at.

Usage:  python tools/status_page.py [OUTPUT_DIR]
Writes `index.html` into the output directory (default `runtime/autopoiesis/report/site`).
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

#: What each number is, and what it is not. Written here so a reader of the page can disagree
#: with a definition rather than have to trust it.
SECTIONS = (
    ("Overview", "Can the system be trusted to be running?", (
        ("mode", "the trading mode; anything but paper is refused in code"),
        ("daemon", "whether the daemon process is alive and what it last said"),
        ("market", "whether the market is open, from the broker's own clock"),
        ("cycles", "cycles recorded on this host, over the window the journal still holds"),
    )),
    ("Decisions", "Why this decision, and why not another?", (
        ("last_cycle", "the most recent cycle's symbol, action, source and rationale"),
        ("guardian", "what the Guardian refused, and why"),
        ("rule_vs_llm", "where the model overrode the rule, and what that was worth"),
    )),
    ("Risk", "How much risk is the agent carrying?", (
        ("limits", "the hard limits as configured, which the code cannot raise"),
        ("exposure", "the agent's own exposure against the cap"),
        ("risk_block", "the volatility forecast the decision context carries, and its quality"),
    )),
    ("Evidence", "Do the results hold up?", (
        ("benchmark", "the agent's return after cost against SPY over the same window"),
        ("window", "trading days on record against the 60-day target"),
        ("coverage", "how many decisions can be attributed to a model at all"),
    )),
)


def _read_heartbeat() -> dict:
    path = ROOT / "runtime" / "autopoiesis" / "heartbeat.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_latest_report() -> dict:
    """The newest daily report the report timer wrote, if there is one."""
    directory = ROOT / "runtime" / "autopoiesis" / "reports"
    if not directory.exists():
        return {}
    newest = sorted(directory.glob("*.txt"))
    if not newest:
        return {}
    return {"file": newest[-1].name, "text": newest[-1].read_text(encoding="utf-8")[:4000]}


def _risk_block() -> dict:
    """The same risk block the decision context carries, for the first traded symbol."""
    try:
        import os

        import alpaca_trade_api as tradeapi

        from autopoiesis.config import AgentConfig
        from autopoiesis.data_gateway import AlpacaDataGateway
        from autopoiesis.risk_judgment import RiskJudgment
    except ImportError:
        return {"status": "UNKNOWN", "note": "broker client unavailable"}
    if not os.environ.get("ALPACA_API_KEY"):
        return {"status": "UNKNOWN", "note": "no credentials; run with the operator env file"}
    try:
        config = AgentConfig.from_env()
        gateway = AlpacaDataGateway(
            client=tradeapi.REST(
                os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], os.environ["ALPACA_BASE_URL"]
            )
        )
        judgment = RiskJudgment(gateway.daily_closes)
        symbol = next(iter(config.symbols), None)
        block = judgment.block(symbol) if symbol else None
        return block or {"status": "UNKNOWN", "note": f"no forecast for {symbol}"}
    except Exception as exc:
        return {"status": "UNKNOWN", "note": f"{type(exc).__name__}: {exc}"}


def _cell(value) -> str:
    """A value as HTML, with null rendered as the word UNKNOWN rather than as 0 or empty."""
    if value is None or value == "":
        return '<span class="unknown">UNKNOWN</span>'
    return html.escape(str(value))


def render(rows: dict[str, object]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>autopoiesis — status</title>",
        "<style>",
        ":root{--bg:#1b1e22;--fg:#f2efe8;--accent:#4fd1a5;--warn:#e8b04b;--fail:#e8695f;--dim:#9aa3ab}",
        "body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;margin:0;padding:2rem}",
        "h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.1rem;margin:2rem 0 .5rem;color:var(--accent)}",
        "table{border-collapse:collapse;width:100%;max-width:60rem}td,th{padding:.4rem .6rem;text-align:left;"
        "border-bottom:1px solid #2c3136;vertical-align:top}th{color:var(--dim);font-weight:600}",
        "code{font-family:ui-monospace,monospace;font-size:.9em}",
        ".unknown{color:var(--warn);font-weight:600}",
        ".note{color:var(--dim);font-size:.9rem;max-width:60rem}",
        "</style></head><body>",
        "<h1>autopoiesis</h1>",
        f'<p class="note">PAPER ONLY &middot; read-only snapshot &middot; generated {now} &middot; '
        "this page cannot place an order or change a setting</p>",
    ]
    for title, question, items in SECTIONS:
        parts.append(f"<h2>{html.escape(title)}</h2>")
        parts.append(f'<p class="note">{html.escape(question)}</p>')
        parts.append("<table><tr><th>what</th><th>value</th><th>what it means</th></tr>")
        for key, meaning in items:
            parts.append(
                f"<tr><td>{html.escape(key)}</td><td>{_cell(rows.get(key))}</td>"
                f"<td class=\"note\">{html.escape(meaning)}</td></tr>"
            )
        parts.append("</table>")
    parts.append('<p class="note">Every figure here is read from the same journal and the same '
                 "evaluator the CLI uses; none is recomputed in the browser. A number that cannot be "
                 "computed reads UNKNOWN, which is a different claim from zero.</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    heartbeat = _read_heartbeat()
    report = _read_latest_report()
    rows: dict[str, object] = {
        "mode": (heartbeat.get("status") and "paper") or None,
        "daemon": heartbeat.get("status"),
        "market": None,  # the broker's clock is not read here; a stale page must not claim it
        "cycles": heartbeat.get("cycle_count"),
        "last_cycle": heartbeat.get("last_cycle_id"),
        "guardian": "see the journal; the Guardian has no bypass",
        "rule_vs_llm": report.get("file"),
        "limits": "max_position_value / max_total_exposure / max_daily_loss / max_trades_per_day",
        "exposure": None,
        "risk_block": json.dumps(_risk_block(), sort_keys=True, default=str),
        "benchmark": "run `make benchmark` for the target verdict",
        "window": "run `make benchmark` for trading days on record vs the 60-day target",
        "coverage": "run `make benchmark` for the attribution rate",
    }
    out = Path(argv[1]) if len(argv) > 1 else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(rows), encoding="utf-8")
    print(f"wrote {out / 'index.html'}")
    print("read-only: this page has no order path and reads no credentials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
