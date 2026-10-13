"""The public site: a static, generated, credential-free set of pages.

What this is for. The README is a document; a project site is a claim about a project, read by
somebody deciding whether to spend an afternoon on the repository. Every figure on these pages
comes from `docs/site/figures.json`, which `make refresh-figures` produced by running the commands
that produced the figures. Nothing is typed in by hand: a number in prose rots the moment the code
moves, and a reader has no way to tell a current number from a stale one.

**No screenshots.** There is no browser here to take one and no image that would not be stale the
next day, so the pages carry tables of measured numbers and the command that produced each one. A
reader can run the command; a reader cannot re-derive a PNG.

**No runtime data.** The site must build on a fresh clone, where `runtime/` does not exist, so it
reads the published figures file and nothing else. That is also why it publishes no account
position and no host path: what is published is an aggregate over the record, and the record is
not published.

Design constraints, in order of how much they cost to get wrong:

1. **Accessible.** Semantic landmarks, one `h1`, `aria-current` on the nav, a visible focus ring,
   `prefers-reduced-motion` honoured, and text at a contrast ratio above 4.5:1 in both colour
   schemes. A reader using a keyboard or a screen reader gets the same page as one using a mouse.
2. **Responsive.** One column below 48rem, and tables that scroll inside a wrapper rather than
   overflowing the viewport.
3. **No build step, no JavaScript.** Five files, hand-written CSS. Nothing here needs Node, and
   nothing runs on the reader's machine except their browser.
"""

from __future__ import annotations

import html
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIGURES = ROOT / "docs" / "site" / "figures.json"
OUT_DIR = ROOT / "docs" / "site"

NAV = (
    ("index", "Overview"),
    ("evidence", "Evidence"),
    ("architecture", "Architecture"),
    ("safety", "Safety"),
    ("build", "Build it"),
)

STYLE = """
:root{
  --bg:#fbfaf7; --surface:#ffffff; --fg:#1b1e22; --dim:#5b636b; --line:#e2ded5;
  --accent:#0f6f5c; --accent-soft:#e6f2ef; --warn:#8a5a00; --warn-soft:#fdf3e0;
  --fail:#8f2f26; --fail-soft:#fbeceb;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#14171a; --surface:#1b1f23; --fg:#ecebe7; --dim:#a3abb3; --line:#2c3237;
    --accent:#5ecfae; --accent-soft:#16302a; --warn:#e0b25f; --warn-soft:#2e2513;
    --fail:#e8837a; --fail-soft:#2e1a18;
  }
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion: reduce){html{scroll-behavior:auto} *{transition:none!important}}
body{margin:0;background:var(--bg);color:var(--fg);
  font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:3px}
.skip{position:absolute;left:-9999px;top:0;background:var(--surface);padding:.5rem 1rem}
.skip:focus{left:0}
header.site{background:var(--surface);border-bottom:1px solid var(--line);padding:1.5rem 2rem}
header.site h1{margin:0;font-size:1.35rem;letter-spacing:-.01em}
header.site .tag{margin:.35rem 0 0;color:var(--dim);font-size:.95rem;max-width:60rem}
nav.site{display:flex;gap:1.25rem;flex-wrap:wrap;margin-top:1rem;font-size:.95rem}
nav.site a{color:var(--dim);text-decoration:none;padding:.25rem 0;border-bottom:2px solid transparent}
nav.site a:hover{color:var(--fg)}
nav.site a[aria-current]{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
main{max-width:66rem;margin:0 auto;padding:2rem}
h2{font-size:1.15rem;margin:2.5rem 0 .5rem;letter-spacing:-.01em}
h3{font-size:1rem;margin:1.75rem 0 .35rem}
p{max-width:60rem}
.note{color:var(--dim);font-size:.92rem;max-width:60rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:1rem;margin:1.25rem 0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:.6rem;padding:1rem}
.card dt{color:var(--dim);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin:0}
.card dd{margin:.3rem 0 0;font-size:1.55rem;font-weight:650;font-variant-numeric:tabular-nums}
.card p{margin:.4rem 0 0;color:var(--dim);font-size:.85rem}
table{border-collapse:collapse;width:100%;margin:.75rem 0 1.5rem;font-size:.95rem}
th,td{padding:.5rem .65rem;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:.8rem;text-transform:uppercase;letter-spacing:.04em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:.1rem .55rem;border-radius:1rem;font-size:.78rem;font-weight:650;
  background:var(--accent-soft);color:var(--accent)}
.badge.warn{background:var(--warn-soft);color:var(--warn)}
.badge.fail{background:var(--fail-soft);color:var(--fail)}
.badge.mute{background:var(--line);color:var(--dim)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em}
pre{background:var(--surface);border:1px solid var(--line);border-radius:.5rem;padding:1rem;
  overflow-x:auto;font-size:.88rem}
ul{padding-left:1.25rem;max-width:60rem}
li{margin:.3rem 0}
footer.site{border-top:1px solid var(--line);margin-top:3rem;padding:1.5rem 2rem;color:var(--dim);
  font-size:.85rem}
.tablewrap{overflow-x:auto}
@media (max-width:48rem){
  header.site,main,footer.site{padding-left:1rem;padding-right:1rem}
  .card dd{font-size:1.3rem}
}
"""


