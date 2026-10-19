"""Observe the running paper agent; never run a cycle or execute an order."""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from autopoiesis.atomicio import write_json_atomic
from autopoiesis.broker_client import create_alpaca_client, list_open_orders
from autopoiesis.cli import _build_agent_daemon
from autopoiesis.config import AgentConfig
from autopoiesis.daemon import source_fingerprint
from autopoiesis.fill_reconciler import TERMINAL_STATUSES, confirmed_fill_events
from autopoiesis.health import HealthMonitor
from autopoiesis.journal import JsonlJournal
from autopoiesis.learning_evidence import eligibility, valid_provenance
from autopoiesis.models import CycleRecord, JournalEvent, OpenOrderSnapshot, Side, TradeDecision
from autopoiesis.submission_state import project_submission_state

NY = ZoneInfo("America/New_York")
CONFIG_FIELDS = (
    "mode", "symbols", "allowlist", "model", "gpu_devices", "max_position_value",
    "max_daily_loss", "max_trades_per_day", "max_total_exposure", "max_account_value",
    "min_confidence", "stale_after_seconds", "shorts", "shadow", "manage_account",
    "universe", "min_price", "attention_slots", "daemon_interval_seconds",
    "llm_timeout_seconds", "curriculum_enabled", "reflect_every", "curriculum_every",
    "maintenance_interval_seconds", "evidence_interval_seconds", "reflection_interval_seconds",
    "curriculum_interval_seconds", "max_daily_cycles",
)


class ObservationRefused(Exception):
    def __init__(self, code: str, exit_code: int = 1):
        self.code = code
        self.exit_code = exit_code


def _require(condition, code: str, exit_code: int = 1):
    if not condition:
        raise ObservationRefused(code, exit_code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "TIMESTAMP_NOT_AWARE")
    return parsed.astimezone(timezone.utc)


def _process_environment(pid: int) -> tuple[dict[str, str], Path]:
    """Credentials remain private in memory; never serialize the process environment."""
    process = Path("/proc") / str(pid)
    entries = process.joinpath("environ").read_bytes().split(b"\0")
    environment = dict(entry.decode().split("=", 1) for entry in entries if b"=" in entry)
    return environment, process.joinpath("cwd").resolve(strict=True)


def _health(config, expected, now):
    monitor = HealthMonitor(config.heartbeat_path, stale_after_seconds=config.stale_after_seconds)
    heartbeat = monitor.read()
    if heartbeat is None:
        raise ObservationRefused("DAEMON_NOT_LIVE")
    _require(heartbeat.status in {"RUNNING", "STARTING", "BACKING_OFF"}
             and monitor._pid_probe(heartbeat.pid), "DAEMON_NOT_LIVE")
    _require(0 <= (now - _timestamp(heartbeat.timestamp)).total_seconds() <= config.stale_after_seconds, "HEARTBEAT_NOT_FRESH")
    _require(heartbeat.source_fingerprint == expected == source_fingerprint(), "SOURCE_MISMATCH")
    return heartbeat


class ReadOnlyJournal(JsonlJournal):
    """Use existing read-only lock descriptors, and reject every dropped history row."""

    @contextmanager
    def _read_lock(self, *, exclusive=False):
        _require(not exclusive, "JOURNAL_WRITE_FORBIDDEN")
        descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_RDONLY)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ObservationRefused("JOURNAL_BUSY", 2) from None
            yield
        finally:
            os.close(descriptor)

    def archive_manifest_status(self):
        _require(self.archive_manifest_path.is_file(), "ARCHIVE_MANIFEST_MISSING")
        return super().archive_manifest_status()

    def _scan(self, model, *, limit, keep=None):
        result = super()._scan(model, limit=limit, keep=keep)
        _require(self.last_dropped_lines == 0, "CORRUPT_HISTORY")
        return result


def _journal(config):
    journal = ReadOnlyJournal(config.journal_path)
    _require(journal.path.is_file(), "JOURNAL_MISSING")
    _require(journal.path.with_suffix(journal.path.suffix + ".lock").is_file(), "JOURNAL_LOCK_MISSING")
    _require(journal.archive_manifest_path.is_file(), "ARCHIVE_MANIFEST_MISSING")
    with journal._read_lock():
        archive = journal.archive_manifest_status()
        _require(archive["valid"] and archive["trusted"], "ARCHIVE_NOT_TRUSTED")
        records = journal.read_all()
        events = journal.read_events()
        state = project_submission_state(journal)
        _require(not state.unresolved, "UNRESOLVED_SUBMISSIONS")
    return journal, records, events, state


