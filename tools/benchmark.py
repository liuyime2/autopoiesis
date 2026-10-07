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

Every figure is computed once, by `src/min_agent/doctor.py`, and parsed back out of its check
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
        "doctor_under_benchmark", ROOT / "src" / "min_agent" / "doctor.py"
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
    from min_agent.journal import JsonlJournal

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


def _headline(report) -> tuple[str, int]:
    """The verdict and its exit code, from the same check the table was printed from."""
    detail, _ = _pick(report.checks, "pnl vs holding")
    if detail is None:
        return "NOT MEASURABLE: doctor reported no comparison against buy-and-hold", (
            EXIT_NOTHING_TO_MEASURE
        )
    rows = _parse_comparison(detail)
    if not rows:
        return f"NOT MEASURABLE: {detail}", EXIT_NOTHING_TO_MEASURE
    behind = [row for row in rows if row[3].startswith("-")]
    if behind:
        worst = max(behind, key=lambda row: float(row[3]))
        return (
            f"FAIL: {len(behind)} of {len(rows)} strateg"
            f"{'y is' if len(behind) == 1 else 'ies are'} behind buy-and-hold, "
            f"worst {worst[0]} by {worst[3][1:]} points",
            EXIT_BEHIND,
        )
    return f"PASS: all {len(rows)} strateg{'y' if len(rows) == 1 else 'ies'} beat buy-and-hold", (
        EXIT_MET
    )


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
    from min_agent.config import AgentConfig

    try:
        config = AgentConfig.from_env()
    except Exception as exc:  # a missing key file must not hide the benchmark
        config = None
        print(f"# config unavailable ({type(exc).__name__}: {exc}); using documented defaults")
    if config is None:
        from min_agent.config import AgentConfig as _AgentConfig

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
        print(f"  {'strategy':<26} {'return':>8} {'vs SPY':>9}")
        for name, return_pct, market_pct, excess in rows:
            print(f"  {name:<26} {return_pct:>8} {excess:>9}")
        print(f"  {'buy-and-hold (SPY)':<26} {rows[0][2]:>8} {'---':>9}   <- the benchmark to beat")
        print()
        print(
            "  basis: return is on the capital each strategy deployed (its peak exposure) and"
        )
        print(
            "  is gross of the assumed round-trip cost. The after-cost figure for the whole"
        )
        print(
            "  account is in the increment block below; it is not broken out per strategy here."
        )
        print()

    attribution, extras = _pick(report.checks, "pnl attribution")
    _print_split_section("LLM increment vs the deterministic baseline", attribution, extras)

    registry, _ = _pick(report.checks, "model registry")
    if registry is not None:
        print(f"attribution coverage   {registry}")
        print()

    evidence, _ = _pick(report.checks, "pnl evidence")
    if evidence is not None:
        print(f"pnl evidence          {evidence}")
        print()

    verdict, code = _headline(report)
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