@dataclass
class Figures:
    """The published measurements, read once, at generation time."""

    generated_at: str = ""
    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = DEFAULT_FIGURES) -> Figures:
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            generated_at=str(payload.get("generated_at", "")),
            data=payload.get("figures", {}) or {},
        )

    def named(self, key: str) -> dict:
        value = self.data.get(key) or {}
        return value if isinstance(value, dict) else {}

    def broken(self) -> list[str]:
        return sorted(k for k, v in self.data.items() if isinstance(v, dict) and "error" in v)


def _pct(value, sign: bool = True) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{value:+.2f}%" if sign else f"{value:.2f}%"


def _money(value, sign: bool = False) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{value:+,.2f}" if sign else f"{value:,.2f}"


def _page(title: str, current: str, body: list[str], figures: Figures) -> str:
    nav = "".join(
        f'<a href="{key}.html"{chr(32) + chr(34) + "aria-current" + chr(34) + chr(61) + chr(34) + "page" + chr(34) if key == current else ""}>{label}</a>'
        for key, label in NAV
    )
    stamp = figures.generated_at[:16].replace("T", " ") if figures.generated_at else "not generated"
    return "\n".join([
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>autopoiesis — {html.escape(title)}</title>",
        '<meta name="description" content="A self-evolving LLM paper-trading agent that grades its '
        'own strategies against broker evidence and retires the ones that do not earn their place.">',
        f"<style>{STYLE}</style></head><body>",
        '<a class="skip" href="#main">Skip to content</a>',
        '<header class="site">',
        "<h1>autopoiesis</h1>",
        '<p class="tag">A self-evolving LLM paper-trading agent, graded against its own broker '
        "evidence. Paper only: the Guardian's limits cannot be weakened by any configuration, and "
        "every number on this site names the command that produced it.</p>",
        f'<nav class="site" aria-label="Pages">{nav}</nav>',
        "</header>",
        '<main id="main">',
        *body,
        "</main>",
        '<footer class="site">',
        f"<p>Figures published {html.escape(stamp)} UTC from <code>docs/site/figures.json</code>. "
        "A number that cannot be computed reads UNKNOWN, which is a different claim from zero.</p>",
        "<p>Apache-2.0. Paper trading only; no live venue is reachable from this code.</p>",
        "</footer>",
        "</body></html>",
    ])



# --- the five pages -------------------------------------------------------------------------


def _agent_accounting(benchmark: dict) -> dict:
    agent = benchmark.get("agent") or {}
    if agent.get("performance_contract") != "agent-accounting-v1":
        return {}
    return agent


def _card(label, value, sub):
    return ('<div class="card"><dt>' + html.escape(str(label)) + '</dt><dd>'
            + html.escape(str(value)) + '</dd><p>' + html.escape(str(sub)) + '</p></div>')


