from __future__ import annotations

import hashlib
import importlib.util
import os
import signal
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from min_agent import calibration, coerce, counterfactual, offline_validation
from min_agent.atomicio import file_lock, write_text_atomic
from min_agent.broker_evidence import latest_evidence_batch
from min_agent.config import AgentConfig
from min_agent.curriculum import StructuredCurriculumAgent
from min_agent.evaluator import (
    PNL_EVIDENCE_ACCOUNT_VERIFIED,
    PNL_EVIDENCE_MISSING,
    PNL_EVIDENCE_STRATEGY_REALIZED_VERIFIED,
    DeterministicEvaluator,
    confirmed_fill_activities,
)
from min_agent.fill_reconciler import FILL_EVENT, FillReconciler
from min_agent.health import HealthMonitor
from min_agent.journal import JsonlJournal
from min_agent.knowledge_admission import KnowledgeAdmission
from min_agent.knowledge_library import lesson_effects
from min_agent.models import (
    BrokerEvidenceBatch,
    DaemonStatus,
    HeartbeatPayload,
    JournalEvent,
    JournalEventStatus,
    JournalEventType,
    KnowledgeArtifact,
    StrategyResult,
)
from min_agent.reflection_memory import ReflectionMemory
from min_agent.strategy_admission import StrategyAdmission
from min_agent.strategy_engine import (
    StrategyLibrary,
    StrategyLifecycleDecision,
    StrategyLifecycleManager,
)

#: Calendar days a strategy stays PAUSED before it may be re-examined at all.
PAUSED_REVIEW_DAYS = 3


def source_fingerprint() -> str:
    """A short digest of the `min_agent` package as it is on disk right now.

    Hashed from the source files rather than a git revision, because the question being
    asked is not "which commit is checked out" but "is the process running the same code
    that is on disk" - and the two differ constantly while a daemon is left running
    across an edit. On its own this reads the worktree; it describes the running code only
    when taken at import, which is what `LOADED_SOURCE_FINGERPRINT` is for.

    Falls back to hashing the module's bytecode if the source is unavailable, so an
    installed package without .py files still produces a stable value.
    """
    package_dir = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_dir.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            cache = importlib.util.cache_from_source(str(path))
            try:
                digest.update(Path(cache).read_bytes())
            except OSError:
                continue
    return digest.hexdigest()[:16]