def _clock(client, now, stale_after):
    clock = client.get_clock()
    stamp = _timestamp(clock.timestamp)
    _require(abs((now - stamp).total_seconds()) <= min(stale_after, 60), "BROKER_CLOCK_NOT_FRESH")
    return clock


def _session(client, session_date):
    rows = client.get_calendar(start=session_date.isoformat(), end=session_date.isoformat())
    if not rows:
        raise ObservationRefused("NO_MARKET_SESSION", 2)
    _require(len(rows) == 1, "CALENDAR_AMBIGUOUS")
    row = rows[0]
    day = row.date.date() if isinstance(row.date, datetime) else date.fromisoformat(str(row.date)[:10])
    _require(day == session_date, "CALENDAR_DATE_MISMATCH")
    _require(isinstance(row.open, time) and isinstance(row.close, time), "CALENDAR_TIME_INVALID")
    opened = datetime.combine(day, row.open, NY).astimezone(timezone.utc)
    closed = datetime.combine(day, row.close, NY).astimezone(timezone.utc)
    _require(opened < closed, "CALENDAR_HOURS_INVALID")
    return opened, closed


def _broker_state(client):
    account = client.get_account()
    _require(str(account.status) == "ACTIVE", "ACCOUNT_NOT_ACTIVE")
    for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        _require(getattr(account, flag, None) is False, "ACCOUNT_BLOCKED_OR_UNKNOWN")
    positions = client.list_positions()
    try:
        orders = list_open_orders(client)
    except RuntimeError:
        raise ObservationRefused("OPEN_ORDERS_INCOMPLETE") from None
    snapshots = tuple(OpenOrderSnapshot(
        symbol=str(order.symbol), side=cast("Side", str(order.side).upper()), quantity=float(order.qty), status=str(order.status),
    ) for order in orders)
    return positions, snapshots


def _preview(daemon, snapshots, records, heartbeat):
    daemon.on_cycle_count(heartbeat.cycle_count)
    daemon.knowledge_admission.knowledge_library.verify_journal_sources(records)
    engine = daemon.loop.decision_engine
    strategies = daemon.strategy_library.list()
    _require(not daemon.strategy_library.rejected, "STRATEGY_FILES_INVALID")
    results = engine._results(strategies)
    coverage = []
    selected_snapshot = None
    for snapshot in snapshots:
        selected = engine.policy_engine.selector.select(
            strategies, results, snapshot.last_price, engine._served(), symbol=snapshot.symbol,
        )
        coverage.append({"symbol": snapshot.symbol, "selectable": selected is not None,
                         "uncovered_behavior": None if selected is not None else "HOLD_ZERO_WITHOUT_ATTRIBUTION"})
        if selected_snapshot is None and selected is not None:
            selected_snapshot = snapshot
    if selected_snapshot is None:
        raise ObservationRefused("NO_COVERED_PREVIEW_SYMBOL")
    observed = []
    decide = engine.llm.decide

    def observe(context):
        result = decide(context)
        # Revalidate even if a transport returns an already-created object.
        valid = TradeDecision.model_validate(result.model_dump()) if isinstance(result, TradeDecision) else None
        observed.append(valid is not None and valid.symbol == selected_snapshot.symbol)
        return result

    engine.llm.decide = observe
    try:
        decision = engine.decide_snapshot(selected_snapshot)
    finally:
        engine.llm.decide = decide
    _require(isinstance(decision, TradeDecision), "PREVIEW_DECISION_INVALID")
    provenance = valid_provenance(decision.strategy_provenance)
    _require(provenance is not None and decision.strategy_id == provenance.strategy_id
             and decision.symbol == selected_snapshot.symbol and decision.symbol in provenance.universe,
             "PREVIEW_PROVENANCE_INVALID")
    loop = daemon.loop
    review_at = _now()
    guardian = loop.guardian.review(
        decision, selected_snapshot, mode=daemon.config.mode, now=review_at,
        trades_today=loop.trade_counter.trades_today(now=review_at),
        agent_position_quantity=loop._sellable_holding(decision.symbol, selected_snapshot),
        agent_short_quantity=loop._agent_short(decision.symbol),
    )
    result = {
        "symbol": decision.symbol, "action": decision.action, "quantity": decision.quantity,
        "decision_source": decision.decision_source, "strategy_id": decision.strategy_id,
        "valid_provenance": True, "model_response_observed": observed == [True],
        "guardian_approved": guardian.approved, "guardian_review_at": review_at.isoformat(),
        "guardian_refusal": None if guardian.approved else (
            "STALE_DATA" if loop.guardian._is_stale(selected_snapshot, now=review_at) else
            "MARKET_CLOSED" if not selected_snapshot.market_open else "RISK_REFUSAL"
        ),
        "closed_market_approval_certified": False,
    }
    return coverage, result


