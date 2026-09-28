from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

import requests

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from min_agent.broker_evidence import BrokerEvidenceProvider
from min_agent.config import AgentConfig
from min_agent.daemon import AgentDaemon
from min_agent.data_gateway import AlpacaDataGateway
from min_agent.executor import AlpacaPaperExecutor
from min_agent.guardian import Guardian
from min_agent.health import HealthMonitor
from min_agent.evaluator import DeterministicEvaluator, PNL_EVIDENCE_ACCOUNT_VERIFIED, PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
from min_agent.journal import JsonlJournal
from min_agent.knowledge_admission import KnowledgeAdmission
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.llm_decision import HybridDecisionEngine, OllamaDecisionEngine
from min_agent.loop import TradingLoop
from min_agent.models import CurriculumTask, BrokerEvidenceBatch, JournalEvent
from min_agent.fill_reconciler import FillReconciler
from min_agent.order_reconciler import OrderReconciler
from min_agent.curriculum import StructuredCurriculumAgent
from min_agent.policy_engine import PolicyEngine
from min_agent.reflection_memory import ReflectionMemory
from min_agent.scheduler import MarketScheduler
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
    parser.add_argument("--verify-profit-target", action="store_true", help="Verify the 10% daily paper-profit target using broker evidence only.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional controlled daemon cycle limit for smoke tests.")
    parser.add_argument("--doctor", action="store_true", help="Check the whole system and exit non-zero on any fault.")
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

    parser.print_help()
    return 2


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


def _run_once(config: AgentConfig) -> int:
    missing = config.missing_alpaca_credentials()
    if missing:
        print(json.dumps({"error": "missing Alpaca credentials", "missing": missing}, sort_keys=True))
        return 1

    if "paper" not in config.alpaca_base_url:
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
        executor=AlpacaPaperExecutor(client=client, base_url=config.alpaca_base_url),
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

    if "paper" not in config.alpaca_base_url:
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
    decision_engine = HybridDecisionEngine(
        llm=OllamaDecisionEngine(
            base_url=config.ollama_base_url,
            model=config.model,
            gpu_devices=config.gpu_devices,
        ),
        policy_engine=policy_engine,
        lessons=policy_engine.relevant_lessons,
    )
    loop = TradingLoop(
        data_gateway=AlpacaDataGateway(client=client),
        decision_engine=decision_engine,
        guardian=guardian,
        executor=AlpacaPaperExecutor(client=client, base_url=config.alpaca_base_url),
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
        scheduler=MarketScheduler(clock_provider=client.get_clock),
        reflect_every=config.reflect_every,
        curriculum_every=config.curriculum_every,
    )
    return daemon.run(max_cycles=max_cycles)