#: The fingerprint of the code this process imported, taken once. The heartbeat used to call
#: `source_fingerprint()` on every beat, which re-reads the files on disk - so after an edit a
#: daemon still running the old code reported the new fingerprint, and the check built to catch
#: exactly that passed. Python does not re-import on a file change, so the value at import time
#: is the one that describes what is running.
LOADED_SOURCE_FINGERPRINT = source_fingerprint()


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
        #: Called with the running cycle count after each cycle, so a caller that needs a
        #: per-cycle number can hold it. The incumbent cadence is defined over cycles but
        #: reads `reflection.json`, which is a 30-minute-old snapshot, so without this the
        #: cadence advances once per reflection window and fires in whole-window batches
        #: rather than one cycle in five.
        on_cycle_count=None,
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
        self.on_cycle_count = on_cycle_count
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
        #: Events appended by this process, cleared at the start of every maintenance pass so
        #: it holds one pass's worth; see `_appended_since`.
        self._appended: list[JournalEvent] = []
        #: The counterfactual report this pass computed, reused rather than recomputed.
        self._last_counterfactual_report: counterfactual.CounterfactualReport | None = None
        self.last_evidence_at: datetime | None = None
        self.last_reflection_at: datetime | None = None
        self.last_curriculum_at: datetime | None = None
        # Profit target state — only emit PROFIT_TARGET_CHECKED on transitions
        # to avoid flooding the journal with identical "not satisfied" events.
        self._last_profit_target_status: str | None = None

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
                # One failure boundary for the whole loop, not one per call site.
                #
                # `_run_symbol` has always had its own try/except; `_maintenance` and
                # `_reset_daily_count_if_needed` did not, and neither do five of the functions
                # `_maintenance` calls. A malformed journal shape reaching the evaluator, or a
                # strategy file that no longer parses, propagated out of run() and killed the
                # daemon - and with StartLimitBurst=5 systemd then refused to restart it, so
                # one bad record could end trading for the day with only a log line to say so.
                #
                # Maintenance failing is not a reason to stop trading: the trading cycle is
                # separately guarded, the risk limits do not depend on any of it, and a
                # daemon that keeps trading while reporting its own maintenance failures is
                # strictly better than one that stops.
                try:
                    self._reset_daily_count_if_needed()
                    self._maintenance()
                except Exception as exc:
                    self.error_count += 1
                    self._heartbeat(
                        "BACKING_OFF",
                        f"maintenance error, continuing to trade: "
                        f"{type(exc).__name__}: {exc}",
                    )
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
            if self.on_cycle_count is not None:
                # Reported even when the cycle failed, so the number stays monotone and never
                # skips. A cadence measured against a counter that jumped would be a cadence
                # against something other than the cycle count.
                self.on_cycle_count(self.cycle_count)
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

    def _request_stop(self, signum, _frame) -> None:  # pragma: no cover - signal wiring is integration behavior
        self._stop_requested = True
        self._heartbeat("STOPPING", f"received signal {signum}")

    def _startup_reconcile(self) -> bool:
        if self.startup_reconciler is None:
            return True
        report = self.startup_reconciler.reconcile()
        status: JournalEventStatus = "SUCCESS" if report.matched else "FAILED"
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

    def _cumulative_cycles_by_strategy(self) -> tuple[dict[str, int], dict[str, int]]:
        """Lifetime service per strategy: cycles served, and BUY/SELL decisions made.

        The reflection window is `reflection_window` records - 50 by default, about
        five hours - shared by every strategy that traded in it, so it currently
        holds at most 7 cycles for any one strategy. Probation is budgeted at 13 and
        was being compared against that number, which made the budget unreachable:
        candidates never left probation, the queue never emptied, and because the
        queue has absolute priority in the selector the one ACTIVE strategy was
        selected 0 times in the 146 cycles after its promotion.

        The journal is the single source of truth for how often a strategy has run,
        and this cycle already reads it in full several times over, so the counts
        cost nothing that is not already being paid.
        """
        cycles: dict[str, int] = {}
        attempts: dict[str, int] = {}
        if self.journal is None:
            return cycles, attempts
        for record in self.journal.read_all():
            strategy_id = record.decision.strategy_id
            if not strategy_id:
                continue
            key = str(strategy_id)
            cycles[key] = cycles.get(key, 0) + 1
            if record.decision.action in {"BUY", "SELL"}:
                attempts[key] = attempts.get(key, 0) + 1
        return cycles, attempts

    def _reflect(self, *, evidence: BrokerEvidenceBatch | None = None) -> None:
        if self.journal is None or self.reflection_memory is None:
            return
        try:
            recent = self.journal.last_n(self.config.reflection_window)
            cumulative_cycles, cumulative_trade_attempts = self._cumulative_cycles_by_strategy()
            reflection = self.reflection_memory.reflect(
                recent,
                evidence=evidence,
                fills=self._confirmed_fills(),
                seeded_fills=self._confirmed_fill_activities(),
                cumulative_cycles=cumulative_cycles,
                cumulative_trade_attempts=cumulative_trade_attempts,
            )
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

    def _screen_candidate(
        self, strategy_id: str, *, _events=None, _latest_payloads=None, _day_up=None
    ) -> None:
        """Screen a candidate on the decisions it has actually produced.

        Read from the journalled counterfactual verdicts, so this cannot disagree
        with what was recorded and cannot manufacture evidence. It can only
        report; promotion stays with prospective live cycles.

        A verdict identical to the one already on record for this strategy is not
        journalled again. Measured on this journal: 9661 of 9832 events repeated the
        previous verdict unchanged, and a maintenance pass wrote 48 of them every
        fifteen minutes. The verdict is a pure function of the strategy's recorded
        decisions, so a pass that changed no decision cannot have changed it.

        Nothing downstream can tell the two apart, which was checked rather than
        assumed: `_offline_evidence_by_strategy`, `lineage` and `experiment_registry`
        each keep the latest verdict per strategy and overwrite, `summarize` counts
        one verdict per strategy rather than per event, and the lifecycle reviewer
        returns early when the strategy is already PAUSED or RETIRED. The full
        payload is compared, not just the verdict, because the promotion gate reads
        `scored` and a strategy crossing the threshold with an unchanged verdict is a
        real transition.
        """
        if self.journal is None:
            return
        try:
            decisions = offline_validation.collect_decisions(
                self.journal.read_events("COUNTERFACTUAL_EVALUATED")
                if _events is None else _events,
                strategy_id,
            )
            result = offline_validation.validate(
                decisions, strategy_id=strategy_id, day_up=_day_up
            )
        except Exception as exc:
            self._append_event(
                "OFFLINE_VALIDATION_COMPLETED",
                status="FAILED",
                message=f"{type(exc).__name__}: {exc}",
                strategy_id=strategy_id,
                payload={"strategy_id": strategy_id, "verdict": "FAILED"},
            )
            return
        payload = result.to_payload()
        if _latest_payloads is not None and _latest_payloads.get(strategy_id) == payload:
            return
        self._append_event(
            "OFFLINE_VALIDATION_COMPLETED",
            status="SUCCESS",
            message=result.reason,
            strategy_id=strategy_id,
            payload=payload,
        )
        # The rejection itself is applied by `_manage_strategy_lifecycle`, from the rule
        # set. It used to be applied here instead, by `_apply_offline_rejection`, which meant
        # the same screen verdict produced PAUSED on the daemon path and RETIRED from
        # `StrategyLifecycleManager.review()` - one piece of evidence, two rulings, which is
        # the same one-authoritative-rule violation the veto was moved out of.

    def _latest_screen_payloads(self, events) -> dict[str, dict]:
        """The full payload last journalled per strategy, for change detection.

        Returns the payload rather than just the verdict so the comparison covers
        every field a consumer reads, not the one field that happens to be called
        the verdict.
        """
        latest: dict[str, dict] = {}
        for event in events:
            if not event.strategy_id:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            latest[event.strategy_id] = payload
        return latest

    def _offline_evidence_by_strategy(self, *, _validation=None) -> dict[str, object]:
        """The latest offline validation result per strategy, read from the journal.

        Read rather than recomputed so the lifecycle and the screen cannot disagree:
        both are looking at the same journalled rows. A strategy with no verdict yet
        is simply absent, and the gate reads that as "not yet" rather than as a pass.
        """
        if self.journal is None:
            return {}
        latest: dict[str, object] = {}
        try:
            events = (
                _validation
                if _validation is not None
                else self.journal.read_events("OFFLINE_VALIDATION_COMPLETED")
            )
        except Exception:
            return {}
        for event in events:
            if not event.strategy_id:
                continue
            latest[event.strategy_id] = offline_validation.OfflineValidationResult.from_payload(
                event.strategy_id, event.payload, event.event_id
            )
        return latest

    def _screen_all_strategies(self, *, _events=None, _validation=None) -> None:
        """Screen every strategy in the library, not just new candidates.

        The admission-time screen is structurally incapable of rejecting anything:
        a candidate is brand new, so it has no recorded decisions and always comes
        back INCONCLUSIVE. Judging a strategy requires a history, and history
        only exists for strategies that have already been running - so this is
        where the stage has to live to mean anything.

        Verdicts are journalled for every strategy, including the INCONCLUSIVE
        ones, because "we cannot tell yet" is a result worth having on the record
        and it is what stops a thin strategy from being treated as validated.
        """
        if self.journal is None or self.strategy_library is None:
            return
        events = (
            self.journal.read_events("COUNTERFACTUAL_EVALUATED")
            if _events is None
            else _events
        )
        latest_payloads = (
            self._latest_screen_payloads(_validation)
            if _validation is not None
            else None
        )
        # Once per pass, over every strategy's rows: the base rate each screen is read against.
        day_up = offline_validation.day_direction(events)
        for spec in self.strategy_library.list():
            if spec.lifecycle in {"RETIRED"}:
                continue
            self._screen_candidate(
                spec.strategy_id, _events=events, _latest_payloads=latest_payloads,
                _day_up=day_up,
            )

    def _recorded_counterfactual_verdicts(self, events) -> dict[str, str]:
        """The verdict last journalled for each cycle, as `{cycle_id: verdict}`."""
        recorded: dict[str, str] = {}
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            for row in coerce.field_dict_tuple(payload.get("rows"), "payload.rows"):
                cycle_id = coerce.field_str(row.get("cycle_id"), "row.cycle_id")
                if cycle_id:
                    recorded[cycle_id] = coerce.field_str(row.get("verdict"), "row.verdict")
        return recorded

    def _record_counterfactuals(self, *, _events) -> None:
        """Score every decision against what the market actually did next.

        864 of 920 journaled cycles are HOLD, and until this existed nothing
        recorded what happened after them, so the system could count decisions and
        their realized PnL but never learn whether a hold was right. This is the
        first thing in the loop that can say a hold was wrong.

        Only decisions that are new, or whose verdict changed, are journalled.

        Every pass used to append the entire ledger: 356 events carrying 210,473
        rows to describe 720 distinct decisions, of which 209,753 - 99% - were
        re-records of a row already in the journal. That is 66MB, 55% of a 114MB
        journal, growing by ~186KB every fifteen minutes, and `_screen_all_strategies`
        already re-parsed all of it on every pass to answer a question about a fixed
        720 decisions. The growth was self-feeding: more rows meant a slower read,
        which meant the same rows were re-derived and re-written for longer.

        A PENDING row is deliberately still re-journalled when it resolves, which is
        why the rule is "new or changed" rather than "not seen before": a row whose
        horizon has not elapsed yet is not a claim about the outcome, and the later
        verdict that supersedes it is the useful record.
        """
        if self.journal is None:
            return
        try:
            symbol = self.config.symbols[0] if self.config.symbols else "SPY"
            report = counterfactual.evaluate(
                self.journal.read_all(),
                symbol=symbol,
                horizon_hours=self.config.counterfactual_horizon_hours,
                assumed_cost_pct=self.config.assumed_round_trip_cost_pct,
            )
        except Exception as exc:
            self._append_event(
                "COUNTERFACTUAL_EVALUATED",
                status="FAILED",
                message=f"{type(exc).__name__}: {exc}",
                payload={"status": "failed"},
            )
            return
        self._last_counterfactual_report = report
        if not report.rows:
            return
        recorded = self._recorded_counterfactual_verdicts(_events)
        fresh = [
            row
            for row in report.rows
            if recorded.get(row.cycle_id) != row.verdict
        ]
        if not fresh:
            # Every decision already carries the verdict it has now, so there is
            # nothing to record. The aggregates below are computed over the whole
            # report and would be identical to the last event's, so writing them
            # again would add a row that says only that the pass ran.
            return
        counts = report.counts()
        self._append_event(
            "COUNTERFACTUAL_EVALUATED",
            status="SUCCESS",
            message=(
                f"hold_quality={report.hold_quality()} "
                f"scored={len(report.scored)} pending={report.pending_count} "
                f"gap={report.gap_count} new_or_changed={len(fresh)}/{len(report.rows)}"
            ),
            payload={
                "symbol": symbol,
                "horizon_hours": self.config.counterfactual_horizon_hours,
                "assumed_round_trip_cost_pct": self.config.assumed_round_trip_cost_pct,
                "hold_quality": report.hold_quality(),
                "counts": counts,
                "scored": len(report.scored),
                "pending": report.pending_count,
                "gap": report.gap_count,
                "net_pct_total": round(report.net_pct_total(), 4),
                "rows": [
                    {
                        "cycle_id": row.cycle_id,
                        "action": row.action,
                        "strategy_id": row.strategy_id,
                        "decided_at": row.decided_at.isoformat(),
                        "price_at_decision": row.price_at_decision,
                        "price_at_horizon": row.price_at_horizon,
                        "gap_hours": row.gap_hours,
                        "gross_return_pct": row.gross_return_pct,
                        "net_return_pct": row.net_return_pct,
                        "verdict": row.verdict,
                        "rule_action": row.rule_action,
                        "rule_verdict": row.rule_verdict,
                        "override_value_pct": row.override_value_pct,
                    }
                    for row in fresh
                ],
            },
        )

    def _last_market(self) -> tuple[float | None, str]:
        """The most recent real broker price, for evaluating executable capability."""
        if self.journal is None:
            return None, (self.config.symbols[0] if self.config.symbols else "SPY")
        for record in reversed(self.journal.read_all()):
            return record.snapshot.last_price, record.snapshot.symbol
        return None, (self.config.symbols[0] if self.config.symbols else "SPY")

    def _open_book(self) -> list[dict[str, object]]:
        """Positions from the most recent real snapshot, for the curriculum context.

        Only the fields an exit decision needs, and only what the broker reported.
        """
        if self.journal is None:
            return []
        for record in reversed(self.journal.read_all()):
            held = [
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "market_value": round(position.market_value, 2),
                }
                for position in record.snapshot.positions
                if position.quantity > 0
            ]
            if held:
                return held
        return []

    def _recent_rejections(self, limit: int = 6) -> list[dict[str, str]]:
        """Rejected proposals with the reason, newest verdict per strategy id.

        Admission already produces a specific, actionable refusal - it names the
        colliding strategy and the dimensions that must differ - and the journal
        already keeps it. Nothing read it back. The curriculum was therefore
        asked to "propose the next distinct strategy" while being shown only
        trading activity, never its own output, so a refused id looked unused.
        Deduplicated because the same id is reviewed again on every retry and six
        copies of one sentence teaches nothing a single copy does not.
        """
        if self.journal is None:
            return []
        latest: dict[str, str] = {}
        for event in self.journal.read_events("STRATEGY_ADMISSION_REVIEWED"):
            if event.payload.get("accepted"):
                continue
            strategy_id = (event.strategy_id or "").strip()
            reason = str(event.payload.get("reason") or "").strip()
            if strategy_id and reason:
                latest[strategy_id] = reason
        return [
            {"strategy_id": strategy_id, "rejected_because": reason}
            for strategy_id, reason in list(latest.items())[-limit:]
        ]

    def _curriculum_proposal(self) -> None:
        if self.reflection_memory is None or self.curriculum_agent is None:
            return
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
                    prior_rejections=self._recent_rejections(),
                    guardian_max_position_value=self.config.max_position_value,
                    open_positions=self._open_book(),
                    last_price=self._last_market()[0],
                    symbol=self._last_market()[1],
                )
            except (ConnectionError, TimeoutError, OSError):
                self.sleep(5)
                task = self.curriculum_agent.propose(
                    reflection=reflection,
                    current_strategies=strategies,
                    recent_exploration_summary=exploration_summary,
                    prior_rejections=self._recent_rejections(),
                    guardian_max_position_value=self.config.max_position_value,
                    open_positions=self._open_book(),
                    last_price=self._last_market()[0],
                    symbol=self._last_market()[1],
                )
            strategy_id = task.strategy_spec.strategy_id if task.strategy_spec is not None else None
            # A fallback task is a hard-coded constant, not model output. It is
            # recorded as SKIPPED so the journal distinguishes the two: 30 of
            # the 168 recorded CURRICULUM_PROPOSED events were fallbacks
            # journalled as SUCCESS, which made them indistinguishable from real
            # proposals.
            from_model = task.source == "llm"
            reason = getattr(self.curriculum_agent, "last_failure", None)
            message = task.summary if from_model else f"deterministic fallback (not LLM output): {task.summary}"
            if not from_model and reason:
                message = f"{message} | reason: {reason}"
            self._append_event(
                "CURRICULUM_PROPOSED",
                status="SUCCESS" if from_model else "SKIPPED",
                message=message,
                strategy_id=strategy_id,
                task_id=task.task_id,
                payload={
                    "task_type": task.task_type,
                    "source": task.source,
                    "rationale": task.rationale,
                    "parameters": task.parameters,
                    "strategy_id": strategy_id,
                    "recent_exploration_summary": exploration_summary,
                },
            )
            if task.task_type == "STRATEGY_SPEC" and task.strategy_spec and self.strategy_admission is not None:
                # Give admission the real prices so a TREND_FOLLOW anchored to
                # a fabricated level is rejected instead of entering the library.
                self.strategy_admission.set_market_prices(self._last_known_prices())
                result = self.strategy_admission.admit(
                    task.strategy_spec, self._strategy_results()
                )
                self._append_event(
                    "STRATEGY_ADMISSION_REVIEWED",
                    status="ACCEPTED" if result.accepted else "REJECTED",
                    message=result.reason,
                    strategy_id=result.strategy_id,
                    task_id=task.task_id,
                    payload={"accepted": result.accepted, "reason": result.reason},
                )
                # Offline validation runs on the candidate's *existing* recorded
                # decisions, not on a replay of its rule. In this system a strategy
                # is context handed to the model, not an executable rule, so a
                # rule-replay would grade something the live system never runs.
                #
                # A brand-new candidate has no history, so this returns
                # INCONCLUSIVE for it. That is correct but useless on its own,
                # which is why `_screen_all_strategies` runs the same screen over
                # the whole library every maintenance pass: that is the only place
                # a verdict can have teeth, because only existing strategies have
                # recorded decisions to be judged by.
                self._screen_candidate(result.strategy_id)
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
        # Beat before the work, not only after it. The heartbeat used to be written
        # once per loop iteration, after this method returned, so a maintenance
        # pass that screens 27 strategies *and* makes a ~140s curriculum call went
        # longer than `stale_after_seconds` (900) and a daemon that was provably
        # working - journal events landing throughout - was reported dead by
        # doctor's liveness check. It went red twice before the cause was found, and
        # a health check that cries wolf is worse than none: it teaches the reader
        # to re-run it instead of reading it.
        #
        # Beating on entry rather than raising the threshold matters. A daemon that
        # writes here and then genuinely wedges inside this method still goes stale
        # 900s later, so the check keeps its ability to catch a real hang; only the
        # honest "I am working" silence is removed.
        self._heartbeat("RUNNING", "maintenance in progress")
        self._resolve_pending_fills()
        evidence = self._latest_evidence_batch()
        if self._due(self.last_evidence_at, self.config.evidence_interval_seconds):
            evidence = self._ingest_broker_evidence()
        # Bound before the branch below: `_manage_strategy_lifecycle` runs after it and
        # takes the same read, and a name bound only inside the `if` would be undefined
        # on the path where there is no journal - which is the path the tests exercise.
        validation_events = None
        self._appended = []
        if self.journal is not None:
            self._record_pnl_evidence(evidence)
            self._verify_profit_target(evidence)
            # One read of the counterfactual ledger, shared. It used to be parsed
            # twice per pass - once here and once in `_screen_all_strategies` - and
            # each read grew with the journal, so the cost of every maintenance pass
            # rose with the number of passes that had already run.
            counterfactual_events = self.journal.read_events("COUNTERFACTUAL_EVALUATED")
            # Also read once. `_offline_evidence_by_strategy` parsed every validation
            # event on every pass already, so this adds no scan - it removes one - and
            # the same read is what tells the screen whether a verdict actually moved.
            validation_events = self.journal.read_events("OFFLINE_VALIDATION_COMPLETED")
            # Each stage hands what it appended to the next, so a pass acts on its own
            # results. The screen used to run on the read taken before the counterfactuals
            # were recorded, and the lifecycle on the read taken before the screen - so every
            # newly scored decision reached a verdict one pass (15 minutes) late, and a new
            # verdict reached the lifecycle one pass after that.
            mark = len(self._appended)
            self._record_counterfactuals(_events=counterfactual_events)
            counterfactual_events = [
                *counterfactual_events,
                *self._appended_since(mark, "COUNTERFACTUAL_EVALUATED"),
            ]
            self._record_calibration_lesson()
            self._retire_lessons_without_effect()
            mark = len(self._appended)
            self._screen_all_strategies(
                _events=counterfactual_events, _validation=validation_events
            )
            validation_events = [
                *validation_events,
                *self._appended_since(mark, "OFFLINE_VALIDATION_COMPLETED"),
            ]
        if self.reflection_memory is not None and self._due(self.last_reflection_at, self.config.reflection_interval_seconds):
            self._reflect(evidence=evidence)
        if self.curriculum_agent is not None and self.reflection_memory is not None and self._due(self.last_curriculum_at, self.config.curriculum_interval_seconds):
            self._curriculum_proposal()
        self._manage_strategy_lifecycle(_validation=validation_events)
        self.last_maintenance_at = self.now()

    def _last_known_prices(self) -> dict[str, float]:
        """Real last prices from the journal, newest cycle wins.

        Used to validate a proposed reference_price against the market. Reading
        the journal rather than calling the broker keeps admission cheap and
        works even when the broker window is closed.
        """
        prices: dict[str, float] = {}
        if self.journal is None:
            return prices
        for record in self.journal.last_n(self.config.reflection_window):
            prices[record.snapshot.symbol] = record.snapshot.last_price
        return prices

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

    def _confirmed_fill_activities(self):
        if self.journal is None:
            return []
        return confirmed_fill_activities(self.journal, self.journal.read_all())

    def _record_pnl_evidence(self, evidence: BrokerEvidenceBatch | None) -> None:
        if self.journal is None:
            return
        report = DeterministicEvaluator().evaluate(
            self.journal.read_all(),
            evidence=evidence,
            fills=self._confirmed_fills(),
            seeded_fills=self._confirmed_fill_activities(),
        )
        success = report.pnl_evidence != PNL_EVIDENCE_MISSING
        self._append_event(
            "PNL_EVIDENCE_RECORDED" if success else "PNL_EVIDENCE_FAILED",
            status="SUCCESS" if success else "SKIPPED",
            message=report.pnl_evidence,
            payload=report.model_dump(mode="json"),
        )

    def _verify_profit_target(self, evidence: BrokerEvidenceBatch | None) -> None:
        if self.journal is None:
            return
        report = DeterministicEvaluator().evaluate(
            self.journal.read_all(),
            evidence=evidence,
            fills=self._confirmed_fills(),
            seeded_fills=self._confirmed_fill_activities(),
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
        return latest_evidence_batch(self.journal)

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

    def _record_calibration_lesson(self) -> None:
        """Send the model its own measured reliability, through the knowledge channel.

        The system has been measuring whether the model's stated confidence predicts
        its decisions turning out right, and reporting the answer - and nothing acted
        on it. `doctor` has said `MIS-CALIBRATED` about this for days. A measurement no
        component reads is not self-evolution, it is a log line. The knowledge library
        already carries journal measurements to the model through `context["lessons"]`,
        so this uses that path rather than adding a second one, and the artifact says
        what was measured rather than what to conclude - the risk limits stay where
        they are and the model is the one being told.

        **The first version of this lesson was wrong and said so to the model every
        maintenance pass.** It read "confidence is inverted" off a comparison against the
        overall base rate, which on this account is the market's direction: the per-day
        rate of scored decisions runs 96.9% / 26.6% / 85.1% / 12.3%, so the 0.0-0.2
        bucket that looked 90.6% correct was one day's base rate and nothing else. Read
        against its own days, the HOLD buckets sit within a point of zero margin. The
        lesson is now gated on the day-adjusted margin being materially negative, so a
        day effect cannot be taught to the model as a character trait.

        Cheap enough for every maintenance pass: 0.34s over 1179 cycles, dominated by
        reading the journal rather than by the evaluation.
        """
        if self.knowledge_admission is None or self.journal is None:
            return
        records = self.journal.read_all()
        if not records:
            return
        verdicts = {
            row.cycle_id: row.verdict
            # The same symbol and cost the ledger is scored with. Left at the defaults, this
            # graded calibration at a hardcoded 0.05% on SPY whatever the operator configured,
            # so the lesson and the screen could disagree about the same decision.
            for row in counterfactual.evaluate(
                records,
                symbol=self.config.symbols[0] if self.config.symbols else "SPY",
                horizon_hours=self.config.counterfactual_horizon_hours,
                assumed_cost_pct=self.config.assumed_round_trip_cost_pct,
            ).rows
        }
        rows = calibration.build_rows(records, verdicts, source="llm")
        report = calibration.calibrate(rows, min_confidence=self.config.min_confidence)
        if report.brier is None or not str(report.verdict).startswith("MIS-CALIBRATED"):
            return

        buckets = [
            bucket for bucket in report.buckets if bucket.n >= calibration.MIN_SCORED_DECISIONS
        ]
        if len(buckets) < 2:
            return
        # The cycles this claim is computed from, so the artifact can be traced back
        # to the records rather than asserting a fact about the model in general. If
        # there are none, there is nothing to attribute the claim to and it is not
        # published: `KnowledgeArtifact` refuses an artifact with no source, and
        # that refusal is the correct outcome, not something to work around.
        scored_refs = [row.cycle_id for row in rows if row.informative][-200:]
        if not scored_refs:
            return
        # Refuse to teach an inversion that the day-adjusted figures do not support.
        #
        # The raw comparison said confidence "runs backwards", and that reading survived
        # in this lesson for weeks - but the per-day base rate on this account runs
        # 96.9% / 26.6% / 85.1% / 12.3%, so the bucket that looked 90.6% accurate was the
        # one day it came from. Once each bucket is read against its own days, the HOLD
        # buckets land within one point of zero margin. Telling a model that its
        # confidence is inverted, when what the record shows is that holding is right on
        # down days, is a lesson that changes behaviour and is not true.
        #
        # So the lesson is only published when the day-adjusted margin is materially
        # negative, and it quotes that margin rather than the raw accuracy spread.
        if report.top_bucket_day_margin is None:
            return
        if report.top_bucket_day_margin >= -calibration.MIN_MATERIAL_MARGIN:
            return
        # Only buckets the control could judge are eligible to be called the worst; a
        # bucket with no day expectation carries no margin and sorting it first would
        # report a number the report does not have.
        judged = [(b.day_margin, b) for b in buckets if b.day_margin is not None]
        if not judged:
            return
        worst = min(judged, key=lambda pair: pair[0])[1]
        day_clause = (
            f" Against the {report.days} day(s) those decisions were taken on, its Brier "
            f"is {report.day_brier:.3f}, so {(report.brier - report.day_brier):.3f} of "
            "the gap is the market's direction rather than yours."
            if report.day_brier is not None else ""
        )
        answer = (
            f"On this account your stated confidence does not predict whether you are "
            f"right. Across {report.scored} scored decisions on {report.days} trading "
            f"days (base rate {report.base_rate:.0%}), a {worst.low:.1f}-"
            f"{worst.high:.1f} confidence bucket was right {worst.accuracy:.0%} of the "
            f"time while the days it was taken on were right "
            f"{worst.day_expected_accuracy:.0%} - a margin of "
            f"{worst.day_margin:+.0%}. Brier {report.brier:.3f} against the 0.25 that "
            f"always answering 0.5 would score.{day_clause} Judge your own past "
            f"decisions by what followed them, not by how sure you felt."
        )
        digest = hashlib.sha1(f"calibration|{answer}".encode()).hexdigest()[:12]
        artifact = KnowledgeArtifact(
            artifact_id=f"lesson-calibration-{digest}",
            artifact_type="LESSON",
            answer=answer,
            summary=(
                f"Confidence does not predict correctness on this account: the worst "
                f"bucket is {worst.day_margin:+.0%} against its own days, Brier "
                f"{report.brier:.3f} over {report.scored} scored decisions."
            ),
            tags=("calibration", "model", "counterfactual"),
            source_kind="journal",
            source_refs=tuple(scored_refs),
            created_at=self.now(),
            rationale=(
                "computed by min_agent.calibration over the same counterfactual verdicts "
                "doctor reports, delivered through the existing knowledge channel"
            ),
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
            payload={
                "accepted": result.accepted,
                "reason": result.reason,
                "artifact_id": result.artifact_id,
            },
        )

    def _record_no_exploration_lesson(self, summary: dict[str, object]) -> None:
        if self.knowledge_admission is None or not summary.get("no_exploration"):
            return
        cycle_ids = coerce.field_str_tuple(summary.get("cycle_ids"), "summary.cycle_ids")
        if not cycle_ids:
            return
        subtype, answer = self._diagnose_no_exploration(summary)
        # Content-hash-based artifact_id keeps the same lesson stable across runs
        # and lets KnowledgeAdmission's content-hash dedup do its job.
        digest = hashlib.sha1(f"{subtype}|{answer}".encode()).hexdigest()[:12]
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
        skipped = coerce.field_int(summary.get("skipped_orders"), "summary.skipped_orders")
        rejected = coerce.field_int(summary.get("rejected_orders"), "summary.rejected_orders")
        strategy_ids = coerce.field_str_tuple(summary.get("strategy_ids"), "summary.strategy_ids")
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

    def _strategy_results(self) -> list[StrategyResult]:
        """The current reflection's per-strategy results, or none before the first one.

        Read rather than recomputed: the lifecycle review and the selector are handed
        the same list, so admission is being asked the same question they are.
        """
        if self.reflection_memory is None:
            return []
        reflection = self.reflection_memory.load()
        if reflection is None:
            return []
        return self.reflection_memory.strategy_results(reflection)

    def _manage_strategy_lifecycle(self, *, _validation=None) -> None:
        if self.lifecycle_manager is None or self.strategy_library is None or self.reflection_memory is None:
            return
        reflection = self.reflection_memory.load()
        if reflection is None:
            return
        results = self.reflection_memory.strategy_results(reflection)
        # Phase 6: promotion is evidence-gated, so the lifecycle review is given the
        # offline validation verdict for each strategy. Without this the gate would
        # see "no evidence" for everyone and block every promotion permanently -
        # a gate that cannot be satisfied is not a gate, it is a freeze.
        evidence = self._offline_evidence_by_strategy(_validation=_validation)
        decisions = self.lifecycle_manager.review(self.strategy_library.list(), results, evidence)
        decisions.extend(self._reconsider_paused(results, evidence))
        for decision in decisions:
            self._apply_lifecycle_decision(decision)

    def _retire_lessons_without_effect(self) -> None:
        """Take a lesson out of the prompt once the ablation shows it changes nothing.

        Each lesson is shown on half of cycles (`HybridDecisionEngine._show_lesson`). After it
        has been both shown and withheld on `LESSON_EFFECT_MIN_DAYS` trading days and enough
        paired decisions in each arm, a lesson whose effect on the model's override value is
        inside one standard error of zero is RETIRED: it costs prompt on every cycle and has
        not been shown to buy anything. Retired, not deleted - its sources stay on disk.
        Inert until that much ablated data exists.
        """
        report = self._last_counterfactual_report
        if self.journal is None or self.knowledge_admission is None or report is None:
            return
        library = self.knowledge_admission.knowledge_library
        effects = lesson_effects(self.journal.read_all(), report.rows)
        for artifact in library.list(status="ACCEPTED"):
            effect = effects.get(artifact.artifact_id)
            if artifact.artifact_type != "LESSON" or effect is None or not effect.without_effect:
                continue
            reason = (
                f"no measurable effect: shown {effect.shown}, withheld {effect.withheld} over "
                f"{effect.days} trading days; effect {effect.effect:+.4f} within one se "
                f"({effect.se:.4f}) of zero"
            )
            library.save(artifact.model_copy(update={"status": "RETIRED"}))
            self._append_event(
                "KNOWLEDGE_ARTIFACT_ADMISSION_REVIEWED", status="REJECTED", message=reason,
                payload={"artifact_id": artifact.artifact_id, "phase": "retired_without_effect",
                         "shown": effect.shown, "withheld": effect.withheld,
                         "effect": effect.effect, "se": effect.se},
            )

    def _apply_lifecycle_decision(self, decision) -> None:
        if self.strategy_library is None:
            return
        old_lifecycle = decision.strategy.lifecycle
        payload = {
            "old_lifecycle": old_lifecycle,
            "new_lifecycle": decision.new_lifecycle,
            "reason": decision.reason,
        }
        # Journal the intent, write the file, then journal the outcome.
        #
        # Saving first meant a crash between the save and the event left a
        # strategy in a terminal state with no recorded decision - and that is
        # exactly what happened here: eight duplicate retirements landed on
        # disk while nothing was journalled, which the provenance check could
        # not even see, let alone report. Writing the event first means the
        # worst case is an event for a transition that did not happen, which
        # is visible and harmless; the reverse is a silent state change.
        event_id = self._append_event(
            "STRATEGY_LIFECYCLE_UPDATED",
            status="SUCCESS",
            message=decision.reason,
            strategy_id=decision.strategy.strategy_id,
            payload={**payload, "phase": "decided"},
        )
        updated = decision.strategy.model_copy(
            update={
                "lifecycle": decision.new_lifecycle,
                "lifecycle_reason": decision.reason,
                # A transition that carries state (e.g. a probation restart bumping
                # `probation_restarts`) writes it here too; without it the lifecycle
                # label changes and the state does not, which is the "fixed into a state
                # nothing will serve" failure measured on 2026-10-06.
                **(decision.update or {}),
            }
        )
        self.strategy_library.save(updated)
        self._append_event(
            "STRATEGY_LIFECYCLE_UPDATED",
            status="SUCCESS",
            message=decision.reason,
            strategy_id=decision.strategy.strategy_id,
            payload={**payload, "phase": "applied", "decided_event_id": event_id},
        )

    def _reconsider_paused(self, results, evidence) -> list:
        """Return at most one PAUSED strategy to probation per pass, and only on evidence.

        PAUSED used to be terminal by construction - `_review_one` returns early for it -
        so 37 of 75 strategies could never be looked at again, including some paused under
        rules that have since been replaced. Reopening them all was measured and rejected in
        STATUS.md: it lengthens the serial probation queue and produces no promotion. So the
        bar is every one of:

        - paused for at least `PAUSED_REVIEW_DAYS`, read from the journalled transition -
          a strategy whose pause time is not on the record is not reopened, since its age
          would be invented;
        - its latest screen is PASS_SCREENED under the day-adjusted gate (`day_margin`
          present): no worse than its days, which is evidence, not a raw ratio;
        - the probation queue has room, by the same count admission uses;
        - the lifecycle rules, run on the reopened copy, would not immediately pause or
          retire it again - otherwise this is churn, and a HOLD-only strategy would be
          re-paused for "no exploration" on the very next pass.

        The best margin goes first; one per pass so the queue is never flooded.
        """
        if (
            self.journal is None
            or self.strategy_library is None
            or self.strategy_admission is None
            or self.lifecycle_manager is None
        ):
            return []
        admission = self.strategy_admission
        if admission.probation_backlog(results) >= admission.max_probation_queue:
            return []
        paused_at: dict[str, datetime] = {}
        for event in self.journal.read_events("STRATEGY_LIFECYCLE_UPDATED"):
            if event.strategy_id and event.payload.get("phase") == "applied":
                if event.payload.get("new_lifecycle") == "PAUSED":
                    paused_at[event.strategy_id] = event.timestamp
                else:
                    paused_at.pop(event.strategy_id, None)
        budget = admission.probation_selector.min_probation_cycles
        by_id = {result.strategy_id: result for result in results}
        candidates = []
        for spec in self.strategy_library.list():
            if spec.lifecycle != "PAUSED" or not spec.enabled:
                continue
            since = paused_at.get(spec.strategy_id)
            screen = evidence.get(spec.strategy_id)
            if since is None or (self.now() - since).days < PAUSED_REVIEW_DAYS:
                continue
            if screen is None or screen.verdict != offline_validation.PASS_SCREENED:
                continue
            if screen.day_margin is None:
                continue
            served = by_id[spec.strategy_id].cumulative_cycles if spec.strategy_id in by_id else 0
            reopened = spec.model_copy(update={
                "lifecycle": "PROBATION",
                # A fresh budget on top of what it has already been served, so it is not
                # read as budget-spent the moment it returns.
                "probation_restarts": served // max(budget, 1) + 1,
            })
            if any(
                d.new_lifecycle in {"PAUSED", "RETIRED"}
                for d in self.lifecycle_manager.review([reopened], results, evidence)
            ):
                continue
            candidates.append((screen.day_margin, spec, reopened))
        if not candidates:
            return []
        margin, spec, reopened = max(candidates, key=lambda item: item[0])
        return [StrategyLifecycleDecision(
            spec, "PROBATION",
            f"re-examined after {(self.now() - paused_at[spec.strategy_id]).days} day(s) "
            f"paused: screened PASS against its days (margin {margin:+.3f}), the probation "
            f"queue has room, and the lifecycle rules would not pause it again",
            update={"probation_restarts": reopened.probation_restarts},
        )]

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
        event_type: JournalEventType,
        *,
        status: JournalEventStatus,
        message: str,
        payload: dict[str, object] | None = None,
        strategy_id: str | None = None,
        task_id: str | None = None,
    ) -> str | None:
        """Append an event and return its id, so a caller can correlate the phases.

        Returning the id is what lets the lifecycle manager journal its decision
        before it writes the file: the 'decided' event and the 'applied' event can
        be tied together, so a crash between them leaves a traceable half-state
        rather than a silent state change.
        """
        if self.journal is None:
            return None
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
        self._appended.append(event)
        return event.event_id

    def _appended_since(self, mark: int, event_type: str) -> list[JournalEvent]:
        """Events of one type this process has appended since `mark` (a length of `_appended`)."""
        return [e for e in self._appended[mark:] if e.event_type == event_type]

    def _heartbeat(self, status: DaemonStatus, message: str) -> None:
        payload = HeartbeatPayload(
            pid=os.getpid(),
            status=status,
            timestamp=self.now(),
            cycle_count=self.cycle_count,
            error_count=self.error_count,
            last_cycle_id=self.last_cycle_id,
            message=message,
            source_fingerprint=LOADED_SOURCE_FINGERPRINT,
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
