from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests

from min_agent.broker_evidence import BrokerEvidenceProvider, latest_evidence_batch
from min_agent.config import AgentConfig
from min_agent.curriculum import StructuredCurriculumAgent, generate_curriculum_task_json
from min_agent.daemon import AgentDaemon
from min_agent.data_gateway import AlpacaDataGateway
from min_agent.evaluator import (
    PNL_EVIDENCE_ACCOUNT_VERIFIED,
    PNL_EVIDENCE_MISSING,
    PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
    DeterministicEvaluator,
    confirmed_fill_activities,
)
from min_agent.executor import AlpacaPaperExecutor
from min_agent.fill_reconciler import FillReconciler
from min_agent.guardian import Guardian
from min_agent.health import HealthMonitor
from min_agent.journal import JsonlJournal
from min_agent.knowledge_admission import KnowledgeAdmission
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.llm_decision import (
    CURRICULUM_NUM_CTX,
    CURRICULUM_TIMEOUT_S,
    HybridDecisionEngine,
    OllamaDecisionEngine,
)
from min_agent.loop import TradingLoop
from min_agent.models import BrokerEvidenceBatch, JournalEvent, JournalEventStatus
from min_agent.order_reconciler import OrderReconciler
from min_agent.policy_engine import PolicyEngine
from min_agent.reflection_memory import ReflectionMemory
from min_agent.scheduler import MarketScheduler
from min_agent.shadow import ShadowExecutor
from min_agent.strategy_admission import StrategyAdmission
from min_agent.strategy_engine import StrategyLibrary, StrategyLifecycleManager
from min_agent.trade_counter import TradeCounter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the minimum Alpaca paper-trading agent.")
    parser.add_argument("--check-env", action="store_true", help="Check external dependencies without trading.")
    parser.add_argument("--once", action="store_true", help="Run one paper-trading cycle.")
    parser.add_argument("--daemon", action="store_true", help="Run the 24x7 paper-trading daemon.")
    parser.add_argument("--status", action="store_true", help="Print daemon heartbeat status.")
    parser.add_argument("--stop", action="store_true", help="Send SIGTERM to the daemon pidfile process.")
    parser.add_argument("--reconcile", action="store_true", help="Compare recent journal orders with Alpaca open orders.")
    parser.add_argument("--ingest-evidence", action="store_true", help="Fetch broker paper evidence for the recent window.")
    parser.add_argument("--evidence-report", action="store_true", help="Report broker-backed PnL evidence from journal and latest ingest.")
    parser.add_argument("--verify-profit-target", action="store_true", help="Verify the 10 percent daily paper-profit target using broker evidence only.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional controlled daemon cycle limit for smoke tests.")
    parser.add_argument("--doctor", action="store_true", help="Check the whole system and exit non-zero on any fault.")
    parser.add_argument(
        "--watchdog", action="store_true",
        help="Run the recovery check: start the daemon if it is not running, and report why.",
    )
    parser.add_argument("--skip-broker", action="store_true", help="With --doctor, do not contact the broker.")
    parser.add_argument("--quiet", action="store_true", help="With --doctor, one compact line for logging.")
    args = parser.parse_args(argv)

    config = AgentConfig.from_env()
    if args.doctor:
        return _doctor(config, skip_broker=args.skip_broker, quiet=args.quiet)
    if args.check_env:
        return _check_env(config)
    if args.once:
        return _run_once(config)
    if args.daemon:
        return _run_daemon(config, max_cycles=args.max_cycles)
    if args.status:
        monitor = HealthMonitor(config.heartbeat_path, stale_after_seconds=config.stale_after_seconds)
        print(monitor.status_json())
        return 0 if monitor.is_live() else 1
    if args.stop:
        return _stop_daemon(config)
    if args.reconcile:
        return _reconcile(config)
    if args.ingest_evidence:
        return _ingest_evidence(config)
    if args.evidence_report:
        return _evidence_report(config)
    if args.verify_profit_target:
        return _verify_profit_target(config)
    if args.watchdog:
        return _watchdog(config)

    parser.print_help()
    return 2


SERVICE_NAME = "min-agent.service"