def _ollama_curriculum_transport(config: AgentConfig):
    engine = OllamaDecisionEngine(
        base_url=config.ollama_base_url,
        model=config.model,
        gpu_devices=config.gpu_devices,
    )
    model = config.model

    def transport(context):
        # Every number the model can copy must come from the real context. This
        # prompt previously hard-coded a created_at of 2026-06-10 and worked
        # example TREND_FOLLOW parameters anchored to a 100-dollar reference
        # price; the model copied the example and nine admitted strategies were
        # created anchored at {100,120,150,170,180,200} against a ~$744 SPY, so
        # every one of them was permanently degenerate. See the prompt-source
        # test in tests/min_agent/test_llm_provenance.py.
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prompt = (
            "You are the autonomous curriculum agent for a paper-only Quant-Voyager. "
            "Return exactly one JSON object and no prose. The object must match this shape: "
            "{\"task_id\":\"<a unique id you choose>\",\"task_type\":\"STRATEGY_SPEC\",\"summary\":\"...\","
            "\"strategy_spec\":{\"strategy_id\":\"<a unique id you choose>\",\"name\":\"...\","
            "\"kind\":\"HOLD_BASELINE|FIXED_SIZE|TREND_FOLLOW\","
            "\"symbols\":[\"<a symbol from the context>\"],\"parameters\":{},"
            "\"max_position_value\":<a number at or below the context risk limit>,"
            "\"enabled\":true,\"created_at\":\"" + today + "\",\"rationale\":\"...\"},"
            "\"parameters\":{},\"rationale\":\"...\"}. For EVALUATE tasks set strategy_spec to null. "
            "Allowed strategy parameters are strict: HOLD_BASELINE uses {}, FIXED_SIZE requires exactly "
            "{\"action\":\"BUY|SELL|HOLD\",\"quantity\":integer,\"confidence\":0.0_to_1.0}, and TREND_FOLLOW requires exactly "
            "{\"reference_price\":positive_number,\"threshold_pct\":0.0_to_0.20,\"quantity\":positive_integer,\"confidence\":0.0_to_1.0}. "
            "reference_price MUST be taken from the current market price in the context for that symbol. "
            "Do not reuse a number from this instruction as a price. "
            "max_position_value MUST be at or below risk_limits.max_position_value in the context. "
            "Start small: for FIXED_SIZE use quantity 1 and confidence at or near 0.6. "
            "Unknown parameters such as fixed_size, holding_period, target_daily_return_pct, lookback, code, script, or shell will be rejected. "
            "Follow progression: first HOLD_BASELINE, then tiny FIXED_SIZE, then only after evidence/evaluation TREND_FOLLOW. "
            "Your output must be a complete object. Every field listed as required in schema and "
            "strategy_spec_fields must be present, including the top-level summary and rationale. "
            "The context contains a worked_example: it shows the SHAPE only. Replace every placeholder value in it "
            "with values derived from the context - in particular task_id, strategy_id and created_at must be freshly "
            "generated by you and must not duplicate any strategy_id already listed in the context. "
            "Do not propose Python code, shell commands, live trading, web-search claims, or changes to Guardian hard limits. Context: "
            f"{json.dumps(context, sort_keys=True)}"
        )
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            # Schema-constrained generation, not format="json". With the plain
            # "json" mode the model repeatedly omitted the top-level summary and
            # rationale, so every curriculum call failed validation and fell back
            # to a hard-coded EVALUATE - the self-evolution loop was dead while
            # reporting healthy. Constraining to the pydantic schema makes the
            # shape exact, and measured against deepseek-r1:8b it was also 5x
            # faster (11.7s vs 54.4s) and produced 4x less output, because the
            # model no longer has to search for a valid shape.
            "format": CurriculumTask.model_json_schema(),
            "options": {"temperature": 0.1},
        }
        response = engine.transport(f"{engine.base_url}/api/generate", payload, engine.timeout)
        return str(response.get("response", ""))

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


def _evidence_report(config: AgentConfig) -> int:
    journal = JsonlJournal(config.journal_path)
    batch = _latest_evidence_batch(journal)
    report = DeterministicEvaluator().evaluate(journal.read_all(), evidence=batch)
    event_status = "SUCCESS" if report.pnl_evidence != "missing_fill_price_and_broker_activity" else "SKIPPED"
    journal.append_event(
        JournalEvent(
            event_id=str(uuid4()),
            event_type="PNL_EVIDENCE_RECORDED" if event_status == "SUCCESS" else "PNL_EVIDENCE_FAILED",
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
    report = DeterministicEvaluator().evaluate(journal.read_all(), evidence=batch)
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
    events = journal.read_events("BROKER_EVIDENCE_INGESTED")
    if not events:
        return None
    return BrokerEvidenceBatch.model_validate(events[-1].payload)


def _paper_client(config: AgentConfig):
    missing = config.missing_alpaca_credentials()
    if missing:
        print(json.dumps({"error": "missing Alpaca credentials", "missing": missing}, sort_keys=True))
        return None
    if "paper" not in config.alpaca_base_url:
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