def _fill_observations(submissions, events, now):
    """Project bounded cumulative broker observations, independently of learning."""
    by_order = {record.execution.order_id: record for record in submissions.values()}
    observed = {}
    current = [event for event in events if _timestamp(event.timestamp) <= now]
    for event in confirmed_fill_events(current):
        payload = event.payload
        client = payload.get("client_order_id")
        order = payload.get("order_id")
        client = client if isinstance(client, str) else ""
        order = order if isinstance(order, str) else ""
        record = submissions.get(client) or by_order.get(order)
        if record is None:
            continue
        _require(client == record.execution.client_order_id and order == record.execution.order_id
                 and payload.get("symbol") == record.symbol and payload.get("side") == record.decision.action
                 and payload.get("strategy_id") == record.strategy_id
                 and payload.get("requested_quantity") == record.decision.quantity
                 and payload.get("source") == "alpaca" and payload.get("paper_only") is True,
                 "FILL_IDENTITY_MISMATCH")
        quantity = payload.get("cumulative_filled_quantity", payload.get("filled_quantity"))
        if (not isinstance(quantity, (int, float)) or isinstance(quantity, bool)
                or not math.isfinite(quantity) or not 0 <= quantity <= record.decision.quantity):
            raise ObservationRefused("FILL_QUANTITY_INVALID")
        observed[client] = (float(quantity), str(payload.get("broker_status", "")).lower())
    filled = 0.0
    pending = 0
    for client, record in submissions.items():
        quantity, status = observed.get(client, (record.execution.filled_quantity,
                                                str(record.execution.broker_status or "").lower()))
        _require(math.isfinite(quantity) and record.execution.filled_quantity <= quantity <= record.decision.quantity,
                 "FILL_QUANTITY_INVALID")
        filled += quantity
        pending += status not in TERMINAL_STATUSES
    return filled, pending


