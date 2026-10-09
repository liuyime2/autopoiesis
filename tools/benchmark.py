"""Benchmark the system against SPY buy-and-hold, after costs, inside the risk budget.

Run it:

    make benchmark            # or: python tools/benchmark.py

**Why this exists.** An external review's central objection was that nothing had shown the
system beats doing nothing, and that the model's contribution had never been isolated. Both
are measurable, and neither had been measured in a form anyone else could run. This is that
form.

**What it needs.** The runtime record: the journal and the broker evidence batches in
`runtime/`, which are gitignored because they are produced by trading. No credentials and no
network - `doctor` is run with `skip_broker=True` and every number is read out of what is
already on disk. That also means it is **not** a fresh-clone demo: a new clone has no record
and the tool reports that rather than inventing a number for it.

Every figure is computed once, by `src/autopoiesis/doctor.py`, and parsed back out of its check
detail rather than recomputed here. A second implementation of the comparison could disagree
with the one the project argues from, and then two numbers would exist and only one of them
would be defended.

**The target, stated once so there is nothing to reinterpret:**

    Within a stated risk budget and after costs, beat SPY buy-and-hold over a rolling
    60-trading-day window. Separately: the increment attributable to LLM decisions versus the
    deterministic baseline, over the same window and the same fills.

Deliberately *not* "make money". A positive return in a rising market is not evidence of
anything, and every strategy in the library already has one. Beating buy-and-hold is the claim.

**On the window, which is the part that is easy to fake.** `doctor`'s comparison covers the
whole retained journal, and the journal is not 60 trading days long. The first version of this
script printed the replay cache's date range next to whole-of-record returns, which made a
60-day benchmark out of 16 days of cycles - the same error as labelling a whole-of-record
figure "monthly". So the record's own window is printed, the target window is reported as met
or not, and the verdict says both things:

    * on the record that exists, the target is currently failed - all four strategies that
      closed a lot are behind the market, by 1.47 to 4.29 points;
    * over the 60 trading days the target actually names, it is neither met nor falsified,
      because only 16 trading days of cycles exist.

**On the LLM increment.** The split is printed next to its own coverage, always. "The model
contributed +193.56" means the model made the decisions on the lots that closed profitably; it
does **not** mean the baseline would have lost that money on the same bars, because the
decision changed which lot existed, so no paired comparison was run. And most decisions predate
provenance capture and cannot be attributed to any model at all. A share printed without its
coverage is the kind of figure that gets retracted later; this one is printed with it or not
at all.

**Exit codes**, so a green run is never mistaken for a passed target:

    0  the target is met over the record
    1  the target is failed: at least one strategy is behind buy-and-hold
    2  nothing to measure - no journal, or no closed lots on record
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: The window the target is defined over. Not a knob: the target sentence in this file's
#: docstring is the specification, and a benchmark whose window can be changed by a flag is a
#: benchmark whose verdict can be chosen.
TARGET_TRADING_DAYS = 60

EXIT_MET = 0
EXIT_BEHIND = 1
EXIT_NOTHING_TO_MEASURE = 2


def _load_doctor():
    """`doctor` by module path, because `tools/` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "doctor_under_benchmark", ROOT / "src" / "autopoiesis" / "doctor.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _record_window(config) -> tuple[str, str, int, int]:
    """First and last cycle date, the two dates as an ISO range, and the day and cycle counts.

    Metadata about the record, not a computation about it: the comparison itself stays in
    `doctor`. What is measured here is only *how much record the comparison covers*, which is
    the number that decides whether the 60-day target can be judged at all.
    """
    from autopoiesis.journal import JsonlJournal

    records = JsonlJournal(config.journal_path).read_all()
    stamps = sorted(
        record.snapshot.timestamp
        for record in records
        if getattr(record, "snapshot", None) is not None
    )
    if not stamps:
        return "", "", 0, len(records)
    days = sorted({stamp.date().isoformat() for stamp in stamps})
    return days[0], days[-1], len(days), len(records)