def _watchdog(config: AgentConfig) -> int:
    """Start the daemon if it is not running. Report; never kill what is alive.

    The watchdog unit ran `--doctor` every ten minutes and wrote the result to a log, which
    made it a reporter and nothing more. Combined with `StartLimitBurst=5` in the daemon's
    unit, that meant five crashes inside fifteen minutes left the daemon permanently stopped:
    systemd had given up and the only thing still running was the thing that noticed.

    So the unit's ExecStart is this instead. `reset-failed` clears the start-limit state
    systemd latched after the burst, then `start` retries. Every other action is a report.

    Deliberately conservative about the two ways this could do harm:
      * it never stops or restarts a unit that is active, so a healthy daemon is untouched;
      * a start that immediately fails is recorded as a failure rather than retried in a
        tight loop, and the next attempt is ten minutes away, which is the timer's period.

    Exit 0 whether or not a recovery was needed - the timer's job is to have acted, not to
    signal. A non-zero exit here would make the unit look failed while having succeeded.
    """
    import subprocess

    def systemctl(*words: str) -> tuple[int, str]:
        proc = subprocess.run(
            ["systemctl", "--user", *words], capture_output=True, text=True, timeout=60,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    active_rc, active_out = systemctl("is-active", SERVICE_NAME)
    if active_rc == 0 and active_out.strip() == "active":
        print(f"watchdog: {SERVICE_NAME} is active; nothing to do")
        return 0

    # `failed` or `inactive` or `activating`: systemd has latched a start limit, or the unit
    # was never started. reset-failed is what makes another attempt possible.
    systemctl("reset-failed", SERVICE_NAME)
    start_rc, start_out = systemctl("start", SERVICE_NAME)
    if start_rc != 0:
        print(f"watchdog: could not start {SERVICE_NAME}: {start_out}")
        return 0

    print(f"watchdog: {SERVICE_NAME} was not running ({active_out or 'unknown'}) and has been started")
    verify_rc, verify_out = systemctl("is-active", SERVICE_NAME)
    if verify_rc != 0 or verify_out.strip() != "active":
        print(f"watchdog: start returned success but the unit is {verify_out!r}; it will fail again")
    return 0


def _doctor(config: AgentConfig, *, skip_broker: bool = False, quiet: bool = False) -> int:
    from min_agent.doctor import build_client, record, run_doctor

    report = run_doctor(config, client=build_client(config), skip_broker=skip_broker)
    record(report, config)
    if quiet:
        failures = ",".join(c.name for c in report.failures) or "none"
        warnings = ",".join(c.name for c in report.warnings) or "none"
        print(
            f"ok={not report.failures} exit={report.exit_code} "
            f"failures=[{failures}] warnings=[{warnings}] "
            f"checks={len(report.checks)}"
        )
    else:
        print(report.render())
        print()
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return report.exit_code


def _check_env(config: AgentConfig) -> int:
    result = {
        "mode": config.mode,
        "model": config.model,
        "gpu_devices": config.gpu_devices,
        "alpaca_base_url": config.alpaca_base_url,
        "missing_alpaca_credentials": config.missing_alpaca_credentials(),
        "ollama_tags_accessible": False,
        "ollama_model_present": False,
        "requested_gpus_visible": _requested_gpus_visible(config.gpu_devices),
    }

    try:
        response = requests.get(f"{config.ollama_base_url}/api/tags", timeout=5)
        response.raise_for_status()
        tags = response.json().get("models", [])
        names = {item.get("name") for item in tags}
        result["ollama_tags_accessible"] = True
        result["ollama_model_present"] = config.model in names
    except Exception as exc:
        result["ollama_error"] = str(exc)

    print(json.dumps(result, indent=2, sort_keys=True))
    if result["missing_alpaca_credentials"] or not result["ollama_tags_accessible"] or not result["ollama_model_present"]:
        return 1
    return 0


def _execution_sink(config, client, journal):
    """The one place the execution sink is chosen.

    Shadow trading swaps the final call to the broker and nothing else, so the data,
    the model, the Guardian and the journal above it are the same ones that will
    trade for real. A shadow mode with its own decision path would be grading a
    different system and could pass while the real path failed.

    Anchored on a real function: an earlier attempt to insert this used
    `def _build_loop(` as its anchor, which does not exist in this file, so the two
    call sites were rewritten and the definition never landed. The startup test
    caught it as an unbound name before it could run.
    """
    if config.shadow:
        return ShadowExecutor(journal=journal)
    return AlpacaPaperExecutor(client=client, base_url=config.alpaca_base_url)


def _run_once(config: AgentConfig) -> int:
    missing = config.missing_alpaca_credentials()
    if missing:
        print(json.dumps({"error": "missing Alpaca credentials", "missing": missing}, sort_keys=True))
        return 1

    if not config.is_paper_endpoint():
        print(json.dumps({"error": "Alpaca base URL must be paper trading"}, sort_keys=True))
        return 1

    try:
        import alpaca_trade_api as tradeapi
    except ImportError as exc:
        print(json.dumps({"error": "alpaca-trade-api is not installed", "detail": str(exc)}, sort_keys=True))
        return 1

    client = tradeapi.REST(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
        api_version="v2",
    )
    loop = TradingLoop(
        data_gateway=AlpacaDataGateway(client=client),
        decision_engine=OllamaDecisionEngine(
            base_url=config.ollama_base_url,
            model=config.model,
            gpu_devices=config.gpu_devices,
        ),
        guardian=Guardian(
            allowlist=config.allowlist,
            max_position_value=config.max_position_value,
            max_daily_loss=config.max_daily_loss,
        ),
        executor=_execution_sink(config, client, JsonlJournal(config.journal_path)),
        journal=JsonlJournal(config.journal_path),
        mode=config.mode,
        trade_counter=TradeCounter(journal=JsonlJournal(config.journal_path)),
    )
    record = loop.run_once(config.symbols[0])
    if record is None:
        print(json.dumps({"error": "broker data unavailable; see journal CYCLE_FAILED event"}, sort_keys=True))
        return 1
    print(record.model_dump_json(indent=2))
    return 0


def _run_daemon(config: AgentConfig, *, max_cycles: int | None = None) -> int:
    missing = config.missing_alpaca_credentials()
    if missing:
        print(json.dumps({"error": "missing Alpaca credentials", "missing": missing}, sort_keys=True))
        return 1

    if not config.is_paper_endpoint():
        print(json.dumps({"error": "Alpaca base URL must be paper trading"}, sort_keys=True))
        return 1

    try:
        import alpaca_trade_api as tradeapi
    except ImportError as exc:
        print(json.dumps({"error": "alpaca-trade-api is not installed", "detail": str(exc)}, sort_keys=True))
        return 1

    client = tradeapi.REST(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
        api_version="v2",
    )
    journal = JsonlJournal(config.journal_path)
    strategy_library = StrategyLibrary(config.strategy_dir)
    knowledge_library = KnowledgeLibrary(config.knowledge_dir)
    reflection_memory = ReflectionMemory(config.journal_path.parent / "reflection.json")
    guardian = Guardian(
        allowlist=config.allowlist,
        max_position_value=config.max_position_value,
        max_daily_loss=config.max_daily_loss,
        max_trades_per_day=config.max_trades_per_day,
        max_total_exposure=config.max_total_exposure,
        min_confidence=config.min_confidence,
        max_account_value=config.max_account_value or None,
        max_snapshot_age_seconds=config.stale_after_seconds,
    )
    policy_engine = PolicyEngine(
        strategy_library=strategy_library,
        reflection_memory=reflection_memory,
        knowledge_library=knowledge_library,
    )
    # The LLM proposes; the policy engine is the disclosed fallback. Guardian
    # reviews whatever comes out either way. Previously the daemon injected
    # policy_engine alone, so OllamaDecisionEngine.decide was unreachable
    # outside --once: every trade decision was a static JSON lookup.
    agent_lots = _agent_open_lots(journal)

    def cost_basis() -> dict[str, dict[str, object]]:
        # Frozen at start-up: the lots can only change through a fill, and a fill
        # does not happen inside a decision.
        #
        # Takes no symbol and returns every symbol's lots, because that is the
        # contract `HybridDecisionEngine` declares. It used to take a symbol and
        # return one symbol's lots, so the engine's zero-argument call raised
        # TypeError, the surrounding `except Exception` turned it into an empty
        # mapping, and the model was silently given no cost basis at all - the one
        # thing it needs to tell profit from loss.
        return {symbol: dict(lots) for symbol, lots in agent_lots.items() if lots}

    decision_engine = HybridDecisionEngine(
        llm=OllamaDecisionEngine(
            base_url=config.ollama_base_url,
            model=config.model,
            gpu_devices=config.gpu_devices,
        ),
        policy_engine=policy_engine,
        lessons=policy_engine.relevant_lessons,
        risk_limits={
            "max_position_value": config.max_position_value,
            "max_daily_loss": config.max_daily_loss,
            "max_trades_per_day": config.max_trades_per_day,
            "max_total_exposure": config.max_total_exposure or None,
            "min_confidence": config.min_confidence,
            "allowlist": sorted(config.allowlist),
            "max_account_value": config.max_account_value or None,
        },
        cost_basis=cost_basis,
    )
    loop = TradingLoop(
        data_gateway=AlpacaDataGateway(client=client),
        decision_engine=decision_engine,
        guardian=guardian,
        executor=_execution_sink(config, client, journal),
        journal=journal,
        mode=config.mode,
        trade_counter=TradeCounter(journal=journal),
    )
    daemon = AgentDaemon(
        config=config,
        loop=loop,
        journal=journal,
        strategy_library=strategy_library,
        reflection_memory=reflection_memory,
        curriculum_agent=(
            StructuredCurriculumAgent(
                transport=_ollama_curriculum_transport(config),
                state_path=config.journal_path.parent / "curriculum_state.json",
            )
            if config.curriculum_enabled
            else None
        ),
        strategy_admission=StrategyAdmission(guardian=guardian, strategy_library=strategy_library),
        broker_evidence_provider=BrokerEvidenceProvider(client=client),
        lifecycle_manager=StrategyLifecycleManager(),
        knowledge_admission=KnowledgeAdmission(knowledge_library=knowledge_library),
        startup_reconciler=OrderReconciler(client=client, journal=journal),
        fill_reconciler=FillReconciler(client=client, journal=journal),
        scheduler=MarketScheduler(
            clock_provider=client.get_clock,
            stale_after_seconds=config.stale_after_seconds,
        ),
        reflect_every=config.reflect_every,
        curriculum_every=config.curriculum_every,
    )
    return daemon.run(max_cycles=max_cycles)


def _ollama_curriculum_transport(config: AgentConfig):
    engine = OllamaDecisionEngine(
        base_url=config.ollama_base_url,
        model=config.model,
        gpu_devices=config.gpu_devices,
        # Not the engine default of 120s. Measured on the real curriculum prompt
        # the model takes 118.4s at num_ctx=6144 and 141.9s at 8192, so a 120s
        # timeout sat *inside* the working range: raising the window alone would
        # have converted a truncation failure into a ReadTimeout. The curriculum
        # runs off-hours, so a two-minute correct answer beats a fast wrong one.
        timeout=CURRICULUM_TIMEOUT_S,
    )

    def call(prompt: str, schema: dict) -> str:
        payload = {
            "model": config.model,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.1, "num_ctx": CURRICULUM_NUM_CTX},
        }
        response = engine.transport(f"{engine.base_url}/api/generate", payload, engine.timeout)
        return str(response.get("response", ""))

    def transport(context):
        return generate_curriculum_task_json(call=call, prompt_context=context)

    return transport