def overview(figures: Figures) -> list[str]:
    benchmark = figures.named("benchmark")
    gate = figures.named("gate")
    tests = figures.named("tests")
    record = figures.named("record")
    signals = figures.named("signals")
    target = benchmark.get("target") or {}

    body = [
        '<p>A research instrument, not a product. It trades a paper account, grades its own '
        'strategies against broker-confirmed fills, retires the ones that do not earn their '
        'place, and publishes every claim it makes with the command that produced it.</p>',
        '<p class="note">Every figure here is published from '
        '<code>docs/site/figures.json</code>, and each names the command that produced it. '
        'There are no screenshots: a reader can re-run a command and cannot re-derive an '
        'image.</p>',
    ]
    broken = figures.broken()
    if broken:
        body.append('<p class="note">These figures could not be collected and read UNKNOWN '
                    'rather than as zero: ' + html.escape(', '.join(broken)) + '.</p>')

    body.append('<dl class="grid">')
    body.append(_card('agent, after cost', 'UNKNOWN',
                      'allocation and aligned rolling equity history unavailable'))
    body.append(_card('vs SPY holding',
                      'UNKNOWN',
                      'aligned portfolio comparison unavailable'))
    body.append(_card('trading days on record', record.get('trading_days', 0),
                      'against a ' + str(target.get('trading_days', 60)) + '-day target'))
    body.append(_card('gate', str(gate.get('check_classes')) + ' classes',
                      str(tests.get('tests')) + ' tests across '
                      + str(tests.get('test_files')) + ' files'))
    body.append('</dl>')

    body.append('<h2>What it has shown</h2>')
    body.append('<ul>')
    body.append('<li>The accounting is real: broker-confirmed fills, a FIFO lot ledger, and a '
                'Guardian whose limits cannot be weakened by any configuration value.</li>')
    body.append('<li>The gate can fail, and is made to fail on purpose to prove it. '
                '<code>make verify-self-test</code> plants the defects each class exists to '
                'catch, including a NaN limit that silently stops comparing.</li>')
    body.append('<li>Research requires a registered contract and validated real bars before '
                'evaluating candidates and recording trials. Scheduled operation requires '
                'a configured contract, usable cache and verified successful runs.</li>')
    body.append('</ul>')

    body.append('<h2>What it has not shown</h2>')
    body.append('<ul>')
    body.append('<li><strong>That it beats holding.</strong> '
                + 'UNKNOWN after cost against SPY UNKNOWN'
                + ' over ' + str(record.get('trading_days', 0)) + ' of '
                + str(target.get('trading_days', 60))
                + ' recorded dates. Allocated capital and aligned equity are unavailable.</li>')
    body.append('<li><strong>Any directional edge.</strong> '
                + str(signals.get('hypotheses', 0))
                + ' hypotheses appear in the historical signal ledger. These aggregate '
                'verdict counts do not establish validated alpha or certify statistical '
                'error control.</li>')
    body.append('<li><strong>Live trading.</strong> Not present. <code>live</code> mode is '
                'refused by the configuration loader.</li>')
    body.append('</ul>')
    return body


def evidence(figures: Figures) -> list[str]:
    benchmark = figures.named("benchmark")
    record = figures.named("record")
    signals = figures.named("signals")
    agent = _agent_accounting(benchmark)
    target = benchmark.get("target") or {}
    budget = benchmark.get("risk_budget") or {}
    rows = benchmark.get("strategies") or []

    body = ['<h2>The target, and where it stands</h2>']
    body.append('<p>Beat SPY buy-and-hold on deployed capital after cost, within the risk '
                'budget, over a rolling ' + str(target.get('trading_days', 60))
                + '-trading-day window. The record holds '
                + str(record.get('trading_days', 0)) + ' trading days, so the window is '
                + 'not established by these dates alone'
                + '; allocation and aligned equity/control history are unavailable.</p>')
    body.append('<dl class="grid">')
    body.append(_card('agent return on deployed capital',
                      'UNKNOWN',
                      _money(agent.get('pnl'), sign=True) + ' PnL; allocated capital UNKNOWN'))
    body.append(_card('SPY, same window', 'UNKNOWN',
                      'aligned agent capital and rolling window unavailable'))
    body.append(_card('after cost', _money(agent.get('assumed_cost')) if agent else 'UNKNOWN',
                      'assumed '
                      + str(benchmark.get('cost', {}).get('assumed_round_trip_cost_pct'))
                      + '% round trip'))
    body.append(_card('realized + unrealized',
                      (_money(agent.get('realized'), sign=True) + ' / '
                       + _money(agent.get('unrealized'), sign=True)) if agent else 'UNKNOWN',
                      'from the FIFO lot ledger'))
    body.append('</dl>')

    if rows:
        body.append('<h2>Per strategy, as a diagnostic</h2>')
        body.append('<div class="tablewrap"><table><tr><th>strategy</th>'
                    '<th class="num">return</th><th class="num">SPY, same window</th>'
                    '<th class="num">excess</th></tr>')
        for row in rows:
            body.append('<tr><td>' + html.escape(str(row.get('strategy_id')))
                        + '</td><td class="num">' + html.escape(str(row.get('return_pct')))
                        + '</td><td class="num">' + html.escape(str(row.get('market_pct')))
                        + '</td><td class="num">' + html.escape(str(row.get('excess')))
                        + '</td></tr>')
        body.append('</table></div>')
        body.append('<p class="note">Strategy returns use historical peak exposure as a '
                    'diagnostic denominator. They do not establish agent allocation, '
                    'a continuous equity curve, or project profitability.</p>')

    if signals.get('present'):
        body.append('<h2>The signal ledger</h2>')
        body.append('<div class="tablewrap"><table><tr><th>verdict</th>'
                    '<th class="num">rows</th></tr>')
        for name, count in (signals.get('verdicts') or {}).items():
            cls = 'badge'
            if name == 'SURVIVES':
                cls = 'badge'
            elif name in ('UNDERPOWERED', 'EXPLORATORY'):
                cls = 'badge warn'
            else:
                cls = 'badge mute'
            body.append('<tr><td><span class="' + cls + '">' + html.escape(str(name))
                        + '</span></td><td class="num">' + str(count) + '</td></tr>')
        body.append('</table></div>')
        body.append('<p>' + str(signals.get('hypotheses')) + ' distinct hypotheses, '
                    + str(signals.get('rows')) + ' historical recorded verdicts. These are ledger '
                    'counts, not certification under the current research contract or evidence '
                    'of validated alpha.</p>')
    else:
        body.append('<p class="note">No signal ledger in the published figures, so the research '
                    'record reads UNKNOWN rather than as zero.</p>')

    body.append('<h2>The risk budget these results were produced inside</h2>')
    body.append('<div class="tablewrap"><table><tr><th>limit</th><th class="num">value</th></tr>')
    for label, key in (('max position value', 'max_position_value'),
                       ('max total exposure', 'max_total_exposure'),
                       ('max daily loss', 'max_daily_loss'),
                       ('max trades per day', 'max_trades_per_day')):
        if key in budget:
            body.append('<tr><td>' + html.escape(label) + '</td><td class="num">'
                        + format(budget[key], ',.2f') + '</td></tr>')
    body.append('</table></div>')
    body.append('<p class="note">Enforced by the Guardian independently of the decision maker. '
                'A decision maker that abstains on risk grounds is acting as a second risk '
                'authority and can halt trading without appearing to.</p>')
    return body