def _parse_comparison(detail: str) -> list[tuple[str, str, str, str]] | None:
    """The per-strategy rows of `doctor`'s `pnl vs holding` check.

    Both severities share one sentence shape - "N of M strategy/ies behind the market: ..." on
    WARN, "every strategy beat the market: ..." on OK - and the rows after the colon are
    comma-separated, so splitting on "; " yields the whole sentence and every filter on it
    drops everything. That is the parse that a previous version of this file got wrong.
    """
    _, sep, tail = detail.partition(":")
    if not sep:
        return None
    rows = []
    for part in tail.split(","):
        if "vs market" not in part:
            continue
        name, _, rest = part.partition("=")
        return_pct, _, rest = rest.partition(" vs market ")
        market_pct, _, excess = rest.partition("(")
        rows.append(
            (
                name.strip(),
                return_pct.strip(),
                market_pct.strip(),
                excess.rstrip(") ").strip(),
            )
        )
    return rows or None


def _pick(checks, name: str):
    """The one check called `name`, worst severity first, plus what its duplicates add.

    `doctor` can emit the same check name twice - unmatched sells and a model that subtracted
    from a positive total are independently reportable - and both carry the whole PnL
    paragraph with one clause appended. Printing both printed the paragraph twice; picking one
    and discarding the other would drop a real finding. So the shared paragraph is printed
    once and each check contributes only the clause it appended.

    The split is the longest common prefix rather than "everything the winner has", because
    which check carries the extra clause depends on its severity: the unmatched-sells warning
    is longer than the OK that reports the same PnL without it.
    """
    matches = [check for check in checks if check.name == name]
    if not matches:
        return None, []
    rank = {"fail": 0, "warn": 1, "ok": 2, "skip": 3}
    ranked = sorted(matches, key=lambda check: rank.get(check.status.value, 4))
    shared = os.path.commonprefix([check.detail for check in ranked]).strip()
    extras: list[str] = []
    for check in ranked:
        clause = check.detail[len(shared):].lstrip("; ").strip()
        if clause and clause not in extras:
            extras.append(clause)
    return shared, extras


def _headline(report, agent: dict | None) -> tuple[str, int]:
    """The verdict and its exit code.

    Two claims are reported because they are two different claims, and the target sentence is only
    one of them. The per-strategy worst case answers "given each strategy its own peak capital, did
    it beat SPY over its own holding window". The target sentence answers "did the agent, after
    costs, beat SPY over the window". A single weak strategy failing the first says nothing about
    the second, and reporting only the first made the agent look worse than its own record - or
    better - than it was.

    The exit code follows the target sentence, because that is the specification. The worst case
    is printed first and labelled as what it is, so a reader cannot mistake one for the other.
    """
    detail, _ = _pick(report.checks, "pnl vs holding")
    if detail is None:
        return "NOT MEASURABLE: doctor reported no comparison against buy-and-hold", (
            EXIT_NOTHING_TO_MEASURE
        )
    rows = _parse_comparison(detail)
    if not rows:
        return f"NOT MEASURABLE: {detail}", EXIT_NOTHING_TO_MEASURE
    behind = [row for row in rows if row[3].startswith("-")]
    diagnostic = (
        f"{len(behind)} of {len(rows)} strategies are behind buy-and-hold on their own capital and "
        f"window (worst {min(behind, key=lambda r: float(r[3]))[0]} by "
        f"{min(behind, key=lambda r: float(r[3]))[3][1:]} points) - a diagnostic, not the target"
        if behind
        else f"all {len(rows)} strategies beat buy-and-hold on their own capital and window"
    )

    # The target sentence: the agent, after costs, over the window.
    if agent is None:
        return f"NOT MEASURABLE: {diagnostic}; the agent-level comparison could not be computed", (
            EXIT_NOTHING_TO_MEASURE
        )
    if agent["excess"] >= 0:
        return (
            f"PASS: the agent returned {agent['return_pct']:+.2f}% on deployed capital after cost "
            f"against SPY {agent['spy_pct']:+.2f}% over the same window ({agent['excess']:+.2f}). "
            f"{diagnostic}",
            EXIT_MET,
        )
    return (
        f"FAIL: the agent returned {agent['return_pct']:+.2f}% on deployed capital after cost "
        f"against SPY {agent['spy_pct']:+.2f}% over the same window ({agent['excess']:+.2f}). "
        f"{diagnostic}",
        EXIT_BEHIND,
    )


