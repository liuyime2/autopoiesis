"""Read-only paper accounting and the unmeasured portfolio profitability target.

Use make benchmark or make benchmark-json. All accounting comes from the retained
real journal and broker evidence, including confirmed older fills and owner-fill
segregation. No credentials, broker requests or orders are needed.

The target is after-cost agent performance against an aligned SPY control over a
rolling 60 trading days, with hard risk limits and a separately measured LLM
increment. Retained journal dates establish coverage, not a rolling equity curve.
Strategy peak-exposure ratios are diagnostics. Their independent maxima cannot
define allocated agent capital, and whole-account equity contains owner capital.

The current record lacks an explicit agent allocation, cash-flow history and
aligned rolling equity/control series. Therefore return, excess and target status
are UNKNOWN, even when raw agent PnL can be measured. Missing valuation is never
zero. The JSON collector reports successful collection separately from target
status. Exit 2 means the target is not measurable; it does not mean PnL is zero.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: The window the target is defined over. Not a knob: the target sentence in this file's
#: docstring is the specification, and a benchmark whose window can be changed by a flag is a
#: benchmark whose verdict can be chosen.
TARGET_TRADING_DAYS = 60

#: Filled by `_run_prose` and read by `main` when it was asked for JSON. A module-level holder
#: rather than a return value because the measurement and the printing are one function and the
#: two formats are two views of it - which is the whole reason they cannot disagree.
_PAYLOAD: dict = {}

EXIT_MET = 0
EXIT_BEHIND = 1
EXIT_NOTHING_TO_MEASURE = 2


def _load_doctor():
    """`doctor` by module path, because `tools/` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "doctor_under_benchmark", ROOT / "src" / "autopoiesis" / "doctor.py"
    )
    if spec is None or spec.loader is None:  # a path that is not a module is a config error, not a None
        raise SystemExit(f"cannot load doctor from {ROOT / 'src' / 'autopoiesis' / 'doctor.py'}")
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
    # The longest leading run of characters all the details agree on, computed here rather than
    # with `os.path.commonprefix`. That function is for paths: this compares doctor's prose, and
    # `os.path.commonpath` would split on separators that are not there. `ruff` 0.17 flags the
    # call as RUF071 and suggests the path version, which would change the meaning - so the
    # intent is stated instead, in one place, and the lint rule is not globally disabled.
    details = [check.detail for check in ranked]
    shared = ""
    if details:
        limit = min(len(d) for d in details)
        index = 0
        while index < limit and len({d[index] for d in details}) == 1:
            index += 1
        shared = details[0][:index]
    shared = shared.strip()
    extras: list[str] = []
    for check in ranked:
        clause = check.detail[len(shared):].lstrip("; ").strip()
        if clause and clause not in extras:
            extras.append(clause)
    return shared, extras


def _headline(report, agent: dict | None) -> tuple[str, int]:
    """Per-strategy ratios and raw PnL cannot prove the portfolio target."""
    detail, _ = _pick(report.checks, "pnl vs holding")
    rows = _parse_comparison(detail) if detail else None
    diagnostic = "no per-strategy comparison available"
    if rows:
        behind = [row for row in rows if row[3].startswith("-")]
        diagnostic = (
            f"{len(behind)} of {len(rows)} strategies are behind buy-and-hold on their own "
            f"peak exposure and window (worst {min(behind, key=lambda r: float(r[3]))[0]} "
            f"by {min(behind, key=lambda r: float(r[3]))[3][1:]} points) - a diagnostic"
            if behind else f"all {len(rows)} strategy ratios are positive diagnostics"
        )
    reason = (agent or {}).get("comparison_reason",
        "agent allocation and aligned rolling equity history are unavailable")
    return f"NOT MEASURABLE: {reason}; {diagnostic}", EXIT_NOTHING_TO_MEASURE


