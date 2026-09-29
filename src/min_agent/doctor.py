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

from min_agent.atomicio import write_json_atomic
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
from min_agent.models import BrokerEvidenceBatch
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
    unaccounted = []
    for strategy in library.list():
        if strategy.lifecycle in decided.values():
            continue
        if not decided.get(strategy.strategy_id) and strategy.lifecycle not in {
            "PROBATION", "BASELINE", "ACTIVE"
        }:
            unaccounted.append(f"{strategy.strategy_id}={strategy.lifecycle}")
    if unaccounted:
        report.add(
            "lifecycle provenance", FAIL,
            f"{len(unaccounted)} strategy/ies reached a gated state with no recorded "
            f"decision: {', '.join(unaccounted[:6])}",
            "PAUSED/RETIRED are terminal; only the lifecycle manager may set them",
        )
    else:
        report.add(
            "lifecycle provenance", OK,
            f"{len(decided)} lifecycle decision(s) all traceable to the journal",
        )


def _check_admission_provenance(
    report: DoctorReport, config: AgentConfig, journal: JsonlJournal
) -> None:
    accepted = {
        event.strategy_id
        for event in journal.read_events("STRATEGY_ADMISSION_REVIEWED")
        if event.status == "ACCEPTED" and event.strategy_id
    }
    library = StrategyLibrary(config.strategy_dir)
    if not accepted:
        # Pre-existing library from before the admission gate was journaled.
        report.add("admission provenance", WARN,
                   "no accepted admissions in the journal to compare the library against")
        return
    missing = [s.strategy_id for s in library.list() if s.strategy_id not in accepted]
    if missing:
        report.add(
            "admission provenance", WARN,
            f"{len(missing)} strategy file(s) have no accepted admission event: "
            f"{', '.join(missing[:6])}",
            "legacy from before admission was journalled, or written outside the gate",
        )
    else:
        report.add("admission provenance", OK, f"all {len(accepted)} file(s) traceable to admission")


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
    if "paper" in config.alpaca_base_url:
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
    report.add(
        "broker positions", OK,
        f"{len(positions)} position(s), {len(orders)} open order(s)",
        hint="" if positions or orders else "an empty book is correct for a fresh paper account",
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
    detail = f"{stats['bytes'] / 1e6:.1f}MB, {stats['lines']} lines, {len(records)} cycles"
    if journal.last_dropped_lines:
        report.add(
            "journal", WARN,
            f"{detail}; {journal.last_dropped_lines} unparseable line(s) dropped",
            f"first: {journal.last_dropped_samples[:1]}",
        )
    else:
        report.add("journal", OK, detail)
    if stats["utilization"] and stats["utilization"] > 0.8:
        report.add(
            "journal headroom", WARN,
            f"{stats['utilization']:.0%} of the {stats['max_bytes'] / 1e6:.0f}MB cap used; rotation imminent",
        )
    else:
        report.add("journal headroom", OK, f"{stats['utilization']:.0%} of cap used")
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

    fills = {}
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
    evidence_events = journal.read_events("BROKER_EVIDENCE_INGESTED")
    evidence = (
        BrokerEvidenceBatch.model_validate(evidence_events[-1].payload)
        if evidence_events
        else None
    )
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


__all__ = ["DoctorReport", "run_doctor", "build_client", "Status", "OK", "WARN", "FAIL", "SKIP"]