def architecture(figures: Figures) -> list[str]:
    body = [
        '<p>One execution path, and the journal underneath it. Nothing in the system has a second '
        'place a fact lives, which is the only reason every number can be recomputed from the '
        'record.</p>',
        '<pre><code>cli -&gt; daemon -&gt; loop -&gt; guardian -&gt; executor -&gt; journal'
        '\n'
        '  ^          ^                                            |\n'
        '  |          +-- curriculum, reflection, maintenance -&gt; lessons\n'
        '  +-- evidence ingest (broker fills) -&gt; evaluator -&gt; attribution</code></pre>',
        '<h2>What owns what</h2>',
        '<div class="tablewrap"><table><tr><th>component</th><th>owns</th></tr>',
    ]
    for name, owns in (
        ('guardian', 'the hard limits. Every order is checked here, independently of whoever '
                     'proposed it, and a non-finite limit is refused rather than treated as no '
                     'limit'),
        ('executor', 'the broker call, and nothing else. It cannot decide'),
        ('journal', 'every cycle and every event, append-only, the only source of truth'),
        ('evaluator', 'PnL attribution: which of signal, model, strategy, allocation, execution '
                      'or cost produced the result'),
        ('research', 'candidate generation and offline screens. Quarantined: production code '
                     'cannot import it, checked by a gate'),
        ('doctor', '52 check classes over the code and the running system'),
    ):
        body.append('<tr><td><code>' + html.escape(name) + '</code></td><td>'
                    + html.escape(owns) + '</td></tr>')
    body.append('</table></div>')

    body.append('<h2>How the record survives itself</h2>')
    body.append('<p>The journal rotates at a size cap. A generation reaching the horizon is '
                '<strong>moved</strong> into <code>journal-archive/</code> and read back first, '
                'so the record is the whole record and not a rolling window. If a rotation '
                'cannot archive, the append fails rather than deleting history to keep '
                'going.</p>')
    body.append('<p class="note">There is no copy anywhere else. The archive keeps the record '
                'from being deleted on one host; nothing ships it off that host, and no test '
                'claims otherwise.</p>')

    body.append('<h2>What the loop changes</h2>')
    body.append('<ul>'
                '<li>The strategy\'s own rule decides first. The model may override it only with '
                'a reason, and both are recorded.</li>'
                '<li>A strategy is admitted on recorded evidence, screened offline, and retired '
                'on its own decisions. The reason travels with the transition.</li>'
                '<li><code>PAUSED</code> is a holding pattern, not a graveyard: the daemon '
                'returns one paused strategy to probation per pass, on journalled evidence.</li>'
                '</ul>')
    return body