def _agent_level(config=None) -> dict | None:
    """Verified raw agent accounting; no inferred allocation or portfolio return.

    Independent strategy peaks and whole-account equity containing owner capital
    cannot define agent allocation or an aligned rolling benchmark.
    """
    from autopoiesis.broker_evidence import latest_evidence_batch
    from autopoiesis.config import AgentConfig
    from autopoiesis.evaluator import (
        PNL_EVIDENCE_MISSING,
        _pnl_evidence,
        confirmed_fill_activities,
        owner_fill_activities,
    )
    from autopoiesis.journal import JsonlJournal

    config = config or AgentConfig.from_env()
    journal = JsonlJournal(config.journal_path)
    records = journal.read_all()
    evidence = latest_evidence_batch(journal)
    if not records or evidence is None:
        return None
    pnl = _pnl_evidence(records, evidence, confirmed_fill_activities(journal, records),
        assumed_round_trip_cost_pct=config.assumed_round_trip_cost_pct,
        owner_fills=owner_fill_activities(journal))
    if pnl.status == PNL_EVIDENCE_MISSING or not pnl.linked_fill_count:
        return None
    return {
        "performance_contract": "agent-accounting-v1",
        "comparison_status": "UNKNOWN",
        "comparison_reason": "agent allocation, cash flows and aligned rolling equity history are unavailable",
        "pnl": round(pnl.net_after_assumed_cost, 2) if pnl.net_after_assumed_cost is not None else None,
        "realized": round(sum(pnl.strategy_realized_pnl.values()), 2),
        "unrealized": round(pnl.unrealized_pnl, 2) if pnl.unrealized_pnl is not None else None,
        "observed_fees": round(sum(pnl.strategy_fees.values()), 4),
        "capital": None, "return_pct": None, "spy_pct": None, "excess": None,
        "diagnostic_market_return_pct": pnl.market_return_pct,
        "assumed_cost": round(sum(pnl.assumed_cost.values()), 4),
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


def main(argv: list[str] | None = None) -> int:
    """The prose form a reader decides by, and `--json` for anything that has to compare.

    `--json` prints one object and exits with the same code. It exists because the public
    site's figures have to be *collected* rather than retyped, and because running this file
    directly reads the process environment: on a host without the operator's env file that means
    the example config's defaults - so `make benchmark-json` is the entry point that publishes a
    figure, and a bare invocation is the one that shows the default. The make target includes the
    env file, so both entry points differ only in what they were given, never in what they compute.

    The prose is sent to stderr rather than suppressed, so a JSON run is parseable on stdout and
    still says what it measured to whoever is watching the log.
    """
    argv = sys.argv[1:] if argv is None else argv
    if "--json" in argv:
        # The prose form runs into a sink rather than onto the caller's stdout, so one object is
        # all a parser sees. stderr would keep it visible in a log; a sink keeps the collector
        # simple and the figures are dumped in full right after.
        with contextlib.redirect_stdout(io.StringIO()):
            code = _run_prose(argv)
        print(json.dumps(_PAYLOAD, indent=2, sort_keys=True))
        return code
    return _run_prose(argv)


def _run_prose(argv: list[str]) -> int:
    from autopoiesis.config import AgentConfig

    as_json = "--json" in argv
    payload: dict = {"exit_code": None, "strategies": [], "agent": None,
                    "target": {"status": "UNKNOWN", "met": False,
                               "trading_days": TARGET_TRADING_DAYS, "window_covered": False}}
    global _PAYLOAD
    _PAYLOAD = payload

    try:
        config = AgentConfig.from_env()
    except Exception as exc:  # a missing key file must not hide the benchmark
        config = None
        print(f"# config unavailable ({type(exc).__name__}: {exc}); using documented defaults")
    if config is None:
        # `AgentConfig()` has eleven fields with no default, so it raised TypeError: the
        # benchmark crashed on the one path written to survive a configuration it could not
        # read. mypy reported it for five months and nothing caught it at runtime, because this
        # machine has a readable config. The documented defaults are read the same way
        # `from_env` reads them - from an empty mapping, not a second list of literals that
        # could drift from the first and change what the benchmark measures.
        config = AgentConfig.documented_defaults()

    print("=" * 78)
    print("QuantGroup benchmark - does the system beat SPY buy-and-hold?")
    print("=" * 78)

    first, last, days, cycles = _record_window(config)
    if not days:
        print("record            none: no cycle records in the journal, so nothing to measure")
        print(f"                  journal: {config.journal_path}")
        print("                  run 'autopoiesis once' (or let the daemon run) to produce one")
        payload["record"] = {"first": None, "last": None, "trading_days": 0, "cycles": 0,
                             "day_count_basis": "distinct cycle dates; not continuous exchange-calendar coverage"}
        payload["exit_code"] = EXIT_NOTHING_TO_MEASURE
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_NOTHING_TO_MEASURE
    payload["record"] = {"first": first, "last": last, "trading_days": days, "cycles": cycles,
                         "day_count_basis": "distinct cycle dates; not continuous exchange-calendar coverage"}
    print(f"record            {first} .. {last}   {days} trading day(s), {cycles} cycle(s)")
    print(
        f"target window     {TARGET_TRADING_DAYS} trading days - "
        + (
            "RECORD COVERAGE ONLY; rolling comparison unavailable"
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
            payload["strategies"].append(
                {"strategy_id": name, "return_pct": return_pct,
                 "market_pct": market_pct, "excess": excess}
            )
            print(f"  {name:<26} {return_pct:>8} {market_pct:>17} {excess:>8}")
        print()
        print("  diagnostic basis: a strategy peak-exposure ratio and market price change -")
        print("  realized plus unrealized PnL over the strategy's peak exposure, against SPY from")
        print("  its first entry to its last exit (or last valuation while a lot is open). Gross")
        print("  of the assumed round-trip cost; the after-cost figure for agent lots is")
        print("  in the accounting block below. These ratios are not portfolio alpha.")
        print()

    attribution, extras = _pick(report.checks, "pnl attribution")
    _print_split_section("LLM increment vs the deterministic baseline", attribution, extras)

    agent = _agent_level(config)
    if agent is not None:
        print("agent accounting after observed fees and assumed costs")
        print("-" * 78)
        for key in ("realized", "unrealized", "observed_fees", "assumed_cost", "pnl"):
            value = agent[key]
            print(f"  {key:20s} " + (f"{value:+,.4f}" if value is not None else "UNKNOWN"))
        print(f"  portfolio comparison UNKNOWN: {agent['comparison_reason']}")
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
    payload["exit_code"] = code
    payload["agent"] = agent
    payload["risk_budget"] = {
        "max_position_value": config.max_position_value,
        "max_total_exposure": config.max_total_exposure,
        "max_daily_loss": config.max_daily_loss,
        "max_trades_per_day": config.max_trades_per_day,
    }
    payload["cost"] = {"assumed_round_trip_cost_pct": config.assumed_round_trip_cost_pct}
    payload["target"] = {
        "trading_days": TARGET_TRADING_DAYS, "met": False, "status": "UNKNOWN",
        "window_covered": False, "recorded_days_sufficient": days >= TARGET_TRADING_DAYS,
        "covered": days,
    }
    return code


if __name__ == "__main__":
    raise SystemExit(main())
