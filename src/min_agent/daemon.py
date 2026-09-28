from __future__ import annotations

import hashlib
import os
import signal
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from min_agent.atomicio import file_lock, write_text_atomic
from min_agent.config import AgentConfig
from min_agent.curriculum import StructuredCurriculumAgent
from min_agent.evaluator import DeterministicEvaluator, PNL_EVIDENCE_ACCOUNT_VERIFIED, PNL_EVIDENCE_MISSING, PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED
from min_agent.fill_reconciler import FILL_EVENT, FillReconciler
from min_agent.health import HealthMonitor
from min_agent.journal import JsonlJournal
from min_agent.knowledge_admission import KnowledgeAdmission
from min_agent.models import BrokerEvidenceBatch, HeartbeatPayload, JournalEvent, KnowledgeArtifact
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_admission import StrategyAdmission
from min_agent.strategy_engine import StrategyLibrary, StrategyLifecycleManager


class DaemonAlreadyRunningError(RuntimeError):
    pass


class AgentDaemon:
    def __init__(
        self,
        *,
        config: AgentConfig,
        loop,
        health: HealthMonitor | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        scheduler=None,
        startup_reconciler=None,
        fill_reconciler: FillReconciler | None = None,
        strategy_library: StrategyLibrary | None = None,
        reflection_memory: ReflectionMemory | None = None,
        curriculum_agent: StructuredCurriculumAgent | None = None,
        strategy_admission: StrategyAdmission | None = None,
        journal: JsonlJournal | None = None,
        broker_evidence_provider=None,
        lifecycle_manager: StrategyLifecycleManager | None = None,
        knowledge_admission: KnowledgeAdmission | None = None,
        reflect_every: int = 10,
        curriculum_every: int = 50,
    ):
        self.config = config
        self.loop = loop
        self.health = health or HealthMonitor(config.heartbeat_path)
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(tz=timezone.utc))
        self.scheduler = scheduler
        self.startup_reconciler = startup_reconciler
        self.fill_reconciler = fill_reconciler
        self.strategy_library = strategy_library
        self.reflection_memory = reflection_memory
        self.curriculum_agent = curriculum_agent
        self.strategy_admission = strategy_admission
        self.journal = journal
        self.broker_evidence_provider = broker_evidence_provider
        self.lifecycle_manager = lifecycle_manager
        self.knowledge_admission = knowledge_admission
        self.reflect_every = reflect_every
        self.curriculum_every = curriculum_every
        self._stop_requested = False
        self.cycle_count = 0
        self.daily_cycle_count = 0
        self.current_cycle_date: date | None = None
        self.error_count = 0
        self.last_cycle_id: str | None = None
        self.last_maintenance_at: datetime | None = None
        self.last_evidence_at: datetime | None = None
        self.last_reflection_at: datetime | None = None
        self.last_curriculum_at: datetime | None = None
        # Profit target state — only emit PROFIT_TARGET_CHECKED on transitions
        # to avoid flooding the journal with identical "not satisfied" events.
        self._last_profit_target_status: str | None = None
        # Window stability — skip reflection/curriculum writes when the journal
        # window hasn't changed since last run (e.g. weekend maintenance).
        self._last_reflection_window_hash: str | None = None
        self._last_curriculum_window_hash: str | None = None

    def run(self, *, max_cycles: int | None = None) -> int:
        self._acquire_lock()
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        try:
            self._heartbeat("RUNNING", "daemon started")
            if not self._startup_reconcile():
                return 1
            while not self._stop_requested:
                self._reset_daily_count_if_needed()
                self._maintenance()
                if max_cycles is not None and self.cycle_count >= max_cycles:
                    break
                if self.scheduler is not None and not self.scheduler.should_trade_now():
                    self._heartbeat("RUNNING", "market closed; maintenance only")
                    if max_cycles is not None:
                        break
                    self.sleep(self._sleep_seconds())
                    continue
                if self.daily_cycle_count >= self.config.max_daily_cycles:
                    self._heartbeat("BACKING_OFF", "max daily cycles reached")
                    self.sleep(self._sleep_seconds())
                    continue
                for symbol in self.config.symbols:
                    if self._stop_requested:
                        break
                    self._run_symbol(symbol)
                    if max_cycles is not None and self.cycle_count >= max_cycles:
                        break
                    if self.daily_cycle_count >= self.config.max_daily_cycles:
                        break
                if not self._stop_requested:
                    self.sleep(self._sleep_seconds())
            self._heartbeat("STOPPED", "daemon stopped")
            return 0
        finally:
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)
            self._release_lock()

    def stop(self) -> None:
        self._stop_requested = True

    def _run_symbol(self, symbol: str) -> None:
        try:
            record = self.loop.run_once(symbol)
            self.cycle_count += 1
            self.daily_cycle_count += 1
            if record is None:
                # run_once already journaled CYCLE_FAILED; there is no snapshot
                # to report on, so do not invent a last_cycle_id.
                self.error_count += 1
                self._heartbeat("BACKING_OFF", f"broker data unavailable for {symbol}")
                return
            self.last_cycle_id = record.cycle_id
            self._post_cycle()
            self._heartbeat("RUNNING", f"completed cycle for {symbol}")
        except Exception as exc:
            self.cycle_count += 1
            self.daily_cycle_count += 1
            self.error_count += 1
            self._heartbeat("BACKING_OFF", f"cycle error for {symbol}: {exc}")

    def _request_stop(self, signum, frame) -> None:  # pragma: no cover - signal wiring is integration behavior
        self._stop_requested = True
        self._heartbeat("STOPPING", f"received signal {signum}")

    def _startup_reconcile(self) -> bool:
        if self.startup_reconciler is None:
            return True
        report = self.startup_reconciler.reconcile()
        status = "SUCCESS" if report.matched else "FAILED"
        payload = {
            "matched": report.matched,
            "submitted_order_ids": list(report.submitted_order_ids),
            "broker_open_order_ids": list(report.broker_open_order_ids),
            "extra_broker_orders": list(report.extra_broker_orders),
            "message": report.message,
        }
        self._append_event(
            "ORDER_RECONCILIATION_REVIEWED",
            status=status,
            message=report.message,
            payload=payload,
        )
        if report.matched:
            return True
        self._heartbeat("ERROR", f"startup reconciliation failed: {report.message}")
        return False

    def _reset_daily_count_if_needed(self) -> None:
        today = self.now().date()
        if self.current_cycle_date is None:
            self.current_cycle_date = today
            return
        if today != self.current_cycle_date:
            self.current_cycle_date = today
            self.daily_cycle_count = 0

    def _post_cycle(self) -> None:
        if self.journal is None or self.reflection_memory is None:
            return
        if self.cycle_count % self.reflect_every == 0:
            self._reflect(evidence=self._latest_evidence_batch())
            self._manage_strategy_lifecycle()
        if self.cycle_count % self.curriculum_every == 0 and self.curriculum_agent is not None:
            self._curriculum_proposal()

    def _reflect(self, *, evidence: BrokerEvidenceBatch | None = None) -> None:
        try:
            recent = self.journal.last_n(self.config.reflection_window)
            reflection = self.reflection_memory.reflect(recent, evidence=evidence, fills=self._confirmed_fills())
            self.reflection_memory.save(reflection)
            self._append_event(
                "REFLECTION_GENERATED",
                status="SUCCESS",
                message=reflection.summary,
                payload={
                    "window_cycles": reflection.window_cycles,
                    "submitted_orders": reflection.submitted_orders,
                    "rejected_orders": reflection.rejected_orders,
                    "error_count": reflection.error_count,
                    "strategy_scores": reflection.strategy_scores,
                },
            )
            self._append_event(
                "STRATEGY_EVALUATION_RECORDED",
                status="SUCCESS",
                message="deterministic evaluator recorded factual journal metrics",
                payload=reflection.evaluation,
            )
            self.last_reflection_at = self.now()
        except Exception as exc:
            self._append_event(
                "REFLECTION_FAILED",
                status="FAILED",
                message=str(exc),
                payload={"error_type": type(exc).__name__},
            )

    def _curriculum_proposal(self) -> None:
        try:
            reflection = self.reflection_memory.load()
            if reflection is None:
                return
            strategies = self.strategy_library.list() if self.strategy_library else []
            exploration_summary = self._recent_exploration_summary()
            self._record_no_exploration_lesson(exploration_summary)
            # Retry once on transport/connection errors — the LLM endpoint
            # (Ollama) can transiently time out under load. A single retry with
            # a 5-second backstop is sufficient; anything worse should surface
            # as a curriculum failure event for operator attention.
            try:
                task = self.curriculum_agent.propose(
                    reflection=reflection,
                    current_strategies=strategies,
                    recent_exploration_summary=exploration_summary,
                    guardian_max_position_value=self.config.max_position_value,
                )
            except (ConnectionError, TimeoutError, OSError):
                self.sleep(5)
                task = self.curriculum_agent.propose(
                    reflection=reflection,
                    current_strategies=strategies,
                    recent_exploration_summary=exploration_summary,
                    guardian_max_position_value=self.config.max_position_value,
                )
            strategy_id = task.strategy_spec.strategy_id if task.strategy_spec is not None else None
            self._append_event(
                "CURRICULUM_PROPOSED",
                status="SUCCESS",
                message=task.summary,
                strategy_id=strategy_id,
                task_id=task.task_id,
                payload={
                    "task_type": task.task_type,
                    "rationale": task.rationale,
                    "parameters": task.parameters,
                    "strategy_id": strategy_id,
                    "recent_exploration_summary": exploration_summary,
                },
            )
            if task.task_type == "STRATEGY_SPEC" and task.strategy_spec and self.strategy_admission is not None:
                result = self.strategy_admission.admit(task.strategy_spec)
                self._append_event(
                    "STRATEGY_ADMISSION_REVIEWED",
                    status="ACCEPTED" if result.accepted else "REJECTED",
                    message=result.reason,
                    strategy_id=result.strategy_id,
                    task_id=task.task_id,
                    payload={"accepted": result.accepted, "reason": result.reason},
                )
            self.last_curriculum_at = self.now()
        except Exception as exc:
            self._append_event(
                "CURRICULUM_FAILED",
                status="FAILED",
                message=str(exc),
                payload={"error_type": type(exc).__name__},
            )

    def _maintenance(self) -> None:
        if not self._due(self.last_maintenance_at, self.config.maintenance_interval_seconds):
            return
        self._resolve_pending_fills()
        evidence = self._latest_evidence_batch()
        if self._due(self.last_evidence_at, self.config.evidence_interval_seconds):
            evidence = self._ingest_broker_evidence()
        if self.journal is not None:
            self._record_pnl_evidence(evidence)
            self._verify_profit_target(evidence)
        if self.reflection_memory is not None and self._due(self.last_reflection_at, self.config.reflection_interval_seconds):
            self._reflect(evidence=evidence)
        if self.curriculum_agent is not None and self.reflection_memory is not None and self._due(self.last_curriculum_at, self.config.curriculum_interval_seconds):
            self._curriculum_proposal()
        self._manage_strategy_lifecycle()
        self.last_maintenance_at = self.now()

    def _resolve_pending_fills(self) -> int:
        """Poll the broker for fills of orders this agent submitted.

        A market order's submit response reports filled_qty 0, and nothing used
        to re-poll, so filled orders were recorded as filled_quantity 0.0. Every
        downstream number - fill_quantity_ratio, per-strategy realized PnL, and
        therefore the score the selector optimises - was computed against fills
        that were known to have happened but were invisible.
        """
        if self.fill_reconciler is None or self.journal is None:
            return 0
        try:
            confirmations = self.fill_reconciler.resolve()
        except Exception as exc:
            self._append_event(
                FILL_EVENT,
                status="FAILED",
                message=f"fill reconciliation failed: {exc}",
                payload={"error_type": type(exc).__name__, "error": str(exc)},
            )
            return 0
        for confirmation in confirmations:
            self._append_event(
                FILL_EVENT,
                status="SUCCESS",
                message=(
                    f"{confirmation.pending.symbol} {confirmation.pending.side} "
                    f"{confirmation.filled_quantity} @ {confirmation.filled_avg_price} "
                    f"({confirmation.broker_status})"
                ),
                strategy_id=confirmation.pending.strategy_id,
                payload=confirmation.payload(),
            )
        return len(confirmations)

    def _confirmed_fills(self) -> dict[str, float]:
        if self.journal is None:
            return {}
        fills: dict[str, float] = {}
        for event in self.journal.read_events(FILL_EVENT):
            coid = event.payload.get("client_order_id")
            qty = event.payload.get("filled_quantity")
            if isinstance(coid, str) and isinstance(qty, (int, float)) and qty > 0:
                fills[coid] = max(fills.get(coid, 0.0), float(qty))
        return fills

    def _ingest_broker_evidence(self) -> BrokerEvidenceBatch | None:
        if self.broker_evidence_provider is None:
            return None
        window_end = self.now()
        window_start = window_end - timedelta(days=1)
        try:
            batch = self.broker_evidence_provider.ingest(window_start=window_start, window_end=window_end)
            self.last_evidence_at = window_end
            self._append_event(
                "BROKER_EVIDENCE_INGESTED",
                status="SUCCESS" if batch.status != "FAILED" else "FAILED",
                message=batch.status.lower(),
                payload=batch.model_dump(mode="json"),
            )
            return batch
        except Exception as exc:
            self._append_event(
                "BROKER_EVIDENCE_INGESTED",
                status="FAILED",
                message=str(exc),
                payload={"error_type": type(exc).__name__},
            )
            return None

    def _record_pnl_evidence(self, evidence: BrokerEvidenceBatch | None) -> None:
        report = DeterministicEvaluator().evaluate(
            self.journal.read_all(), evidence=evidence, fills=self._confirmed_fills()
        )
        success = report.pnl_evidence != PNL_EVIDENCE_MISSING
        self._append_event(
            "PNL_EVIDENCE_RECORDED" if success else "PNL_EVIDENCE_FAILED",
            status="SUCCESS" if success else "SKIPPED",
            message=report.pnl_evidence,
            payload=report.model_dump(mode="json"),
        )

    def _verify_profit_target(self, evidence: BrokerEvidenceBatch | None) -> None:
        report = DeterministicEvaluator().evaluate(
            self.journal.read_all(), evidence=evidence, fills=self._confirmed_fills()
        )
        pnl = report.pnl
        status = "unproven"
        satisfied = False
        if pnl is not None and report.pnl_evidence in {PNL_EVIDENCE_ACCOUNT_VERIFIED, PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED} and pnl.account_return_pct is not None:
            if pnl.account_return_pct >= self.config.profit_target_return_pct:
                status = "satisfied"
                satisfied = True
            else:
                status = "not satisfied"
        # Only write a journal event when the target status transitions, so a
        # routine "not satisfied" reading does not flood the log.
        if status == self._last_profit_target_status:
            return
        self._last_profit_target_status = status
        self._append_event(
            "PROFIT_TARGET_CHECKED",
            status="SUCCESS" if satisfied else "SKIPPED",
            message=f"profit target {status}",
            payload={
                "target_daily_return_pct": self.config.profit_target_return_pct,
                "pnl_evidence": report.pnl_evidence,
                "satisfied": satisfied,
                "status": status,
                "account_return_pct": None if pnl is None else pnl.account_return_pct,
                "paper_only": self.config.mode == "paper",
            },
        )

    def _latest_evidence_batch(self) -> BrokerEvidenceBatch | None:
        if self.journal is None:
            return None
        events = self.journal.read_events("BROKER_EVIDENCE_INGESTED")
        if not events:
            return None
        try:
            return BrokerEvidenceBatch.model_validate(events[-1].payload)
        except Exception:
            return None

    def _recent_exploration_summary(self) -> dict[str, object]:
        if self.journal is None:
            return {
                "window_cycles": 0,
                "market_open_cycles": 0,
                "strategy_ids": [],
                "action_counts": {},
                "submitted_orders": 0,
                "rejected_orders": 0,
                "skipped_orders": 0,
                "buy_sell_intent_count": 0,
                "submitted_or_rejected_order_count": 0,
                "intended_notional": 0.0,
                "filled_quantity": 0.0,
                "data_sources": {},
                "latest_prices_by_symbol": {},
                "cycle_ids": [],
                "no_exploration": False,
                "paper_only": self.config.mode == "paper",
            }
        records = self.journal.last_n(self.config.reflection_window)
        action_counts: dict[str, int] = {}
        data_sources: dict[str, int] = {}
        latest_prices: dict[str, float] = {}
        strategy_ids: set[str] = set()
        cycle_ids: list[str] = []
        submitted_orders = 0
        rejected_orders = 0
        skipped_orders = 0
        buy_sell_intent_count = 0
        intended_notional = 0.0
        filled_quantity = 0.0
        market_open_cycles = 0
        for record in records:
            cycle_ids.append(record.cycle_id)
            if record.strategy_id or record.decision.strategy_id:
                strategy_ids.add(record.strategy_id or record.decision.strategy_id or "")
            action = record.decision.action
            action_counts[action] = action_counts.get(action, 0) + 1
            data_sources[record.snapshot.source] = data_sources.get(record.snapshot.source, 0) + 1
            latest_prices[record.snapshot.symbol] = record.snapshot.last_price
            if record.snapshot.market_open:
                market_open_cycles += 1
            if action in {"BUY", "SELL"}:
                buy_sell_intent_count += 1
            notional = record.decision.quantity * record.snapshot.last_price
            intended_notional += notional
            filled_quantity += record.execution.filled_quantity
            if record.execution.status == "SUBMITTED":
                submitted_orders += 1
            elif record.execution.status == "REJECTED":
                rejected_orders += 1
            elif record.execution.status == "SKIPPED":
                skipped_orders += 1
        submitted_or_rejected = submitted_orders + rejected_orders
        no_exploration = (
            bool(records)
            and market_open_cycles > 0
            and buy_sell_intent_count == 0
            and submitted_or_rejected == 0
            and intended_notional == 0
        )
        return {
            "window_cycles": len(records),
            "market_open_cycles": market_open_cycles,
            "strategy_ids": sorted(strategy_id for strategy_id in strategy_ids if strategy_id),
            "action_counts": action_counts,
            "submitted_orders": submitted_orders,
            "rejected_orders": rejected_orders,
            "skipped_orders": skipped_orders,
            "buy_sell_intent_count": buy_sell_intent_count,
            "submitted_or_rejected_order_count": submitted_or_rejected,
            "intended_notional": intended_notional,
            "filled_quantity": filled_quantity,
            "data_sources": data_sources,
            "latest_prices_by_symbol": latest_prices,
            "cycle_ids": cycle_ids,
            "no_exploration": no_exploration,
            "paper_only": self.config.mode == "paper",
        }

    def _record_no_exploration_lesson(self, summary: dict[str, object]) -> None:
        if self.knowledge_admission is None or not summary.get("no_exploration"):
            return
        cycle_ids = tuple(str(cycle_id) for cycle_id in summary.get("cycle_ids", []) if str(cycle_id))
        if not cycle_ids:
            return
        subtype, answer = self._diagnose_no_exploration(summary)
        # Content-hash-based artifact_id keeps the same lesson stable across runs
        # and lets KnowledgeAdmission's content-hash dedup do its job.
        digest = hashlib.sha1(f"{subtype}|{answer}".encode("utf-8")).hexdigest()[:12]
        artifact_id = f"lesson-no-exploration-{subtype}-{digest}"
        artifact = KnowledgeArtifact(
            artifact_id=artifact_id,
            artifact_type="LESSON",
            answer=answer,
            summary=f"No-exploration window detected ({subtype}) from real journal cycles.",
            tags=("exploration", "probation", "lifecycle", subtype),
            source_kind="journal",
            source_refs=cycle_ids,
            created_at=self.now(),
            rationale="real journal window had market-open cycles with no BUY/SELL intent, no submitted or rejected orders, and zero intended notional",
        )
        self._append_event(
            "KNOWLEDGE_ARTIFACT_PROPOSED",
            status="SUCCESS",
            message=artifact.summary,
            payload=artifact.model_dump(mode="json"),
        )
        result = self.knowledge_admission.admit(artifact)
        self._append_event(
            "KNOWLEDGE_ARTIFACT_ADMISSION_REVIEWED",
            status="ACCEPTED" if result.accepted else "REJECTED",
            message=result.reason,
            payload={"accepted": result.accepted, "reason": result.reason, "artifact_id": result.artifact_id},
        )

    @staticmethod
    def _diagnose_no_exploration(summary: dict[str, object]) -> tuple[str, str]:
        """Classify why no exploration happened so each lesson carries distinct content.

        Subtype is appended to the artifact tags and folded into the artifact_id
        so KnowledgeAdmission's content-hash dedup keeps only one lesson per
        (subtype, answer) pair instead of writing the same generic lesson per
        window.
        """
        action_counts = summary.get("action_counts") or {}
        skipped = int(summary.get("skipped_orders", 0) or 0)
        rejected = int(summary.get("rejected_orders", 0) or 0)
        strategy_ids = tuple(summary.get("strategy_ids") or ())
        if isinstance(action_counts, dict) and action_counts and set(action_counts) == {"HOLD"}:
            return (
                "all-hold",
                "All decisions in the window were HOLD; the active strategy is degenerate. Pause non-baseline HOLD-only strategies and admit a distinct small skill so Guardian can react to new behavior.",
            )
        if rejected > 0 and skipped == 0:
            return (
                "guardian-rejected",
                "BUY/SELL intent existed but Guardian rejected every order; review per-strategy position limits and confidence thresholds rather than waiting for new probation cycles.",
            )
        if skipped > 0 and rejected == 0:
            return (
                "executor-skipped",
                "Decisions were generated but execution skipped every order; inspect the executor path (market hours, fractional support, or position holdings) before proposing new skills.",
            )
        if not strategy_ids:
            return (
                "no-active-strategy",
                "The window had no active strategy id; ensure at least one non-baseline strategy is in ACTIVE or PROBATION lifecycle so exploration cycles can occur.",
            )
        return (
            "generic",
            "No trade is not failure; no exploration is failure. Probation must not require submitted orders, and degenerate non-baseline strategies should be paused so distinct safe alternatives can be explored.",
        )

    def _manage_strategy_lifecycle(self) -> None:
        if self.lifecycle_manager is None or self.strategy_library is None or self.reflection_memory is None:
            return
        reflection = self.reflection_memory.load()
        if reflection is None:
            return
        results = self.reflection_memory.strategy_results(reflection)
        for decision in self.lifecycle_manager.review(self.strategy_library.list(), results):
            old_lifecycle = decision.strategy.lifecycle
            updated = decision.strategy.model_copy(update={"lifecycle": decision.new_lifecycle})
            self.strategy_library.save(updated)
            self._append_event(
                "STRATEGY_LIFECYCLE_UPDATED",
                status="SUCCESS",
                message=decision.reason,
                strategy_id=decision.strategy.strategy_id,
                payload={
                    "old_lifecycle": old_lifecycle,
                    "new_lifecycle": decision.new_lifecycle,
                    "reason": decision.reason,
                },
            )

    def _due(self, last_at: datetime | None, interval_seconds: int) -> bool:
        if last_at is None:
            return True
        return (self.now() - last_at).total_seconds() >= interval_seconds

    def _sleep_seconds(self) -> int:
        if self.scheduler is not None and hasattr(self.scheduler, "sleep_seconds"):
            return self.scheduler.sleep_seconds(
                default_interval=self.config.daemon_interval_seconds,
                max_sleep=self.config.maintenance_interval_seconds,
            )
        return self.config.daemon_interval_seconds

    def _append_event(
        self,
        event_type: str,
        *,
        status: str,
        message: str,
        payload: dict[str, object] | None = None,
        strategy_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        if self.journal is None:
            return
        event = JournalEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=self.now(),
            status=status,
            message=message,
            cycle_id=self.last_cycle_id,
            strategy_id=strategy_id,
            task_id=task_id,
            payload=payload or {},
        )
        self.journal.append_event(event)

    def _heartbeat(self, status: str, message: str) -> None:
        payload = HeartbeatPayload(
            pid=os.getpid(),
            status=status,
            timestamp=self.now(),
            cycle_count=self.cycle_count,
            error_count=self.error_count,
            last_cycle_id=self.last_cycle_id,
            message=message,
        )
        self.health.write(payload)

    def _acquire_lock(self) -> None:
        pidfile = Path(self.config.pidfile_path)
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        # A non-atomic write can leave a truncated pid such as "2590", which may
        # belong to an unrelated live process - `--stop` would then signal it.
        # The lock also closes the check-then-write race between two daemons.
        with file_lock(pidfile.with_suffix(".lock")):
            if pidfile.exists():
                existing = pidfile.read_text(encoding="utf-8").strip()
                if existing and self._pid_is_alive(existing):
                    raise DaemonAlreadyRunningError(f"daemon already running with pid {existing}")
            write_text_atomic(pidfile, f"{os.getpid()}\n")

    def _release_lock(self) -> None:
        pidfile = Path(self.config.pidfile_path)
        if pidfile.exists() and pidfile.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pidfile.unlink()

    def _pid_is_alive(self, raw_pid: str) -> bool:
        try:
            pid = int(raw_pid)
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            return False
        except PermissionError:
            return True
        return True