def _stop_daemon(config: AgentConfig) -> int:
    pidfile = config.pidfile_path
    if not pidfile.exists():
        print(json.dumps({"error": "no pidfile found", "path": str(pidfile)}, sort_keys=True))
        return 1
    raw_pid = pidfile.read_text(encoding="utf-8").strip()
    if not raw_pid:
        pidfile.unlink()
        print(json.dumps({"cleaned_stale_pidfile": str(pidfile), "reason": "empty pidfile"}, sort_keys=True))
        return 0
    try:
        pid = int(raw_pid)
    except ValueError:
        pidfile.unlink()
        print(json.dumps({"cleaned_stale_pidfile": str(pidfile), "reason": "invalid pid"}, sort_keys=True))
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        if pidfile.exists() and pidfile.read_text(encoding="utf-8").strip() == raw_pid:
            pidfile.unlink()
        print(json.dumps({"already_stopped": pid, "cleaned_stale_pidfile": str(pidfile)}, sort_keys=True))
        return 0
    except PermissionError:
        print(json.dumps({"error": "permission denied", "pid": pid}, sort_keys=True))
        return 1

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            if pidfile.exists() and pidfile.read_text(encoding="utf-8").strip() == raw_pid:
                pidfile.unlink()
            print(json.dumps({"stopped": pid}, sort_keys=True))
            return 0
        time.sleep(0.1)

    print(json.dumps({"signal_sent": pid, "warning": "process still appears to be running"}, sort_keys=True))
    return 1