def _session_evidence(daemon, records, events, state, opened, closed, after, now):
    start = max(opened, after)
    selected = [record for record in records if start <= _timestamp(record.snapshot.timestamp) <= min(now, closed)]
    selected_events = [event for event in events if start <= _timestamp(event.timestamp) <= min(now, closed)]
    _require(not any(event.event_type == "CYCLE_FAILED" for event in selected_events), "SESSION_CYCLE_FAILED")
    _require(len({record.cycle_id for record in selected}) == len(selected), "DUPLICATE_CYCLE_ID")
    _require(len({event.event_id for event in selected_events}) == len(selected_events), "DUPLICATE_EVENT_ID")
    covered = {record.symbol for record in selected}
    missing = sorted(set(daemon.config.symbols) - covered)
    report = {
        "cycles": len(selected), "configured_symbols_observed": sorted(covered & set(daemon.config.symbols)),
        "missing_configured_symbols": missing, "first_round_deadline": (start + timedelta(minutes=60)).isoformat(),
        "model_decisions": 0, "fallback_decisions": 0, "policy_decisions": 0,
        "safe_unattributed_abstentions": 0, "eligible_cycles": 0, "filled_quantity": 0.0,
        "submitted_orders": 0, "pending_fill_observations": 0, "learning_feedback": "UNOBSERVED",
        "actionable_unexecuted": 0,
        "historical_cycle_producer_fingerprint": "UNOBSERVED",
    }
    submissions: dict[str, CycleRecord] = {}
    submitted_orders = set()
    intents: dict[str, JournalEvent] = {}
    acknowledgements: dict[str, JournalEvent] = {}
    for event in events:
        if event.event_type not in {"ORDER_SUBMISSION_INTENT", "ORDER_SUBMISSION_ACKNOWLEDGED"}:
            continue
        client = event.payload.get("client_order_id")
        target = intents if event.event_type == "ORDER_SUBMISSION_INTENT" else acknowledgements
        _require(client not in target, "DUPLICATE_SUBMISSION_EVENT")
        target[client] = event
    for record in selected:
        snapshot, decision, execution = record.snapshot, record.decision, record.execution
        _require(snapshot.source == "alpaca" and snapshot.market_open, "SESSION_SNAPSHOT_INVALID")
        _require(snapshot.quote_time is not None and not daemon.loop.guardian._is_stale(snapshot, now=_timestamp(snapshot.timestamp)), "SESSION_QUOTE_NOT_FRESH")
        _require(not record.error and execution.status not in {"ERROR", "SUBMISSION_UNKNOWN", "REPLAYED"}, "SESSION_EXECUTION_ERROR")
        attributed = eligibility(record) == "ELIGIBLE"
        # Two markers mean the same thing here, and only one was accepted.
        #
        # `selection_unavailable:` is what the engine writes when no strategy matched at all.
        # `no_strategy:` is what it writes for a symbol no strategy covers - the uncovered-symbol
        # path. Both produce a HOLD with no strategy, no provenance, no order and a SKIPPED
        # execution, so both are abstentions rather than evidence about the model. Accepting only
        # the first made this check fail on any session that asked the model about an uncovered
        # symbol, which is every session now that `universe=tradable` with four attention slots.
        #
        # Measured 2026-10-19: 18 of the day's 27 records were `no_strategy:` abstentions, and
        # `market-open-verify` failed at 09:45 with SESSION_PROVENANCE_INVALID on a session whose
        # guardian approved all 25 of its decisions and placed only allowlisted orders.
        #
        # This is not a loosening. Every other conjunct still holds: HOLD, quantity zero, no
        # strategy id, no provenance, SKIPPED. A record with any of those wrong is still refused.
        abstention = decision.rationale.startswith(("selection_unavailable:", "no_strategy:"))
        safe = (decision.action == "HOLD" and decision.quantity == 0 and decision.strategy_id is None
                and record.strategy_id is None and decision.strategy_provenance is None
                and abstention and execution.status == "SKIPPED")
        _require(attributed or safe, "SESSION_PROVENANCE_INVALID")
        if safe:
            report["safe_unattributed_abstentions"] += 1
        else:
            report["eligible_cycles"] += 1
        _require(decision.symbol == snapshot.symbol, "SESSION_SYMBOL_MISMATCH")
        source_key = {"llm": "model_decisions", "fallback_policy_engine": "fallback_decisions"}.get(decision.decision_source, "policy_decisions")
        report[source_key] += 1
        if decision.action != "HOLD" and execution.status != "SUBMITTED":
            report["actionable_unexecuted"] += 1
        if execution.status == "SUBMITTED":
            _require(decision.action != "HOLD" and attributed and record.guardian.approved, "SUBMISSION_WITHOUT_APPROVAL")
            client = execution.client_order_id
            _require(client and client not in submissions and execution.order_id, "SUBMISSION_IDENTITY_INVALID")
            _require(execution.order_id not in submitted_orders, "DUPLICATE_BROKER_ORDER_ID")
            submitted_orders.add(execution.order_id)
            submissions[client] = record
            intent, ack = intents.get(client), acknowledgements.get(client)
            if intent is None or ack is None:
                raise ObservationRefused("SUBMISSION_EVIDENCE_MISSING")
            _require(client in state.acknowledged, "SUBMISSION_EVIDENCE_MISSING")
            for event in (intent, ack):
                _require(event.cycle_id == record.cycle_id and event.strategy_id == record.strategy_id
                         and event.payload.get("symbol") == decision.symbol
                         and event.payload.get("action") == decision.action
                         and event.payload.get("quantity") == decision.quantity, "SUBMISSION_IDENTITY_MISMATCH")
            _require(ack.payload.get("order_id") == execution.order_id, "SUBMISSION_ORDER_MISMATCH")
            report["submitted_orders"] += 1
    report["filled_quantity"], report["pending_fill_observations"] = _fill_observations(submissions, events, now)
    return report, missing