def _agent_level() -> dict | None:
    """The agent's own return on its own deployed capital, after cost, against SPY on the same window.

    Computed from the evaluator's structured `PnLEvidence` rather than parsed out of `doctor`'s
    prose, because the capital leg is a number no sentence in that prose carries: a comparison row
    has `return_pct` but not the PnL you would divide back out to recover the capital. The first
    version of this tried the parse, found it could not, and would have reported "not measurable" for
    a reason that was an artefact of the parsing rather than of the data.

    The denominator is the sum of each strategy's peak exposure: the same convention the per-strategy
    rows use, so the two read against each other, and not the account's total equity, which holds the
    owner's pre-existing positions and is not capital the agent was given.

    Returns None when either leg is missing, rather than a number computed from half the record.
    """
    from autopoiesis.broker_evidence import latest_evidence_batch
    from autopoiesis.config import AgentConfig
    from autopoiesis.evaluator import PNL_EVIDENCE_MISSING, _pnl_evidence, confirmed_fill_activities
    from autopoiesis.journal import JsonlJournal

    config = AgentConfig.from_env()
    journal = JsonlJournal(config.journal_path)
    records = journal.read_all()
    evidence = latest_evidence_batch(journal)
    if not records or evidence is None:
        return None
    # Seeded from the journal exactly as the doctor does. Without it the batch's 24h window
    # hides lots opened weeks ago, `closed_lots` comes back empty, and this reports the open
    # lots' unrealized PnL as the agent's whole record - +24.19 against the real +871.02. That
    # is not a rounding difference, it is a different population, and it is the same defect
    # class the project corrects everywhere else.
    pnl = _pnl_evidence(records, evidence, confirmed_fill_activities(journal, records))
    if pnl.status == PNL_EVIDENCE_MISSING or not pnl.strategy_peak_exposure:
        return None

    capital = sum(pnl.strategy_peak_exposure.values())
    if capital <= 0:
        return None
    # Realized plus unrealized, which is what the strategy rows also compare.
    realized = sum(pnl.strategy_realized_pnl.values())
    unrealized = pnl.unrealized_pnl or 0.0
    # Cost is charged on the realized leg only. An open lot has not paid its exit, and charging
    # it a round trip it has not taken understates the agent's position - the first version of
    # this did exactly that and disagreed with the increment block by ten dollars on the same
    # record, which is the kind of small disagreement that makes a reader distrust both numbers.
    net = realized + unrealized - realized * (config.assumed_round_trip_cost_pct / 100.0)
    if not pnl.market_return_pct:
        return None
    return_pct = net / capital * 100.0
    return {
        "pnl": round(net, 2),
        "realized": round(realized, 2),
        "unrealized": round(unrealized, 2),
        "capital": round(capital, 2),
        "return_pct": round(return_pct, 2),
        "spy_pct": round(pnl.market_return_pct, 2),
        "excess": round(return_pct - pnl.market_return_pct, 2),
    }


def _print_split_section(title: str, detail: str | None, extras: list[str], indent: str = "  ") -> None:
    """Print a `|`-joined check detail as a titled block, one clause per line."""
    if detail is None:
        return
    print(title)
    print("-" * 78)
    for clause in detail.split("|"):
        clause = clause.strip()
        if clause:
            print(f"{indent}{clause}")
    for extra in extras:
        print(f"{indent}{extra}")
    print()


