"""One command that tells you the true state of the system.

This exists because the previous health story was a rubber stamp. `cli --status`
replayed `heartbeat.json` and always returned 0, so a daemon that had been dead
for 97 days passed 112 consecutive automated reviews. `auto_reviewer.py` read
that same file, and the ops shell scripts reported success because `set -e`
without `pipefail` let `| tee` convert any failure into exit 0.

`doctor` judges liveness, reachability, and evidence, and exits non-zero when
anything is wrong. It is the loop to run after every change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from min_agent import (
    attribution,
    calibration,
    coerce,
    counterfactual,
    experiment_registry,
    lineage,
    model_registry,
    regime,
    strategy_engine,
)
from min_agent.atomicio import write_json_atomic
from min_agent.broker_evidence import latest_evidence_batch
from min_agent.config import AgentConfig
from min_agent.evaluator import (
    PNL_EVIDENCE_MISSING,
    DeterministicEvaluator,
    confirmed_fill_activities,
)
from min_agent.fill_reconciler import FILL_EVENT
from min_agent.health import HealthMonitor
from min_agent.journal import JsonlJournal
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import StrategyLifecycle, StrategySpec
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_engine import StrategyLibrary

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""

    @property
    def blocking(self) -> bool:
        return self.status is Status.FAIL


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name, status, detail, hint="") -> None:
        self.checks.append(Check(name, Status(status), detail, hint))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.WARN]

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0

    def to_dict(self) -> dict:
        return {
            "ok": not self.failures,
            "exit_code": self.exit_code,
            "failures": len(self.failures),
            "warnings": len(self.warnings),
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "detail": c.detail,
                    **({"hint": c.hint} if c.hint else {}),
                }
                for c in self.checks
            ],
        }

    def render(self) -> str:
        width = max((len(c.name) for c in self.checks), default=4)
        glyph = {Status.OK: "PASS", Status.WARN: "WARN", Status.FAIL: "FAIL", Status.SKIP: "SKIP"}
        lines = []
        for c in self.checks:
            lines.append(f"[{glyph[c.status]}] {c.name.ljust(width)}  {c.detail}")
            if c.hint and c.status in (Status.FAIL, Status.WARN):
                lines.append(f"       {' ' * width}  -> {c.hint}")
        lines.append("")
        if self.failures:
            lines.append(f"RESULT: FAIL - {len(self.failures)} blocking problem(s): "
                         + ", ".join(c.name for c in self.failures))
        elif self.warnings:
            lines.append(f"RESULT: OK with {len(self.warnings)} warning(s)")
        else:
            lines.append("RESULT: OK - everything checked is healthy")
        return "\n".join(lines)


def record(report: DoctorReport, config: AgentConfig, *, history: bool = True) -> None:
    """Append this run to a bounded history so drift is visible over time.

    A single green reading proves nothing; the 97-day outage survived because
    nothing was watching. The history is what makes "it has been unhealthy since
    Tuesday" answerable.
    """
    if not history:
        return
    path = config.journal_path.parent / "doctor-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "ok": not report.failures,
        "failures": [c.name for c in report.failures],
        "warnings": [c.name for c in report.warnings],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    _trim(path, keep=2000)


def _trim(path: Path, *, keep: int) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= keep:
        return
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines[-keep:]) + "\n")


def run_doctor(config: AgentConfig, *, client=None, skip_broker: bool = False) -> DoctorReport:
    report = DoctorReport()
    _check_environment(report, config)
    _check_config(report, config)
    _check_broker(report, config, client, skip_broker)
    _check_ollama(report, config)
    _check_daemon(report, config)
    _check_journal(report, config)
    _check_strategy_library(report, config)
    _check_knowledge(report, config)
    _check_proof(report, config)
    _check_governance(report, config)
    return report


# --- individual checks -------------------------------------------------------


# The safety envelope the agent cannot cross, checked rather than assumed.
#
# Everything below is enforced today only by there being no code that does the
# thing. A reflection layer is about to be given authority to change the system on
# its own, and an invariant that holds because nobody wrote the violating line is
# not an invariant - it is an absence that one commit can remove. Each check below
# fails loudly if that absence ever ends, and each one reads the journal rather
# than the code, so it catches a hand-edited file or a stray script just as well as
# a new in-tree writer.
RISK_BASELINE_PATH = "risk_baseline.json"


def _check_governance(report: DoctorReport, config: AgentConfig) -> None:
    journal = _journal(config)
    records = _records(journal) if journal is not None else []

    # 1. No order may exist without Guardian approving it. `executor.execute`
    #    refuses to submit an unapproved decision, but nothing ever checked that it
    #    did, and a reflection layer reads these same records.
    bypasses = [
        record
        for record in records
        if record.execution.status == "SUBMITTED" and not record.guardian.approved
    ]
    if bypasses:
        report.add(
            "guardian bypass",
            FAIL,
            f"{len(bypasses)} order(s) submitted without Guardian approval",
            "this must be zero; every SUBMITTED order needs guardian.approved=True",
        )
    elif records:
        approved = sum(
            1
            for record in records
            if record.execution.status == "SUBMITTED" and record.guardian.approved
        )
        report.add("guardian bypass", OK, f"0 bypasses across {approved} submitted order(s)")

    # 2. Risk limits must not have moved upward. `risk limits` above prints the
    #    values and compares them to nothing, so 5000 and 500000 report
    #    identically. The first run records the baseline; later runs flag any
    #    increase, which is the one direction that matters.
    _check_risk_baseline(report, config)

    # 3. Every lifecycle on disk must be traceable to a decision the manager made.
    #    PAUSED and RETIRED are terminal by ordering inside one function, not by any
    #    gate, so a hand-edit or a new writer would revive a retired strategy and
    #    make the selectable count go *up* - which reads as healthier, not worse.
    if journal is not None:
        _check_lifecycle_provenance(report, config, journal)

    # 4. A strategy file that was not written through admission is a strategy the
    #    gates never saw.
    if journal is not None:
        _check_admission_provenance(report, config, journal)

    # 5. The knowledge library must not be a pile of near-identical lines. Nine of
    #    eleven artifacts were variants of one sentence, all reaching every
    #    decision, which is the same zoo pathology the strategy library had.
    _check_knowledge_value(report, config)

    # 6. Decision quality. 864 of 920 cycles are HOLD and this is the only thing
    #    that can say whether they were right.
    _check_decision_quality(report, config, records)

    # 7. The chain the objective names, derived from the journal so there is no
    #    second registry to drift.
    if journal is not None:
        _check_experiment_chain(report, config, journal)

        # 9. Whether the model's stated confidence has predicted anything. Recorded
        #    on every decision, used as a gate, and never once compared against an
        #    outcome.
        _check_model_calibration(report, config, journal, records)

        # 10. Which of signal/model/strategy/allocation/execution/cost/regime
        #     actually produced the PnL. The objective asks this directly and the
        #     answer is not the one the headline number suggests.
        _check_pnl_attribution(report, config, journal, records)

        # 11. Model provenance. Phase 3 asks for a model registry alongside the
        #     strategy one, and until provenance was captured no outcome could ever
        #     be attributed to a model change.
        _check_model_registry(report, config, records)
        _check_llm_fallback(report, config, records)


        # 8. Champion-challenger, search effort, and whether candidates can say
        #    what they are for.
        #
        #    This call site was the first version missing: the check function was
        #    written and never invoked, because the anchor it was inserted against
        #    did not match. That is the "defined but never used" shape the audit
        #    exists to find, created in the same commit that hunts for it - so
        #    every doctor check is asserted to be present in the report.
        _check_champion_and_search(report, config, journal)


def _check_risk_baseline(report: DoctorReport, config: AgentConfig) -> None:
    current = {
        "max_position_value": config.max_position_value,
        "max_daily_loss": config.max_daily_loss,
        "max_total_exposure": config.max_total_exposure,
        "max_account_value": config.max_account_value,
        "max_trades_per_day": config.max_trades_per_day,
        "min_confidence": config.min_confidence,
        "allowlist": sorted(config.allowlist),
    }
    path = Path(config.journal_path).parent / RISK_BASELINE_PATH
    if not path.exists():
        try:
            write_json_atomic(path, {"recorded_at": _now_iso(), "limits": current})
            report.add(
                "risk limits vs baseline", WARN,
                "no baseline on disk; recorded the current limits as the baseline",
                "re-run doctor to compare future changes against it",
            )
        except Exception as exc:
            report.add("risk limits vs baseline", WARN,
                       f"could not record a baseline: {type(exc).__name__}")
        return

    try:
        recorded = json.loads(path.read_text()).get("limits", {})
    except Exception as exc:
        report.add("risk limits vs baseline", FAIL,
                   f"baseline unreadable: {type(exc).__name__}",
                   f"delete {path} to re-record it")
        return

    loosened = []
    for key, value in current.items():
        before = recorded.get(key)
        if isinstance(value, (int, float)) and isinstance(before, (int, float)):
            if value > before:
                loosened.append(f"{key} {before} -> {value}")
        elif value != before:
            loosened.append(f"{key} {before} -> {value}")
    if loosened:
        report.add(
            "risk limits vs baseline", FAIL,
            "; ".join(loosened),
            "a risk limit was raised or the allowlist widened; that is a human decision",
        )
    else:
        report.add("risk limits vs baseline", OK, "unchanged since the recorded baseline")


def _check_lifecycle_provenance(
    report: DoctorReport, config: AgentConfig, journal: JsonlJournal
) -> None:
    decided: dict[str, str] = {}
    for event in journal.read_events("STRATEGY_LIFECYCLE_UPDATED"):
        strategy_id = event.strategy_id or event.payload.get("strategy_id")
        new_lifecycle = event.payload.get("new_lifecycle")
        if isinstance(strategy_id, str) and isinstance(new_lifecycle, str):
            decided[strategy_id] = new_lifecycle

    library = StrategyLibrary(config.strategy_dir)
    unaccounted, undecided = [], []
    for strategy in library.list():
        # Per strategy, not per value. The first cut asked whether *any* decision
        # had produced this lifecycle, so one journalled retirement laundered every
        # other unjournalled one - the check passed against eight strategies that
        # had been retired with no recorded decision at all, which is precisely the
        # bypass it exists to catch.
        last = decided.get(strategy.strategy_id)
        if last is None:
            # No transition has ever been journalled for this strategy. That is
            # legitimate for a strategy still in the state it was admitted into -
            # the HOLD_BASELINE is BASELINE from the moment it is created, and
            # demanding a decision for it would demand a transition that never
            # happened. It is NOT legitimate for a gated state, where reaching the
            # state is itself the event.
            if strategy.lifecycle in {"PAUSED", "RETIRED"}:
                undecided.append(f"{strategy.strategy_id}={strategy.lifecycle}")
            continue
        # Any disagreement, in either direction, is unaccounted for. This check used
        # to skip the default states outright, which left an asymmetry: a strategy
        # moved *into* PAUSED/RETIRED with no decision was caught, but one moved
        # *out* of them into PROBATION/ACTIVE with no decision was invisible. That
        # is not a hypothetical - re-adjudicating the five unapproved strategies
        # moved three of them to PROBATION and the audit stayed silent, because
        # nothing here compared a default state against its last recorded decision.
        if last != strategy.lifecycle:
            unaccounted.append(
                f"{strategy.strategy_id}={strategy.lifecycle} (last decision: {last})"
            )
    if unaccounted or undecided:
        findings = unaccounted + undecided
        report.add(
            "lifecycle provenance", FAIL,
            f"{len(findings)} strategy/ies reached a state with no matching recorded "
            f"decision: {', '.join(findings[:6])}",
            "PAUSED/RETIRED are terminal; only the lifecycle manager may set them, "
            "and no state may differ from its last journalled decision",
        )
    else:
        report.add(
            "lifecycle provenance", OK,
            f"{len(decided)} lifecycle decision(s) all traceable to the journal",
        )


def _check_admission_provenance(
    report: DoctorReport, config: AgentConfig, journal: JsonlJournal
) -> None:
    """Every strategy in the library must trace to an accepted admission.

    The library is a directory and the engine selects straight from it, so a file
    is indistinguishable from an approved strategy once it lands there. This check
    existed and was wired, but it reported its findings as a WARN hedged with
    "legacy from before admission was journalled, or written outside the gate" -
    a hedge the journal can resolve on its own, by comparing each file against the
    first admission event that was ever recorded. Resolving it matters: all five
    files it names were written *after* the gate was journalled, so none of them is
    legacy, and three of them placed 33 broker-confirmed paper fills. A warning
    that cannot fail is not a control, and one that explains away its own finding
    teaches the reader to ignore it.
    """
    reviews = journal.read_events("STRATEGY_ADMISSION_REVIEWED")
    accepted = {e.strategy_id for e in reviews if e.status == "ACCEPTED" and e.strategy_id}
    refused = {e.strategy_id for e in reviews if e.status == "REJECTED" and e.strategy_id}
    library = StrategyLibrary(config.strategy_dir)
    strategies = library.list()
    if not reviews:
        # No admission has ever been journalled, so there is no gate to compare
        # against. That is genuinely unknown, not a pass.
        report.add("admission provenance", WARN,
                   "no admission reviews in the journal to compare the library against")
        return
    # No separate branch for "reviews exist but none was accepted": that is the
    # same finding as any other unadjudicated file, and an early return here
    # reported the condition without naming the file, which is the one thing an
    # operator needs. Every file simply falls through to the loop below.

    # Not selectable is the gate's own way of saying no. A spec the gate refused is
    # properly accounted for once it is RETIRED or PAUSED, and demanding an ACCEPTED
    # event for it instead reported the correct outcome as a bypass - which is how a
    # re-adjudication of the five unaccounted files could be applied and leave the
    # gate red for having done the right thing. The failure being guarded against is
    # a file that is *in the library and tradeable* with no verdict behind it.
    NOT_SELECTABLE = {"RETIRED", "PAUSED"}

    def accounted(strategy: StrategySpec) -> bool:
        if strategy.strategy_id in accepted:
            return True
        return (strategy.strategy_id in refused
                and strategy.lifecycle in NOT_SELECTABLE)

    gate_since = min(e.timestamp for e in reviews)
    # The two appends below fill positions 3 and 4 in opposite orders: an
    # unadjudicated file has a note and no write time, a post-gate file has a
    # write time and no note. Annotated, so neither branch silently fixes the
    # tuple shape for the other.
    bypasses: list[tuple[str, StrategyLifecycle, datetime | None, str | None]] = []
    legacy: list[str] = []
    for strategy in strategies:
        if accounted(strategy):
            continue
        # A refused spec that is still selectable is a different failure from an
        # unadjudicated one, and it is the more dangerous of the two.
        if strategy.strategy_id in refused:
            bypasses.append((strategy.strategy_id, strategy.lifecycle, None,
                             "refused by admission but still selectable"))
            continue
        # created_at is model-supplied and demonstrably unreliable (three files
        # share one timestamp), so the on-disk mtime is the honest witness.
        path = Path(config.strategy_dir) / f"{strategy.strategy_id}.json"
        try:
            written = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            written = None
        if written is not None and written > gate_since:
            bypasses.append((strategy.strategy_id, strategy.lifecycle, written, None))
        else:
            legacy.append(strategy.strategy_id)

    if bypasses:
        fills = _fills_by_strategy(journal)
        traded = [
            f"{sid}={fills.get(sid, 0)}fill(s)" for sid, _lc, _w, _n in bypasses
            if fills.get(sid, 0)
        ]
        detail = ", ".join(
            f"{sid}({lc}{', ' + note if note else ''})"
            for sid, lc, _w, note in bypasses[:6]
        )
        report.add(
            "admission provenance", FAIL,
            f"{len(bypasses)} strategy file(s) are in the library without a "
            f"disposition the gate can stand behind: {detail}."
            + (f" Broker-confirmed fills attributed to them: {', '.join(traded)}."
               if traded else " No broker-confirmed fills attributed to them yet."),
            "each needs a recorded admission verdict, and a refused one must be "
            "retired or paused",
        )
    elif legacy:
        report.add(
            "admission provenance", WARN,
            f"{len(legacy)} strategy file(s) predate the admission gate and have no "
            f"accepted admission: {', '.join(legacy[:6])}",
            "genuinely legacy; no post-gate entry was found",
        )
    else:
        report.add("admission provenance", OK,
                   f"all {len(strategies)} file(s) carry a disposition the gate can "
                   f"stand behind ({len(accepted)} accepted, {len(refused)} refused "
                   f"and not selectable)")


def _fills_by_strategy(journal: JsonlJournal) -> dict[str, int]:
    return _fills_by_strategy_cached.get(journal.path, _scan_fills(journal))


#: One scan per journal path per process. `_fills_by_strategy` is called from a
#: check that already walks the whole journal, and the doctor runs on a schedule.
_fills_by_strategy_cached: dict = {}


def _scan_fills(journal: JsonlJournal) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in journal.read_events("ORDER_FILL_CONFIRMED"):
        if event.strategy_id:
            counts[event.strategy_id] = counts.get(event.strategy_id, 0) + 1
    _fills_by_strategy_cached[journal.path] = counts
    return counts


def _check_decision_quality(
    report: DoctorReport, config: AgentConfig, records: list
) -> None:
    """Report how the HOLDs actually turned out, after cost.

    Read from the journalled counterfactual rows rather than recomputed, so this is
    the same number the loop recorded. A missing or unscored result is reported as
    such: "we cannot tell yet" must not read as "we are doing fine".
    """
    symbol = config.symbols[0] if config.symbols else "SPY"
    try:
        result = counterfactual.evaluate(
            records,
            symbol=symbol,
            horizon_hours=config.counterfactual_horizon_hours,
            assumed_cost_pct=config.assumed_round_trip_cost_pct,
        )
    except Exception as exc:
        report.add("decision quality", WARN, f"evaluation failed: {type(exc).__name__}")
        return
    if not result.rows:
        report.add("decision quality", WARN, "no decision could be scored yet")
        return
    quality = result.hold_quality()
    counts = result.counts()
    detail = (
        f"hold_quality={quality} over {len(result.scored)} scored "
        f"({result.pending_count} pending, {result.gap_count} refused as gaps); "
        f"holds: {counts.get('GOOD_HOLD', 0)} good, "
        f"{counts.get('MISSED_ALPHA', 0)} missed alpha, "
        f"{counts.get('FALSE_TRADE', 0)} false trade"
    )
    if quality is None:
        report.add(
            "decision quality", WARN, detail,
            "no hold has a later quote yet; the journal needs more market hours",
        )
    else:
        report.add("decision quality", OK, detail)


def _check_experiment_chain(
    report: DoctorReport, config: AgentConfig, journal: JsonlJournal
) -> None:
    """Report whether each strategy's journey is fully accounted for.

    A strategy is only understandable if proposal, admission, offline screen and
    evaluation evidence all exist for it. The bulk of the incomplete chains are
    historical - strategies that predate the offline screen - so the count is
    reported rather than failed, but a *tradable* strategy with no evaluation
    evidence at all is a genuine hole and is called out separately.
    """
    try:
        strategies = strategy_engine.StrategyLibrary(
            config.strategy_dir
        ).list()
    except Exception as exc:
        report.add(
            "experiment chain", WARN, f"strategy library unreadable: {type(exc).__name__}"
        )
        return

    # The chain lives entirely in *events*. `read_all()` returns cycle records
    # only, so passing those in reported "0 admitted, 27 unscreened" for a library
    # where 23 strategies were in fact admitted - a check that was structurally
    # incapable of seeing the thing it checked.
    try:
        events = journal.read_events()
    except Exception as exc:
        report.add(
            "experiment chain", WARN, f"journal events unreadable: {type(exc).__name__}"
        )
        return
    try:
        experiments = experiment_registry.build(events, strategies)
    except Exception as exc:
        report.add(
            "experiment chain", WARN, f"could not derive the chain: {type(exc).__name__}"
        )
        return
    if not experiments:
        report.add("experiment chain", WARN, "no strategies to account for")
        return

    summary = experiment_registry.summarize(experiments)
    tradable_without_evidence = [
        e.strategy_id for e in experiments
        if e.lifecycle in {"ACTIVE", "PROBATION"}
        and not e.evaluations
    ]
    detail = (
        f"{summary['strategies']} strategies, {summary['admitted']} admitted, "
        f"{summary['incomplete_chains']} with an incomplete chain; "
        f"screens: {summary['by_offline_verdict']}"
    )
    if tradable_without_evidence:
        report.add(
            "experiment chain", WARN,
            f"{detail}; tradable with no evaluation evidence: "
            f"{tradable_without_evidence}",
        )
    else:
        report.add("experiment chain", OK, detail)


def _check_champion_and_search(
    report: DoctorReport, config: AgentConfig, journal: JsonlJournal
) -> None:
    """Name the benchmark, and report how hard the system searched for each candidate.

    A champion is only named from broker-verified strategy-level PnL. With nothing
    verified positive, no champion is named and the report says so, because naming
    one from cycles or an in-sample backtest would be choosing a benchmark on
    evidence the system already distrusts.

    Candidates that cannot state the observation that motivated them are counted and
    reported rather than defaulted to fine. The live journal shows 33 of 33 in that
    state, which is the finding, not a defect of the report.
    """
    try:
        strategies = strategy_engine.StrategyLibrary(config.strategy_dir).list()
        events = journal.read_events()
    except Exception as exc:
        report.add(
            "champion / search", WARN, f"could not read state: {type(exc).__name__}"
        )
        return

    # The champion has to be designated from real PnL, so the reflection record has
    # to be read. Passing None here named no champion even though one strategy has
    # broker-verified positive PnL on record - a check that reports "NONE" when the
    # answer is on file is worse than no check.
    results_by_id: dict[str, object] = {}
    try:
        memory = ReflectionMemory(config.journal_path.parent / "reflection.json")
        record = memory.load()
        if record is not None:
            results_by_id = {
                r.strategy_id: r for r in memory.strategy_results(record)
            }
    except Exception:
        results_by_id = {}

    try:
        origins = lineage.build_origins(events)
        effort = lineage.search_effort(origins.values())
        outcome = lineage.designate_champion(strategies, results_by_id, origins)
    except Exception as exc:
        report.add(
            "champion / search", WARN, f"could not derive: {type(exc).__name__}"
        )
        return

    unjustified = [o.strategy_id for o in origins.values() if o.missing_justification()]
    detail = (
        f"champion={outcome.champion or 'NONE'} "
        f"({outcome.champion_reason[:70]}); "
        f"{effort['candidates']} candidates from {effort['distinct_tasks']} tasks, "
        f"{effort['tasks_that_searched']} task(s) searched, max "
        f"{effort['max_variants_from_one_task']} variants; "
        f"{len(unjustified)} cannot state what observation motivated them"
    )
    if unjustified:
        report.add("champion / search", WARN, detail)
    else:
        report.add("champion / search", OK, detail)


def _check_model_calibration(
    report: DoctorReport,
    config: AgentConfig,
    journal: JsonlJournal,
    records: list,
) -> None:
    """Score the model's confidence against what actually happened.

    A warning is the right level for "we cannot tell yet", because it is the truthful
    state: every LLM decision so far falls inside a single closed session, so the
    counterfactual ledger has no outcome to score any of them against. Saying
    "unmeasured" is the whole point. A pass here would mean the model had been shown
    to earn its threshold, which is not a claim the evidence supports.
    """
    try:
        verdicts = {
            row.cycle_id: row.verdict
            for row in counterfactual.evaluate(
                records,
                symbol=config.symbols[0] if config.symbols else "SPY",
                horizon_hours=config.counterfactual_horizon_hours,
                assumed_cost_pct=config.assumed_round_trip_cost_pct,
            ).rows
        }
        rows = calibration.build_rows(records, verdicts, source="llm")
        result = calibration.calibrate(rows, min_confidence=config.min_confidence)
    except Exception as exc:
        report.add(
            "model calibration", WARN, f"evaluation failed: {type(exc).__name__}"
        )
        return

    # The verdict is reported whole, not truncated to its label.
    #
    # `verdict.split(":")[0]` printed "MIS-CALIBRATED" and discarded the rest, which is
    # where the actionable half lives: `calibration.calibrate` had already computed the
    # bucket accuracies and their margins and stated them in the sentence that was thrown
    # away.
    #
    # That is the same defect this file already records twice, in a new place. A label
    # saying "miscalibrated" invites the reading "the model is overconfident, raise
    # `min_confidence`" - and the earlier version of this comment argued the opposite
    # from the raw figures, that the confident decisions were the worst ones.
    #
    # **That argument was itself the bug, and it has been removed rather than reversed.**
    # Correctness is mostly the trading day's: on this account the per-day base rate runs
    # 96.9% / 26.6% / 85.1% / 12.3%, so the bucket that looked 90.6% accurate was one
    # day's rate and the confident bucket looked terrible because it landed on the other
    # days. `calibrate` now reads every bucket against its own days and the verdict
    # carries that margin, so neither reading has to be inferred here.
    detail = (
        f"{result.total_decisions} llm decision(s), {result.scored} scored, "
        f"{result.pending} still awaiting an outcome; {result.verdict}"
    )
    # No separate `, Brier ..., base rate ...` append: every non-INSUFFICIENT branch of
    # `calibrate` already states both inside the verdict, so appending them again would
    # print each figure twice. An INSUFFICIENT verdict names neither, and that absence is
    # the information - there is nothing yet to be miscalibrated against.
    level = OK if result.verdict.startswith("SIGNAL") else WARN
    report.add("model calibration", level, detail)


def _check_pnl_attribution(
    report: DoctorReport,
    config: AgentConfig,
    journal: JsonlJournal,
    records: list,
) -> None:
    """State which cause produced the PnL, and refuse to guess where the data cannot.

    This exists because a number in a payload was being read as evidence about the
    model. The lots record which order opened them and the cycles record which
    decision source issued that order; nothing joined the two, so
    `strategy_realized_pnl` looked like a result for the decision engine when it is a
    result for the baseline rule.
    """
    try:
        events = [
            e for e in journal.read_events("PNL_EVIDENCE_RECORDED")
            if coerce.field_dict(e.payload.get("pnl"), "pnl").get("closed_lots")
        ]
    except Exception as exc:
        report.add("pnl attribution", WARN, f"journal unreadable: {type(exc).__name__}")
        return
    if not events:
        report.add(
            "pnl attribution", WARN,
            "no closed lots on record, so no PnL can be attributed to any cause",
        )
        return

    latest = events[-1]
    # Point-in-time regime labels, so a lot is attributed to the market it was opened
    # in rather than to a regime computed over the whole history.
    labels = dict(regime.regime_timeline(regime.bars_from_records(records)))
    result = attribution.attribute(
        records, coerce.field_dict(latest.payload.get("pnl"), "pnl"), latest.payload, regime_labels=labels
    )
    causes = "; ".join(f"{c.cause}={c.verdict}" for c in result.causes)
    pnl = coerce.field_dict(latest.payload.get("pnl"), "pnl")
    net = coerce.field_float(pnl.get("net_pnl"), "pnl.net_pnl")
    unrealized = coerce.field_float(pnl.get("unrealized_pnl"), "pnl.unrealized_pnl")
    # net = realized + unrealized, stated so the two cannot be confused for the
    # account figure, which covers the whole account over all time.
    # Tolerates a payload that predates the unrealized fields. Every stored
    # PnL event before this change lacks them, and a report that crashes on its own
    # history is a report nobody can read.
    total = ""
    if net is not None and unrealized is not None:
        total = (
            f" (net {net:+.2f} = realized {result.total_realized_pnl:+.2f}"
            f" + unrealized {unrealized:+.2f})"
        )
    elif net is not None:
        total = f" (net {net:+.2f})"
    detail = (
        f"{result.headline()}{total} | AFTER COST: {result.cost_headline()} "
        f"| {causes} | by regime: "
        f"{ {k: round(v, 2) for k, v in result.by_regime.items()} }"
    )
    # `unmatched_sell_quantity` is keyed by strategy, e.g.
    # {"trend-follow-sell-002": 29.0} - not a scalar. The message used to interpolate
    # the raw dict, so it read "UNMATCHED SELLS {'trend-follow-sell-002': 29.0}"
    # while claiming to name a number of shares. Total the shares, and keep the
    # breakdown, because which strategy produced them is the actionable part.
    unmatched_by_strategy = coerce.field_dict(
        pnl.get("unmatched_sell_quantity"), "pnl.unmatched_sell_quantity"
    )
    unmatched_shares = sum(
        coerce.field_float_or(qty, f"pnl.unmatched_sell_quantity[{sid}]", 0.0)
        for sid, qty in unmatched_by_strategy.items()
    )
    if unmatched_shares:
        detail += (
            f" | UNMATCHED SELLS {unmatched_shares:g}: shares were sold "
            f"that no linked BUY could account for ({unmatched_by_strategy}), "
            "and their PnL is absent from the total above rather than merely "
            "unproven"
        )

    # Severity follows a distinction that has to be stated, because it decides
    # whether this is a code fault or a fact about the data:
    #
    # * model_pnl == 0 because the model has opened no lots at all - that is
    #   "not traded yet", which is a fact waiting on market hours, and it is
    #   reported as a warning that cannot be missed;
    # * model_pnl == 0 while the model HAS opened lots and the total is positive -
    #   that is the model trading and failing to beat the baseline, which is a real
    #   problem and fails the gate.
    #
    # Downgrading the first case to a warning to turn the gate green would be
    # masking, so the distinction is drawn on evidence rather than on convenience.
    # Closed lots AND open lots. Counting only closed lots reported "the model has
    # opened no lots" on the day the model opened ten of them and closed none,
    # because realized PnL is 0.00 for a position that has not been sold.
    model_lots = sum(
        n for source, n in result.lots_by_source.items()
        if source in {"llm", "fallback_policy_engine"}
    )
    model_open = result.model_open_quantity
    # Two different findings, and only one of them decides severity.
    #
    # * Shares sold that no BUY accounts for are a limit on what the *record* can support.
    #   `_apply_activity` books no PnL for them - only the quantity and the fee - so the
    #   headline total is *incomplete*, not an upper bound: the omitted term is those shares'
    #   gain or loss, and the record does not say which. An earlier version of this message
    #   called the figure "an upper bound", which is wrong in the direction that flatters the
    #   run and is the reason the sentence now states the mechanism instead. That is a
    #   property of the data, so it is reported prominently and recorded in STATUS.md, but it
    #   does not make the health report FAIL - otherwise a four-month-old accounting gap
    #   keeps `make verify` red permanently and teaches everyone to ignore it.
    #
    # * A model that opened lots and subtracted from a positive total IS a FAIL, and stays
    #   one. `tests/min_agent/test_attribution.py` guards that with a deliberate message: the
    #   original bug was a `== 0.0` test that reported OK when the model merely subtracted,
    #   and downgrading it again to keep a gate green is the same defect a second time. I
    #   briefly downgraded it and the test caught it, correctly.
    # Evaluated independently, not as an elif chain. An earlier version made unmatched sells
    # the `if` arm and left the model-subtraction FAIL in the `elif`, so on this host - where
    # 29 shares are unmatched - the FAIL could never fire and the severity test in
    # test_attribution.py only passed because its fixture has no unmatched sells. Two
    # conditions that are each independently a failure have to be reported as such.
    if unmatched_shares:
        report.add(
            "pnl attribution", WARN,
            detail + f"; {unmatched_shares:g} share(s) were sold that no BUY accounts "
            "for, so the PnL of those exits is not in the total above at all - their "
            "proceeds are real and their cost basis is not on the record, so the omitted "
            "term can be positive or negative and the figure is incomplete rather than "
            "exact. This is a property of the account, not a fault in the trading path - "
            "see STATUS.md",
        )
    if result.closed_lots and model_lots > 0 and result.model_pnl <= 0.0:
        report.add(
            "pnl attribution", FAIL,
            detail + f"; the model opened {model_lots} lot(s) and contributed "
            f"{result.model_pnl:+.2f} to a total of {result.total_realized_pnl:+.2f}",
        )
    elif result.closed_lots and result.model_pnl == 0.0 and model_open > 0:
        report.add(
            "pnl attribution", WARN,
            detail + f"; the model has {model_open:g} share(s) OPEN and none closed, "
            "so its realized contribution is 0.00 by construction rather than "
            "because it did nothing - the closed-lot figure above belongs entirely "
            "to the baseline rule",
        )
    elif result.closed_lots and result.model_pnl == 0.0:
        report.add(
            "pnl attribution", WARN,
            detail + "; the model has opened no lots, so it has neither helped nor "
            "hurt - the figure above belongs entirely to the baseline rule",
        )
    else:
        report.add("pnl attribution", OK, detail)


def _check_llm_fallback(
    report: DoctorReport, config: AgentConfig, records: list
) -> None:
    """Surface cycles where the model was bypassed.

    The objective is explicit that no failure may be masked by a fallback. When the
    model call exceeds the engine timeout, `HybridDecisionEngine` substitutes the
    deterministic policy engine and returns a perfectly ordinary-looking decision.
    That is the safe direction - the substitution is still Guardian-gated, and a
    deterministic rule is not a hallucinating one - but it means the LLM silently
    left the decision loop and the cycle's record looked like any other.

    Nothing reported it. `model_registry` counts the source, so a fallback is not
    `UNATTRIBUTED` and looks accounted for; `decision quality` scores whatever
    decision arrived. The two 2026-09-28 fallbacks are visible in the journal and
    in neither warning. A masked failure is what this line exists to prevent, so
    any occurrence is worth saying out loud, and a sustained rate is worth saying
    louder.
    """
    sources: dict[str, int] = {}
    for record in records:
        source = getattr(record.decision, "decision_source", None)
        if source:
            sources[source] = sources.get(source, 0) + 1
    fallbacks = sources.get("fallback_policy_engine", 0)
    total = sum(sources.values())
    if not fallbacks:
        report.add(
            "llm fallback", OK,
            f"0 of {total} decision(s) bypassed the model "
            f"({sources.get('llm', 0)} came from the model)",
        )
        return
    share = fallbacks / total if total else 0.0
    report.add(
        "llm fallback", WARN if share < 0.1 else FAIL,
        f"{fallbacks} of {total} decision(s) ({share:.0%}) came from the "
        f"deterministic policy engine instead of the model",
        "the model call exceeded its timeout and was substituted; the cycle is "
        "still Guardian-gated, but the LLM was not the author of that decision",
    )


def _check_model_registry(
    report: DoctorReport, config: AgentConfig, records: list
) -> None:
    """Report which models produced decisions, and flag total absence of provenance.

    A warning while every decision is unattributed is correct rather than noisy: it
    is the truth about the 920 cycles already on the journal, and it will clear
    itself as soon as the market is open and new decisions are stamped. Back-filling
    them would make the gap invisible and every future comparison would then look
    like evidence for a model that never ran.
    """
    try:
        registry = model_registry.build(records)
    except Exception as exc:
        report.add(
            "model registry", WARN, f"could not derive: {type(exc).__name__}"
        )
        return
    if registry.total_decisions == 0:
        report.add("model registry", WARN, "no decisions on record")
        return
    unattributed = registry.models[model_registry.UNATTRIBUTED].decisions \
        if model_registry.UNATTRIBUTED in registry.models else 0
    # Severity has to be proportional. This check warned only when *every* decision
    # was unattributed, so a record that is 93% unattributed - 920 of 987, the
    # state it is actually in - reported OK on the strength of the 67 that do
    # carry a name. The docstring is right that back-filling would be worse, and
    # right that the total-absence case is worth a warning; it drew the line at the
    # only value that cannot be a majority. The question this check exists to answer
    # is how much of the decision record can be attributed at all, and "almost none
    # of it" is not an OK. The boundary is inclusive: exactly half unattributed is a
    # coin flip on provenance, which is not a state to report as healthy. The first
    # version used a strict `>` and a test caught 50/50 reporting OK.
    if unattributed and unattributed >= registry.total_decisions / 2:
        report.add(
            "model registry", WARN,
            f"{unattributed} of {registry.total_decisions} decision(s) "
            f"({unattributed / registry.total_decisions:.0%}) cannot be attributed "
            f"to a model; {registry.summary()}",
            "provenance was not captured for these; they are left unattributed "
            "deliberately rather than back-filled",
        )
        return
    report.add("model registry", OK, registry.summary())


def _check_knowledge_value(report: DoctorReport, config: AgentConfig) -> None:
    """Flag a knowledge library that is growing without carrying information."""
    try:
        artifacts = KnowledgeLibrary(config.knowledge_dir).list(status="ACCEPTED")
    except Exception as exc:
        report.add("knowledge value", WARN, f"library unreadable: {type(exc).__name__}")
        return
    if not artifacts:
        report.add("knowledge value", OK, "no accepted artifacts")
        return
    # Count distinct by *answer*, not by summary: two artifacts can share a summary
    # and still say different things, and 10 of the 11 here differ nowhere at all.
    # The decision prompt receives the answer, so the answer is what has to be
    # counted.
    seen: dict[str, int] = {}
    for artifact in artifacts:
        key = " ".join((artifact.answer or artifact.summary).lower().split())
        seen[key] = seen.get(key, 0) + 1
    distinct = len(seen)
    if len(artifacts) > 1 and distinct < len(artifacts) / 2:
        report.add(
            "knowledge value", WARN,
            f"{len(artifacts)} accepted artifact(s) carrying only {distinct} "
            f"distinct statement(s): the library is not adding information",
            "needs a real derivation, and superseded artifacts need pruning",
        )
    else:
        report.add(
            "knowledge value", OK,
            f"{len(artifacts)} accepted artifact(s), {distinct} distinct statement(s)",
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _journal(config: AgentConfig) -> JsonlJournal | None:
    try:
        return JsonlJournal(config.journal_path)
    except Exception:
        return None


def _records(journal: JsonlJournal):
    try:
        return journal.read_all()
    except Exception:
        return []


def _check_environment(report: DoctorReport, config: AgentConfig) -> None:
    import sys

    # Kept even though pyproject declares requires-python >= 3.10. `pip` refusing an
    # incompatible install is not a substitute for the system reporting one: the env can
    # be activated directly, or PYTHONPATH set, without any install step happening at
    # all - which is how this project is run. A 3.9 interpreter under this source would
    # break every dependency check below it, so it is worth naming here rather than
    # discovering later as an unrelated ImportError.
    version = ".".join(str(p) for p in sys.version_info[:3])
    if sys.version_info < (3, 10):
        report.add("python", FAIL, f"{version} is too old (need >= 3.10)", "use the llm conda env")
    else:
        report.add("python", OK, f"{version} at {sys.executable}")

    for module in ("alpaca_trade_api", "pydantic", "requests"):
        try:
            __import__(module)
        except ImportError as exc:
            report.add(f"dep:{module}", FAIL, str(exc), "conda run -n llm ...")
        else:
            report.add(f"dep:{module}", OK, "importable")


def _check_config(report: DoctorReport, config: AgentConfig) -> None:
    if config.mode == "paper":
        report.add("mode", OK, "paper")
    else:
        report.add("mode", FAIL, f"mode={config.mode!r}; only paper is permitted", "unset MIN_AGENT_MODE")
    if config.is_paper_endpoint():
        report.add("paper endpoint", OK, config.alpaca_base_url)
    else:
        report.add("paper endpoint", FAIL, f"{config.alpaca_base_url} is not a paper URL")
    report.add(
        "risk limits",
        OK,
        f"position<={config.max_position_value} exposure<={config.max_total_exposure} "
        f"daily_loss<={config.max_daily_loss} trades/day<={config.max_trades_per_day}",
    )
    missing = config.missing_alpaca_credentials()
    if missing:
        report.add(
            "alpaca credentials", FAIL, f"missing {missing}",
            "export ALPACA_API_KEY and ALPACA_SECRET_KEY (paper keys)",
        )
    else:
        report.add("alpaca credentials", OK, "present")


def _check_broker(report: DoctorReport, config: AgentConfig, client, skip: bool) -> None:
    if skip:
        report.add("broker clock", SKIP, "skipped (--skip-broker)")
        return
    if config.missing_alpaca_credentials():
        report.add("broker clock", SKIP, "no credentials, cannot reach the broker")
        return
    if client is None:
        report.add("broker clock", SKIP, "no client supplied")
        return
    try:
        clock = client.get_clock()
    except Exception as exc:
        report.add("broker clock", FAIL, f"{type(exc).__name__}: {exc}", "check network and paper API")
        return
    is_open = bool(getattr(clock, "is_open", False))
    report.add(
        "broker clock", OK,
        f"market {'OPEN' if is_open else 'CLOSED'}; next_open={getattr(clock, 'next_open', '?')}",
    )
    try:
        positions = client.list_positions()
        orders = client.list_orders(status="open")
    except Exception as exc:
        report.add("broker positions", FAIL, f"{type(exc).__name__}: {exc}")
        return
    if not positions:
        report.add(
            "broker positions", OK, f"0 position(s), {len(orders)} open order(s)",
            hint="an empty book is correct for a fresh paper account",
        )
        return
    report.add(
        "broker positions", OK,
        f"{len(positions)} position(s), {len(orders)} open order(s): "
        + ", ".join(f"{p.symbol}={_position_quantity(p):g}" for p in positions),
        hint="" if orders else "no open orders",
    )
    _check_unmanaged_exposure(report, config, client, positions)


def _position_quantity(position) -> float:
    """alpaca-trade-api returns every numeric field as a string.

    Checking for int/float here and falling back to 0 produced a check that
    reported "5 positions outside the allowlist: BIL=0, TLT=0, ... $37,074" -
    zero shares alongside tens of thousands of dollars, which is worse than no
    output at all.
    """
    for attribute in ("qty", "quantity"):
        value = getattr(position, attribute, None)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _check_unmanaged_exposure(report: DoctorReport, config: AgentConfig, client, positions) -> None:
    """Positions the agent does not manage still move the equity it is judged on.

    This account holds BIL, TLT, XLB, XLE and XLF alongside the allowlist book -
    about $37k of long market value the agent neither chose nor can attribute. The
    agent's *exposure* limits are allowlist-scoped on purpose, because it cannot
    increase exposure outside the allowlist. But `daily_loss` is not exposure, it
    is `day_start_equity - equity` on the whole account, so a drop in BIL or TLT
    consumes the agent's $500 daily-loss budget and trips a risk limit for a reason
    that appears in no decision, no PnL record, and no attribution. Being
    conservative is the right failure direction; being unable to explain why is not,
    and the previous version of this check reported only a count, so the composition
    was invisible.
    """
    allowlist = {s.upper() for s in config.allowlist}
    unmanaged = [p for p in positions if str(p.symbol).upper() not in allowlist]
    if not unmanaged:
        return
    try:
        account = client.get_account()
        equity = float(getattr(account, "equity", 0.0) or 0.0)
    except Exception:
        equity = 0.0
    value = sum(
        float(getattr(p, "market_value", 0.0) or 0.0) for p in unmanaged
    )
    share = f" ({value / equity:.0%} of equity)" if equity else ""
    report.add(
        "unmanaged exposure", WARN,
        f"{len(unmanaged)} position(s) outside the allowlist the agent cannot "
        f"manage or attribute: "
        + ", ".join(f"{p.symbol}={_position_quantity(p):g}" for p in unmanaged)
        + f", ${value:,.2f}{share}",
        "daily_loss is measured on whole-account equity, so these positions can "
        "consume the agent's daily-loss budget without appearing in any PnL record",
    )


def _check_ollama(report: DoctorReport, config: AgentConfig) -> None:
    import requests

    try:
        response = requests.get(f"{config.ollama_base_url}/api/tags", timeout=5)
        response.raise_for_status()
        names = {m.get("name") for m in response.json().get("models", [])}
    except Exception as exc:
        report.add(
            "ollama", FAIL, f"{config.ollama_base_url} unreachable: {type(exc).__name__}",
            "ollama serve (check OLLAMA_HOST/port matches OLLAMA_BASE_URL)",
        )
        return
    if config.model in names:
        report.add("ollama", OK, f"{config.ollama_base_url} serving {config.model}")
    else:
        report.add(
            "ollama", FAIL, f"model {config.model!r} not present (have: {sorted(n for n in names if n)[:6]})",
            f"ollama pull {config.model}",
        )


def _check_daemon(report: DoctorReport, config: AgentConfig) -> None:
    monitor = HealthMonitor(config.heartbeat_path, stale_after_seconds=config.stale_after_seconds)
    live = monitor.liveness()
    if not live["heartbeat_present"]:
        report.add("daemon", WARN, "no heartbeat; not running (or never started)", "min_agent.cli --daemon")
        return
    if live["live"]:
        report.add("daemon", OK, f"pid {live['pid']} alive, age {live['age_seconds']:.0f}s")
        return
    report.add(
        "daemon", FAIL,
        f"claims {live['status']} but pid_alive={live['pid_alive']} stale={live['stale']} "
        f"age={live['age_seconds']}s",
        "start it: min_agent.cli --daemon",
    )


def _check_journal(report: DoctorReport, config: AgentConfig) -> None:
    journal = JsonlJournal(config.journal_path)
    stats = journal.stats()
    if not stats["exists"]:
        report.add("journal", WARN, "absent; no cycles recorded yet", "run one cycle: min_agent.cli --once")
        return
    records = journal.read_all()
    size_mb = coerce.field_float_or(stats.get("bytes"), "stats.bytes", 0.0) / 1e6
    detail = (
        f"{size_mb:.1f}MB, {coerce.field_int(stats.get('lines'), 'stats.lines')} lines, "
        f"{len(records)} cycles"
    )
    if journal.last_dropped_lines:
        report.add(
            "journal", WARN,
            f"{detail}; {journal.last_dropped_lines} unparseable line(s) dropped",
            f"first: {journal.last_dropped_samples[:1]}",
        )
    else:
        report.add("journal", OK, detail)

    # State the window the journal actually covers. Rotation deletes the oldest
    # generation, so after it fires the file is a recent fragment rather than the record,
    # and `len(records) cycles` reads like a total when it is not. Several analyses are
    # recomputed from the journal every pass - calibration, the experiment chain, champion
    # history - so past the horizon they return a confident number computed from a
    # fragment, which is worse than returning nothing.
    #
    # Truncation is judged against the strategy registry rather than by counting
    # generations: the registry survives rotation, so its earliest admission proves when
    # the system existed. A generation boundary cannot distinguish "never had more history"
    # from "had more and lost it".
    # `StrategyLibrary.list` does not raise: it returns [] for a missing directory and
    # collects per-file parse failures internally, so there is nothing to guard.
    admitted = StrategyLibrary(config.strategy_dir).list()
    known_since = min((s.created_at for s in admitted), default=None)
    window = journal.retained_window(known_since=known_since)
    if window["oldest"]:
        window_detail = (
            f"retained {window['oldest'][:19]} .. {(window['newest'] or '')[:19]} "
            f"across {window['generations']} generation(s)"
        )
        if window["truncated"]:
            report.add(
                "journal window", WARN,
                f"{window_detail}; older cycles have been rotated away",
                "model calibration, the experiment chain and champion history are "
                "recomputed from the retained window only - treat them as partial",
            )
        else:
            report.add("journal window", OK, window_detail)
    utilization = coerce.field_float(stats.get("utilization"), "stats.utilization") or 0.0
    if utilization > 0.8:
        report.add(
            "journal headroom", WARN,
            f"{utilization:.0%} of the "
            f"{coerce.field_float_or(stats.get('max_bytes'), 'stats.max_bytes', 0.0) / 1e6:.0f}MB cap used; "
            "rotation imminent",
        )
    else:
        report.add("journal headroom", OK, f"{utilization:.0%} of cap used")
    if not records:
        report.add("journal recency", WARN, "no cycle records in the journal")
    else:
        latest = records[-1].snapshot.timestamp
        age = (datetime_now() - latest).total_seconds()
        report.add(
            "journal recency", OK if age < 86_400 else WARN,
            f"last cycle {age / 3600:.1f}h ago ({latest.isoformat()})",
        )


def _check_strategy_library(report: DoctorReport, config: AgentConfig) -> None:
    library = StrategyLibrary(config.strategy_dir)
    strategies = library.list()
    if not strategies:
        report.add("strategy library", FAIL, f"no readable strategies in {config.strategy_dir}")
        return
    selectable = [
        s for s in strategies
        if s.enabled and s.lifecycle not in {"PAUSED", "RETIRED"}
    ]
    status = OK if selectable else FAIL
    report.add(
        "strategy library", status,
        f"{len(strategies)} total, {len(selectable)} selectable",
        "" if selectable else "every strategy is disabled/paused/retired, so nothing can trade",
    )
    if library.rejected:
        report.add(
            "strategy files", FAIL,
            f"{len(library.rejected)} unreadable: {[n for n, _ in library.rejected][:5]}",
            "a torn or invalid strategy file silently vanished from the selector",
        )
    else:
        report.add("strategy files", OK, "all parse")


def _check_knowledge(report: DoctorReport, config: AgentConfig) -> None:
    library = KnowledgeLibrary(config.knowledge_dir)
    try:
        artifacts = library.list()
    except Exception as exc:
        report.add("knowledge library", FAIL, f"{type(exc).__name__}: {exc}")
        return
    accepted = [a for a in artifacts if a.status == "ACCEPTED"]
    report.add(
        "knowledge library", OK if accepted else WARN,
        f"{len(artifacts)} artifact(s), {len(accepted)} accepted",
        "" if accepted else "no accepted lesson, so the knowledge path cannot inform a decision",
    )


def _check_proof(report: DoctorReport, config: AgentConfig) -> None:
    """The only success criterion. Cycle counts are not evidence.

    The three checks are always emitted, including when there is nothing to
    measure, so the report shape is stable and greppable.
    """
    journal = JsonlJournal(config.journal_path)
    records = journal.read_all()
    if not records:
        report.add("end-to-end proof", WARN, "no cycles recorded; nothing proven yet", "run one: minictrl once")
        for name, detail in (
            ("proof: submitted>0", "unknown: no cycles recorded"),
            ("proof: filled>0", "unknown: no cycles recorded"),
            ("proof: per-strategy pnl", "unknown: no cycles recorded"),
        ):
            report.add(name, WARN, detail, "run one: minictrl once")
        return

    fills: dict[str, float] = {}
    for event in journal.read_events(FILL_EVENT):
        coid = event.payload.get("client_order_id")
        qty = event.payload.get("filled_quantity")
        if isinstance(coid, str) and isinstance(qty, (int, float)) and qty > 0:
            fills[coid] = max(fills.get(coid, 0.0), float(qty))
    # The broker evidence batch is required, not optional. Without it
    # _pnl_evidence returns MISSING before it looks at anything, so the doctor
    # reported "per-strategy pnl empty" for a session in which three strategies
    # had broker-verified closed-lot PnL totalling 564.39 - the same batch the
    # daemon records and the CLI report reads. A health check that cannot see the
    # proof it is meant to certify is worse than no check.
    evidence = latest_evidence_batch(journal)
    evaluation = DeterministicEvaluator().evaluate(
        records,
        evidence=evidence,
        fills=fills,
        seeded_fills=confirmed_fill_activities(journal, records),
    )

    submitted = evaluation.submitted_orders
    filled = evaluation.filled_quantity
    per_strategy_pnl = {
        sid: m.realized_pnl
        for sid, m in evaluation.strategy_metrics.items()
        if m.realized_pnl is not None
    }
    report.add(
        "proof: submitted>0", OK if submitted else WARN, f"{submitted} order(s) submitted",
        "" if submitted else "no order has ever been submitted",
    )
    report.add(
        "proof: filled>0", OK if filled else WARN, f"{filled} share(s) filled ({len(fills)} confirmed)",
        "" if filled else "no fill has been confirmed from the broker yet",
    )
    if per_strategy_pnl:
        detail = ", ".join(f"{k}={v:+.2f}" for k, v in sorted(per_strategy_pnl.items())[:6])
        report.add("proof: per-strategy pnl", OK, f"{len(per_strategy_pnl)} strategy/ies: {detail}")
    else:
        report.add(
            "proof: per-strategy pnl", WARN,
            f"empty (pnl_evidence={evaluation.pnl_evidence})",
            "needs broker closed-lot evidence; a fill alone is not PnL",
        )
    risk_halts = evaluation.hold_reasons.get("risk_limit_near", 0)
    total_holds = evaluation.action_counts.get("HOLD", 0)
    if risk_halts:
        share = risk_halts / total_holds if total_holds else 0
        report.add(
            "risk-driven halts", WARN,
            f"{risk_halts}/{total_holds} HOLD cycles were attributed to a near risk limit "
            f"by the decision maker ({share:.0%})",
            "Guardian enforces the limits independently. A decision maker that abstains "
            "on risk grounds is acting as a second risk authority and can halt trading "
            "without appearing to - see TradeDecision.hold_reason",
        )
    elif total_holds:
        report.add(
            "risk-driven halts", OK,
            f"0/{total_holds} HOLD cycles attributed to a risk limit",
        )
    if evaluation.pnl_evidence == PNL_EVIDENCE_MISSING:
        report.add(
            "pnl evidence", WARN, PNL_EVIDENCE_MISSING,
            "run --ingest-evidence to pull broker activities and portfolio history",
        )
    else:
        report.add("pnl evidence", OK, evaluation.pnl_evidence)


def datetime_now():
    return datetime.now(tz=timezone.utc)


def build_client(config: AgentConfig):
    if config.missing_alpaca_credentials():
        return None
    try:
        import alpaca_trade_api as tradeapi
    except ImportError:
        return None
    base = config.alpaca_base_url.rstrip("/")
    if base.endswith("/v2"):
        base = base[:-3]
    try:
        return tradeapi.REST(
            key_id=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            base_url=base,
            api_version="v2",
        )
    except Exception:
        return None


__all__ = ["FAIL", "OK", "SKIP", "WARN", "DoctorReport", "Status", "build_client", "run_doctor"]