def _reconcile(config: AgentConfig) -> int:
    missing = config.missing_alpaca_credentials()
    if missing:
        print(json.dumps({"error": "missing Alpaca credentials", "missing": missing}, sort_keys=True))
        return 1

    try:
        import alpaca_trade_api as tradeapi
    except ImportError as exc:
        print(json.dumps({"error": "alpaca-trade-api is not installed", "detail": str(exc)}, sort_keys=True))
        return 1

    client = tradeapi.REST(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
        api_version="v2",
    )
    journal = JsonlJournal(config.journal_path)
    reconciler = OrderReconciler(client=client, journal=journal)
    report = reconciler.reconcile()
    print(json.dumps({
        "matched": report.matched,
        "submitted_order_ids": list(report.submitted_order_ids),
        "broker_open_order_ids": list(report.broker_open_order_ids),
        "extra_broker_orders": list(report.extra_broker_orders),
        "message": report.message,
    }, indent=2, sort_keys=True))
    return 0 if report.matched else 1


def _ingest_evidence(config: AgentConfig) -> int:
    client = _paper_client(config)
    if client is None:
        return 1
    now = datetime.now(tz=timezone.utc)
    batch = BrokerEvidenceProvider(client=client).ingest(window_start=now - timedelta(days=30), window_end=now)
    journal = JsonlJournal(config.journal_path)
    journal.append_event(
        JournalEvent(
            event_id=str(uuid4()),
            event_type="BROKER_EVIDENCE_INGESTED",
            timestamp=now,
            status="SUCCESS" if batch.status != "FAILED" else "FAILED",
            message=batch.status.lower(),
            payload=batch.model_dump(mode="json"),
        )
    )
    print(batch.model_dump_json(indent=2))
    return 0 if batch.status != "FAILED" else 1