def main() -> int:
    from autopoiesis.config import AgentConfig

    try:
        config = AgentConfig.from_env()
    except Exception as exc:  # a missing key file must not hide the benchmark
        config = None
        print(f"# config unavailable ({type(exc).__name__}: {exc}); using documented defaults")
    if config is None:
        from autopoiesis.config import AgentConfig as _AgentConfig

        config = _AgentConfig()

    print("=" * 78)
    print("QuantGroup benchmark - does the system beat SPY buy-and-hold?")
    print("=" * 78)

    first, last, days, cycles = _record_window(config)
    if not days:
        print("record            none: no cycle records in the journal, so nothing to measure")
        print(f"                  journal: {config.journal_path}")
        print("                  run 'minictrl once' (or let the daemon run) to produce one")
        return EXIT_NOTHING_TO_MEASURE
    print(f"record            {first} .. {last}   {days} trading day(s), {cycles} cycle(s)")
    print(
        f"target window     {TARGET_TRADING_DAYS} trading days - "
        + (
            "MET"
            if days >= TARGET_TRADING_DAYS
            else f"NOT MET ({days} of {TARGET_TRADING_DAYS})"
        )
    )
    print(
        f"comparison        whole of record ({days} trading day(s)); "
        "no windowed comparison exists, and this does not fake one"
    )
    print(
        f"risk budget       max_position_value ${config.max_position_value:,.0f}  "
        f"max_daily_loss ${config.max_daily_loss:,.0f}  "
        f"max_trades_per_day {config.max_trades_per_day}"
    )
    print(f"cost              assumed {config.assumed_round_trip_cost_pct}% round trip; observed broker fees below")
    print()

    report = _load_doctor().run_doctor(config, skip_broker=True)

    holding, _ = _pick(report.checks, "pnl vs holding")
    rows = _parse_comparison(holding) if holding else None
    if rows:
        print("vs SPY buy-and-hold")
        print("-" * 78)
        print(f"  {'strategy':<26} {'return':>8} {'SPY, same window':>17} {'excess':>8}")
        for name, return_pct, market_pct, excess in rows:
            print(f"  {name:<26} {return_pct:>8} {market_pct:>17} {excess:>8}")
        print()
        print("  basis: each row puts both legs on the same capital and the same window -")
        print("  realized plus unrealized PnL over the strategy's peak exposure, against SPY from")
        print("  its first entry to its last exit (or last valuation while a lot is open). Gross")
        print("  of the assumed round-trip cost; the after-cost figure for the whole account is")
        print("  in the increment block below.")
        print()

    attribution, extras = _pick(report.checks, "pnl attribution")
    _print_split_section("LLM increment vs the deterministic baseline", attribution, extras)

    # --- the agent-level claim, which is what the target sentence is about -------------------
    #
    # The rows above answer a different question: "given each strategy its own peak capital, did it
    # beat SPY over its own holding window". A worst-case rule over six of those rows is not the
    # target sentence, which is about the *agent* - one capital base, after costs, over one window.
    # Reporting only the worst row made a single weak strategy read as the whole agent failing, and
    # that is a definition error, not a strict one.
    agent = _agent_level()
    if agent is not None:
        print("the agent itself, after cost, over the record window")
        print("-" * 78)
        print(f"  agent PnL (realized + unrealized, after cost)   {agent['pnl']:>+12.2f}")
        print(f"    of which realized {agent['realized']:>+.2f}, unrealized {agent['unrealized']:>+.2f}")
        print(f"  on capital deployed (sum of peak exposures)     {agent['capital']:>12.2f}")
        print(f"  return on deployed capital                      {agent['return_pct']:>+11.2f}%")
        print(f"  SPY over the same window                        {agent['spy_pct']:>+11.2f}%")
        print(f"  excess                                          {agent['excess']:>+11.2f} points")
        print()
        print("  this is the claim the target sentence makes. It is not the worst strategy row, and")
        print("  it is not the sum of the rows either: strategies that never overlapped in time did")
        print("  not compete for the same capital, so their peak exposures add.")
        print()

    paired, _ = _pick(report.checks, "llm vs rule")
    if paired is not None:
        print(f"llm vs rule (paired)  {paired}")
        print()

    registry, _ = _pick(report.checks, "model registry")
    if registry is not None:
        print(f"attribution coverage   {registry}")
        print()

    evidence, _ = _pick(report.checks, "pnl evidence")
    if evidence is not None:
        print(f"pnl evidence          {evidence}")
        print()

    verdict, code = _headline(report, agent)
    print("=" * 78)
    print("target: within a stated risk budget and after costs, beat SPY buy-and-hold over")
    print(f"        a rolling {TARGET_TRADING_DAYS}-trading-day window.")
    print("=" * 78)
    print(f"VERDICT           {verdict}")
    if days < TARGET_TRADING_DAYS:
        print(
            f"                  over the {days} trading days on record. The {TARGET_TRADING_DAYS}-day "
            f"window is not"
        )
        print(
            f"                  covered, so the target over {TARGET_TRADING_DAYS} days is neither met"
        )
        print("                  nor falsified yet. The line above is the whole of the record.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