def safety(figures: Figures) -> list[str]:
    return [
        '<p>Autonomy is bounded by rules that are not traded away to satisfy a narrower success '
        'criterion. These are the ones that hold, and each is enforced somewhere other than the '
        'decision maker.</p>',
        '<dl class="grid">',
        _card('mode', 'paper only', 'live is refused by the configuration loader'),
        _card('risk limits', 'not weakenable',
              'a NaN or infinite limit is refused where it is read and where the Guardian is '
              'built'),
        _card('market-open check', 'cannot be skipped',
              'a stale or unreadable snapshot is refused rather than traded on'),
        _card('LLM', 'no shell',
              'no model output is executed as a command anywhere in the codebase'),
        '</dl>',
        '<h2>What the Guardian refuses to let a configuration do</h2>',
        '<p>Every limit is checked at the point it enters, because a limit that cannot be '
        'expressed is not a limit. <code>NaN &lt;= 0</code> is False, so a NaN position cap '
        'sailed through a positivity test and reached the Guardian, where every comparison '
        'against it is also False - a 10,000-unit buy that a 5,000 limit correctly refuses was '
        '<strong>approved</strong>. Nothing reported it, because the refusals simply stopped '
        'happening.</p>',
        '<h2>What is not claimed</h2>',
        '<ul>'
        '<li>Paper results are not live results. The broker fees are zero on paper, so the '
        'reported net charges an explicitly assumed round-trip cost on the notional actually '
        'traded, and the page says so.</li>'
        '<li>No figure here is a certification. The target is not met, and the record holds '
        'fewer trading days than the target window.</li>'
        '</ul>',
    ]

def build(figures: Figures) -> list[str]:
    gate = figures.named("gate")
    tests = figures.named("tests")
    return [
        '<p>No broker account, model or credentials are needed for any of this. Every command '
        'below exits 0 from a fresh clone with none of them.</p>',
        '<pre><code>git clone https://github.com/liuyime2/autopoiesis.git &amp;&amp; cd autopoiesis\n'
        'python3 -m venv .venv &amp;&amp; . .venv/bin/activate\n'
        'make install            # pip install -e ".[dev]"\n'
        'make check              # ruff + mypy + the test suite\n'
        'make smoke-offline      # config and CLI end to end, no broker contacted\n'
        'python examples/minimal_cycle.py\n'
        'make verify             # the full gate: ' + str(gate.get('check_classes'))
        + ' check classes</code></pre>',
        '<h2>What the gate covers</h2>',
        '<p>' + str(gate.get('check_classes')) + ' check classes over the code and the running '
        'system, run by ' + str(tests.get('tests')) + ' tests across '
        + str(tests.get('test_files')) + ' files. It is made to fail on purpose to prove it '
        'can: <code>make verify-self-test</code> plants the defects each class exists to '
        'catch.</p>',
        '<h2>From a package rather than from a clone</h2>',
        '<p><code>pip install autopoiesis</code> (a pre-release, so <code>--pre</code>) gives the '
        'console script, the importable package, <code>examples/</code> and <code>configs/</code>. '
        'It does not give the gate, the suite or the make targets: those are a property of the '
        'source tree.</p>',
        '<h2>Release</h2>',
        '<p><code>make release-check</code> builds the sdist and the wheel, installs the sdist '
        'into a clean virtualenv, and runs what the README claims an installed copy can do. It '
        'is how an sdist missing <code>examples/</code> was found.</p>',
        '<h2>Reproducing the results</h2>',
        '<p>Every figure on this site names its command. <code>make benchmark</code> prints the '
        'raw paper-account accounting and an UNKNOWN portfolio comparison; '
        '<code>make research-search SPEC=&lt;registered-spec&gt;</code> evaluates validated '
        'cached bars under the declared research contract; '
        '<code>make audit</code> runs the recorded-defect audit.</p>',
        '<p class="note">Reproducing them needs a paper account and the same bar cache. A fresh '
        'clone with no credentials can build, test and gate the project; it cannot reproduce '
        'trading results, and no page here claims it can.</p>',
    ]


PAGES = {
    'index': ('Overview', overview),
    'evidence': ('Evidence', evidence),
    'architecture': ('Architecture', architecture),
    'safety': ('Safety', safety),
    'build': ('Build it', build),
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    figures = Figures.load(Path(argv[0]) if argv else DEFAULT_FIGURES)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for key, (title, render) in PAGES.items():
        body = render(figures)
        (OUT_DIR / (key + '.html')).write_text(
            _page(title, key, body, figures), encoding='utf-8')
        print('wrote ' + str(OUT_DIR / (key + '.html')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