def _agent_open_lots(journal: JsonlJournal) -> dict[str, dict[str, object]]:
    """Open lots the agent still holds, by symbol, from broker-confirmed fills.

    The decision context previously carried market_value but no cost, so the model
    could not tell whether the position it held was in profit. Every price here is
    one the broker reported on a fill the reconciler re-polled to a terminal state.

    Sells consume buys. They did not: every activity was appended and then summed,
    so `quantity` was buys + sells and **selling increased the reported position**.
    On this journal that reported 116 SPY shares against a broker holding of 17, in
    the same context that also carries `positions` saying 17 and `sellable_quantity`
    saying 17. The model was handed three numbers for one fact and two of them
    contradicted the broker; `average_cost` and `unrealized_pnl` were then computed
    over that invented average, so the one number that would have told the model
    whether it was up or down was the one number it could not use.

    When sells outrun the buys this journal can see, the shortfall was liquidated
    from shares acquired before it - 29 SPY belonging to the account owner, which
    `_agent_holding` documents. Nothing here can price those, so `average_price` is
    omitted rather than averaged over buys and sells together: the consumer already
    skips a lot without one, and a missing average is honest where a fabricated one
    is not. `sellable_quantity` remains the authority for how many shares can
    actually be sold.
    """
    records = journal.read_all()
    by_symbol: dict[str, list] = {}
    for activity in confirmed_fill_activities(journal, records):
        by_symbol.setdefault(activity.symbol, []).append(activity)

    out: dict[str, dict[str, object]] = {}
    for symbol, activities in by_symbol.items():
        activities.sort(key=lambda a: a.transaction_time)
        # FIFO: the oldest buys are the ones a sell liquidates, so the average is
        # over the shares that survived rather than over everything ever traded.
        lots: list[list[float]] = []
        unpriced_sold = 0.0
        for activity in activities:
            if activity.side == "BUY":
                lots.append([activity.quantity, activity.price])
                continue
            remaining = activity.quantity
            while remaining > 0 and lots:
                take = min(remaining, lots[0][0])
                lots[0][0] -= take
                remaining -= take
                if lots[0][0] <= 0:
                    lots.pop(0)
            if remaining > 0:
                # Liquidated shares this journal never saw bought.
                unpriced_sold += remaining

        total = sum(quantity for quantity, _ in lots)
        if total <= 0:
            continue
        lot: dict[str, object] = {
            "quantity": total,
            "fill_count": len(activities),
            "source": "broker_confirmed_fills",
        }
        if unpriced_sold:
            lot["cost_basis_incomplete"] = True
            lot["unpriced_sold_quantity"] = round(unpriced_sold, 4)
        else:
            lot["average_price"] = round(
                sum(quantity * price for quantity, price in lots) / total, 4
            )
        out[symbol] = lot
    return out