def collect(*, phase, expected_fingerprint, session_date=None, after=None, bootstrap=None):
    now = _now()
    report = {"phase": phase, "observed_at": now.isoformat(), "status": "FAIL", "exit_code": 1,
              "diagnostics": [], "profitability": "UNPROVEN", "additional_held_attention_coverage": "UNOBSERVED"}
    try:
        _require(phase in {"preopen", "open"}, "PHASE_INVALID")
        _require(re.fullmatch(r"[0-9a-f]{16}", expected_fingerprint) is not None, "EXPECTED_FINGERPRINT_INVALID")
        _require(phase != "open" or after is not None, "DEPLOYMENT_CUTOFF_REQUIRED")
        bootstrap = bootstrap or AgentConfig.from_env()
        monitor = HealthMonitor(bootstrap.heartbeat_path)
        heartbeat = monitor.read()
        if heartbeat is None:
            raise ObservationRefused("DAEMON_NOT_LIVE")
        _require(heartbeat.status in {"RUNNING", "STARTING", "BACKING_OFF"}
                 and monitor._pid_probe(heartbeat.pid), "DAEMON_NOT_LIVE")
        _require(heartbeat.source_fingerprint == expected_fingerprint == source_fingerprint(), "SOURCE_MISMATCH")
        environment, cwd = _process_environment(heartbeat.pid)
        config = AgentConfig.from_env(environment)
        config = replace(config, **{name: (path if path.is_absolute() else cwd / path).resolve() for name in (
            "journal_path", "heartbeat_path", "pidfile_path", "strategy_dir", "knowledge_dir",
        ) for path in [getattr(config, name)]})
        report["report_directory"] = str(config.journal_path.parent / "readiness")
        _require(config.heartbeat_path == bootstrap.heartbeat_path.resolve(), "HEARTBEAT_PATH_MISMATCH")
        _require(config.mode == "paper" and config.is_paper_endpoint(), "PAPER_CONFIG_REQUIRED")
        _require(not config.missing_alpaca_credentials(), "PAPER_CREDENTIALS_REQUIRED")
        _require(config.pidfile_path.is_file() and config.pidfile_path.read_text().strip() == str(heartbeat.pid), "PIDFILE_MISMATCH")
        actual_heartbeat = _health(config, expected_fingerprint, now)
        _require(actual_heartbeat.pid == heartbeat.pid, "PROCESS_CHANGED", 2)
        heartbeat = actual_heartbeat
        _require(len(config.symbols) <= 64 and len(config.allowlist) <= 512
                 and all(len(str(getattr(config, name))) <= 128 for name in ("model", "gpu_devices"))
                 and all(len(symbol) <= 32 for symbol in (*config.symbols, *config.allowlist)),
                 "CONFIG_REPORT_BOUNDS_EXCEEDED")
        report["config"] = {name: sorted(value) if isinstance(value, (tuple, frozenset)) else value for name in CONFIG_FIELDS for value in [getattr(config, name)]}
        report["daemon"] = {"pid": heartbeat.pid, "source_fingerprint": expected_fingerprint,
                            "heartbeat_at": heartbeat.timestamp.isoformat(), "cycle_count": heartbeat.cycle_count,
                            "status": heartbeat.status, "error_count": heartbeat.error_count,
                            "broker_healthy": heartbeat.last_broker_ok, "model_healthy": heartbeat.last_model_ok}
        _require(heartbeat.status == "RUNNING", "DAEMON_NOT_RUNNING", 2)
        journal, records, events, state = _journal(config)
        report["history"] = {"cycles": len(records), "events": len(events), "trusted_archive": True, "unresolved_submissions": 0}
        client = create_alpaca_client(key_id=config.alpaca_api_key or "", secret_key=config.alpaca_secret_key or "", base_url=config.alpaca_base_url)
        clock = _clock(client, now, config.stale_after_seconds)
        initially_open = bool(clock.is_open)
        session_date = session_date or now.astimezone(NY).date()
        opened, closed = _session(client, session_date)
        report["calendar"] = {"session_date": session_date.isoformat(), "open": opened.isoformat(), "close": closed.isoformat(),
                              "broker_clock": _timestamp(clock.timestamp).isoformat(), "market_open": bool(clock.is_open)}
        positions, orders = _broker_state(client)
        report["broker"] = {"active_account": True, "positions": len(positions), "open_orders": len(orders), "open_orders_complete": True}
        # Construction only: neither daemon.run nor any maintenance/reconciliation hook.
        with journal._read_lock():
            daemon = _build_agent_daemon(config, client, journal=journal)
        report["scheduler_trade_now"] = daemon.scheduler.should_trade_now()
        if phase == "preopen":
            _require(now < opened and not clock.is_open, "PREOPEN_PHASE_ENDED", 2)
            snapshots = [daemon.loop.data_gateway.snapshot(symbol).model_copy(update={"open_orders": orders}) for symbol in config.symbols]
            for symbol, snapshot in zip(config.symbols, snapshots, strict=True):
                _require(snapshot.symbol == symbol and snapshot.source == "alpaca" and not snapshot.market_open, "PREVIEW_SNAPSHOT_INVALID")
                _require(snapshot.quote_time is not None and abs((_timestamp(snapshot.timestamp) - now).total_seconds()) <= 60, "PREVIEW_TIMESTAMP_INVALID")
            report["data"] = [{"symbol": snapshot.symbol, "snapshot_at": snapshot.timestamp.isoformat(), "quote_at": snapshot.quote_time.isoformat(),
                               "last_price": snapshot.last_price, "stale_quote": daemon.loop.guardian._is_stale(snapshot, now=now)} for snapshot in snapshots]
            coverage, preview = _preview(daemon, snapshots, records, heartbeat)
            report["coverage"], report["preview"] = coverage, preview
            _require(preview["model_response_observed"], "MODEL_RESPONSE_NOT_OBSERVED")
        else:
            _require(now.astimezone(NY).date() == session_date and opened <= now < closed and bool(clock.is_open), "SESSION_NOT_OPEN", 2)
            after = _timestamp(after)
            _require(after <= now, "DEPLOYMENT_CUTOFF_IN_FUTURE", 2)
            session, missing = _session_evidence(daemon, records, events, state, opened, closed, after, now)
            report["session"] = session
            if missing:
                raise ObservationRefused("FIRST_ROUND_COVERAGE_MISSING", 1 if now >= max(opened, after) + timedelta(minutes=60) else 2)
        latest_now = _now()
        latest_clock = _clock(client, latest_now, config.stale_after_seconds)
        try:
            latest = _health(config, expected_fingerprint, latest_now)
        except ObservationRefused:
            raise ObservationRefused("OBSERVATION_CHANGED", 2) from None
        report["daemon"].update(
            pid=latest.pid, heartbeat_at=latest.timestamp.isoformat(), cycle_count=latest.cycle_count,
            status=latest.status, error_count=latest.error_count,
            broker_healthy=latest.last_broker_ok, model_healthy=latest.last_model_ok,
        )
        _require(latest.status == "RUNNING", "DAEMON_NOT_RUNNING", 2)
        _require(latest.pid == heartbeat.pid and bool(latest_clock.is_open) == initially_open
                 and (latest_now < opened if phase == "preopen" else opened <= latest_now < closed), "OBSERVATION_CHANGED", 2)
        # Slow model callbacks can read history again: validate full state once more.
        _journal(config)
        report.update(status="READY" if phase == "preopen" else "VERIFIED", exit_code=0)
    except ObservationRefused as exc:
        report.update(status="WAIT" if exc.exit_code == 2 else "FAIL", exit_code=exc.exit_code, diagnostics=[exc.code])
    except Exception as exc:
        report.update(status="FAIL", exit_code=1, diagnostics=["OBSERVATION_EXCEPTION"], exception_type=type(exc).__name__)
    return report


def write_report(path: Path, report) -> None:
    _require(path.suffix == ".json" and not path.exists(), "OUTPUT_MUST_BE_NEW_JSON")
    write_json_atomic(path, report)
    _require(path.stat().st_mode & 0o777 == 0o600, "OUTPUT_NOT_PRIVATE")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("preopen", "open"), required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--session-date", type=date.fromisoformat)
    parser.add_argument("--after", type=_timestamp)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scheduled", action="store_true", help="Return process success for WAIT while preserving its JSON verdict.")
    args = parser.parse_args(argv)
    report = collect(phase=args.phase, expected_fingerprint=args.expected_fingerprint, session_date=args.session_date, after=args.after)
    output = args.output
    if output is None and "report_directory" in report:
        output = Path(report["report_directory"]) / f"{_now():%Y%m%dT%H%M%S%fZ}-{args.phase}.json"
    if output is not None:
        try:
            write_report(output, report)
        except Exception as exc:
            report.update(status="FAIL", exit_code=1, diagnostics=["REPORT_WRITE_FAILED"], exception_type=type(exc).__name__)
    print(json.dumps(report, sort_keys=True))
    return 0 if args.scheduled and report["status"] == "WAIT" and report["exit_code"] == 2 else report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