def _evidence_report(config: AgentConfig) -> int:
    journal = JsonlJournal(config.journal_path)
    batch = _latest_evidence_batch(journal)
    report = DeterministicEvaluator().evaluate(
        journal.read_all(), evidence=batch, seeded_fills=confirmed_fill_activities(journal, journal.read_all())
    )
    event_status: JournalEventStatus = (
        "SUCCESS" if report.pnl_evidence != PNL_EVIDENCE_MISSING else "SKIPPED"
    )
    journal.append_event(
        JournalEvent(
            event_id=str(uuid4()),
            event_type=(
                "PNL_EVIDENCE_RECORDED" if event_status == "SUCCESS" else "PNL_EVIDENCE_FAILED"
            ),
            timestamp=datetime.now(tz=timezone.utc),
            status=event_status,
            message=report.pnl_evidence,
            payload=report.model_dump(mode="json"),
        )
    )
    print(report.model_dump_json(indent=2))
    return 0


def _verify_profit_target(config: AgentConfig) -> int:
    journal = JsonlJournal(config.journal_path)
    batch = _latest_evidence_batch(journal)
    report = DeterministicEvaluator().evaluate(
        journal.read_all(), evidence=batch, seeded_fills=confirmed_fill_activities(journal)
    )
    pnl = report.pnl
    result = {
        "target_daily_return_pct": 0.10,
        "pnl_evidence": report.pnl_evidence,
        "satisfied": False,
        "status": "unproven",
        "account_return_pct": None if pnl is None else pnl.account_return_pct,
        "paper_only": config.mode == "paper",
    }
    if pnl is None or report.pnl_evidence not in {PNL_EVIDENCE_ACCOUNT_VERIFIED, PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED}:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    if pnl.account_return_pct is None:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    if pnl.account_return_pct >= 0.10:
        result["satisfied"] = True
        result["status"] = "satisfied"
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result["status"] = "not satisfied"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1


def _latest_evidence_batch(journal: JsonlJournal) -> BrokerEvidenceBatch | None:
    return latest_evidence_batch(journal)


def _paper_client(config: AgentConfig):
    missing = config.missing_alpaca_credentials()
    if missing:
        print(json.dumps({"error": "missing Alpaca credentials", "missing": missing}, sort_keys=True))
        return None
    if not config.is_paper_endpoint():
        print(json.dumps({"error": "Alpaca base URL must be paper trading"}, sort_keys=True))
        return None
    try:
        import alpaca_trade_api as tradeapi
    except ImportError as exc:
        print(json.dumps({"error": "alpaca-trade-api is not installed", "detail": str(exc)}, sort_keys=True))
        return None
    return tradeapi.REST(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
        api_version="v2",
    )


def _requested_gpus_visible(gpu_devices: str) -> bool:
    requested = {item.strip() for item in gpu_devices.split(",") if item.strip()}
    if not requested:
        return False
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
    indexes = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return requested.issubset(indexes)


if __name__ == "__main__":
    raise SystemExit(main())
