from __future__ import annotations

import hashlib
import importlib.util
import os
import signal
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from autopoiesis import calibration, coerce, counterfactual, offline_validation
from autopoiesis.atomicio import file_lock, write_text_atomic
from autopoiesis.broker_evidence import latest_evidence_batch
from autopoiesis.config import AgentConfig
from autopoiesis.curriculum import StructuredCurriculumAgent
from autopoiesis.evaluator import (
    PNL_EVIDENCE_MISSING,
    DeterministicEvaluator,
    confirmed_fill_activities,
    owner_fill_activities,
)
from autopoiesis.fill_reconciler import FILL_EVENT, FillReconciler, latest_fill_snapshots
from autopoiesis.health import HealthMonitor
from autopoiesis.journal import JsonlJournal
from autopoiesis.knowledge_admission import KnowledgeAdmission
from autopoiesis.knowledge_library import lesson_effects
from autopoiesis.learning_evidence import ELIGIBILITY_VERSION, capture, valid_provenance
from autopoiesis.llm_decision import LLM_UNAVAILABLE_MARKER

#: How many candidates the model is offered when choosing the watchlist. Wider than the
#: slots so the choice is a choice; bounded so the prompt stays small.
ATTENTION_CANDIDATES = 40

#: How many symbols a round decides that nothing can act on, beyond the ones it always decides.
#:
#: Measured 2026-10-19 across the first session with a working model: a round decided 6 configured
#: symbols plus 4 watchlist picks, at about 1.62 minutes of wall time per symbol, so every symbol
#: was decided roughly every 16 minutes - and `_round_sleep_seconds` already documents the same
#: arithmetic from the six-symbol era ("each symbol thirteen minutes apart").
#:
#: The account holder's instruction was that any symbol may appear and anything may be searched,
#: but that the ones which meet the bar get special attention. So the bar is not "in the list",
#: it is "a decision here can change something": the symbol has a serving strategy, or the account
#: holds it. Those are decided every round. Everything else - a pick the model made, or a
#: configured symbol with no serving strategy and no position - still appears, and still gets
#: decided, but on a rotation, so four picks cannot push the cadence of the five that matter past
#: the point where the agent stops reacting to them.
#:
#: One, not two: five qualifying symbols at 1.62 minutes is 8.1, and two discoveries put the round
#: at 11.3 - past the ten minutes the review asked for. One keeps it at 9.7.
DISCOVERY_SYMBOLS_PER_ROUND = 1
from autopoiesis import attention
from autopoiesis.loop import FAIL_CLOSED_RATIONALE
from autopoiesis.models import (
    BrokerEvidenceBatch,
    DaemonStatus,
    HeartbeatPayload,
    JournalEvent,
    JournalEventStatus,
    JournalEventType,
    KnowledgeArtifact,
    StrategyResult,
)
from autopoiesis.reflection_memory import ReflectionMemory
from autopoiesis.strategy_admission import StrategyAdmission
from autopoiesis.strategy_engine import (
    MAX_PROBATION_RESTARTS,
    StrategyLibrary,
    StrategyLifecycleDecision,
    StrategyLifecycleManager,
)

#: Calendar days a strategy stays PAUSED before it may be re-examined at all.
PAUSED_REVIEW_DAYS = 3

#: Long scheduler/backoff waits check the stop flag at least this often.
COOPERATIVE_WAIT_POLL_SECONDS = 1.0


def source_fingerprint() -> str:
    """A short digest of the `autopoiesis` package as it is on disk right now.

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
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], datetime] | None = None,
        scheduler=None,
        startup_reconciler=None,
        fill_reconciler: FillReconciler | None = None,
        strategy_library: StrategyLibrary | None = None,
        reflection_memory: ReflectionMemory | None = None,
        curriculum_agent: StructuredCurriculumAgent | None = None,
        #: Asked to choose which of the broker's candidates the system should watch. None
        #: leaves the mechanical top-k in place, which is what the system did before this
        #: existed - a missing model must not be able to empty the watchlist.
        attention_transport: Callable[[str, dict], object] | None = None,
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
        self.sleep = sleep if sleep is not None else self._cooperative_sleep
        self.now = now or (lambda: datetime.now(tz=timezone.utc))
        self.scheduler = scheduler
        self.startup_reconciler = startup_reconciler
        self.fill_reconciler = fill_reconciler
        self.strategy_library = strategy_library
        self.attention_transport = attention_transport
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
        # Latest outcome of each dependency, refreshed when it is used. Not initialised from the
        # journal: a fresh process has not talked to anything yet, and claiming otherwise would put
        # a timestamp in a heartbeat that describes a run that did not happen.
        self._last_broker_at: datetime | None = None
        self._last_broker_ok: bool | None = None
        self._last_model_at: datetime | None = None
        self._last_model_ok: bool | None = None
        self.cycle_count = 0
        self.daily_cycle_count = 0
        self.current_cycle_date: date | None = None
        self.error_count = 0
        #: Consecutive broker-clock failures, reset by the first successful read. Drives the backoff
        #: in `_should_trade_now`; see there for the `ConnectTimeout` that killed the daemon twice.
        self._clock_failures = 0
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
            if not self._stop_requested and not self._startup_reconcile():
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
                    # Journalled as well as heartbeaten. The heartbeat is overwritten within
                    # minutes, so this increment used to leave a counter with no recoverable
                    # cause - and that is what happened on 2026-10-19: `error_count` read 1 and
                    # nothing anywhere said which maintenance step had failed, or when, or with
                    # what exception. An error counter whose cause disappears is a number that
                    # teaches an operator to ignore it.
                    #
                    # The write is guarded because a health path must not be the thing that kills
                    # the daemon: if the journal cannot take the record, the heartbeat below still
                    # carries it, and the loop continues trading.
                    try:
                        self._append_event(
                            "CYCLE_FAILED", status="FAILED",
                            message=f"maintenance: {type(exc).__name__}: {exc}",
                        )
                    except Exception:
                        pass
                    self._heartbeat(
                        "BACKING_OFF",
                        f"maintenance error, continuing to trade: "
                        f"{type(exc).__name__}: {exc}",
                    )
                if self._stop_requested:
                    break
                if max_cycles is not None and self.cycle_count >= max_cycles:
                    break
                if self.scheduler is not None and not self._should_trade_now():
                    if self._stop_requested:
                        break
                    self._heartbeat("RUNNING", "market closed; maintenance only")
                    if max_cycles is not None:
                        break
                    if not self._stop_requested:
                        self.sleep(self._sleep_seconds())
                        # Beat on waking, not only before sleeping. The scheduler caps a
                        # closed-market sleep at 75% of the staleness budget (675s of 900s) so a
                        # slow maintenance pass can overrun; without this beat the gap between
                        # beats was sleep *plus* the following maintenance, i.e. 675 + the pass,
                        # and a pass over 225s therefore went stale while the daemon was parked
                        # and idle. Beating here makes the longest gap max(sleep, maintenance).
                        #
                        # Measured 2026-10-18, and the measurement corrected the diagnosis: after
                        # this fix was deployed the heartbeat was *still* stale at 960s, with
                        # message "maintenance in progress" - the dominant cause was never the
                        # sleep but a maintenance pass running longer than the budget, so this is
                        # a real gap closed and not the one that was observed. What that pass
                        # needs is a beat tied to its own progress, which is a change to
                        # `_maintenance` and is not made here. The correction is written down
                        # because the first version of this comment asserted the wrong cause, and
                        # a wrong cause left in the source is read as an explanation.
                        self._heartbeat("RUNNING", "market closed; woke for maintenance")
                    continue
                if self._stop_requested:
                    break
                if self.daily_cycle_count >= self.config.max_daily_cycles:
                    self._heartbeat("BACKING_OFF", "max daily cycles reached")
                    if not self._stop_requested:
                        self.sleep(self._sleep_seconds())
                        # The same beat-on-waking as above, for the same measured reason: this
                        # path waits just as long and is reached on every busy session's afternoon.
                        self._heartbeat("BACKING_OFF", "max daily cycles reached; woke")
                    continue
                round_started = time.monotonic()
                for symbol in self._round_symbols():
                    if self._stop_requested:
                        break
                    self._run_symbol(symbol)
                    if max_cycles is not None and self.cycle_count >= max_cycles:
                        break
                    if self.daily_cycle_count >= self.config.max_daily_cycles:
                        break
                if not self._stop_requested:
                    self.sleep(self._round_sleep_seconds(time.monotonic() - round_started))
            self._heartbeat("STOPPED", "daemon stopped")
            return 0
        finally:
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)
            self._release_lock()

    def stop(self) -> None:
        self._stop_requested = True

    def _cooperative_sleep(self, seconds: float) -> None:
        """Keep normal cadence while bounding how long a stop waits for idle sleep."""
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        deadline = time.monotonic() + seconds
        while not self._stop_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, COOPERATIVE_WAIT_POLL_SECONDS))

    def _should_trade_now(self) -> bool:
        """Is the market open, and does the broker answer?

        The broker call was unguarded and that killed the daemon. Measured 2026-10-08 twice in one
        day: `alpaca_trade_api` raised `ConnectTimeout` on `GET /v2/clock` after a ~15 minute
        network stall, the exception propagated out of `run()`, and systemd's `StartLimitBurst=5`
        then refused to restart - so a transient network problem ended trading for the session with
        one traceback in `daemon.out` and nothing in the journal to say so.

        A clock that cannot be read is not a market that is closed, and treating it as one is the
        safe direction: the round is skipped, the failure is recorded and counted, and the next
        iteration tries again. The backoff grows with consecutive failures so a broker that is down
        does not turn into a tight retry loop against it, and it resets the moment a call succeeds -
        so a single stall costs one skipped round rather than the session.
        """
        if self._stop_requested:
            return False
        try:
            open_now = self.scheduler.should_trade_now()
            self._record_broker(True)
        except Exception as exc:
            self._record_broker(False)
            self.error_count += 1
            self._clock_failures += 1
            delay = min(300, 15 * self._clock_failures)
            self._heartbeat(
                "BACKING_OFF",
                f"broker clock unreadable ({type(exc).__name__}); backing off {delay}s, "
                f"failure {self._clock_failures}: {exc}",
            )
            self._append_event(
                "CYCLE_FAILED",
                status="FAILED",
                message=f"broker clock unreadable, not trading this round: "
                        f"{type(exc).__name__}: {exc}",
            )
            if not self._stop_requested:
                self.sleep(delay)
            return False
        if self._clock_failures:
            # Only worth recording when something had actually gone wrong.
            self._clock_failures = 0
        return bool(open_now)

    def _run_symbol(self, symbol: str) -> None:
        if self._stop_requested:
            return
        # One count per attempted cycle, including exceptions after run_once.
        # Reporting or maintenance failures cannot turn one cycle into two.
        self.cycle_count += 1
        self.daily_cycle_count += 1
        try:
            record = self.loop.run_once(symbol)
            if record is not None:
                self.last_cycle_id = record.cycle_id
            if self._stop_requested:
                if record is None:
                    self.error_count += 1
                return
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
            if self._stop_requested:
                return
            # Whether the model answered, recorded from the one signal that says so.
            #
            # `decision_source == "llm"` means it did. `fallback_policy_engine` means it did not -
            # but that is two different facts, and the first version of this recorded both as
            # "the model did not answer":
            #
            #   - the loop's fail-closed path, when the engine's output could not be used. That is
            #     an outage, and it is what the field is for.
            #   - the policy engine declining because the snapshot's symbol is outside the
            #     strategy's universe. The model was never asked. Nothing is wrong.
            #
            # Recording the second as a failure means `last_model_ok` reads False about a fifth of
            # the time on a widened universe and looks exactly like an outage - a monitor that
            # cries wolf on a correct decline is a monitor that gets ignored. So a decline leaves
            # the flag at its last known value, which is the honest statement: nothing new is
            # known about the model from a cycle that did not ask it.
            self._post_cycle_model_flag(record)
            if self._stop_requested:
                return
            self._post_cycle()
            if not self._stop_requested:
                self._heartbeat("RUNNING", f"completed cycle for {symbol}")
        except Exception as exc:
            self.error_count += 1
            self._heartbeat("BACKING_OFF", f"cycle error for {symbol}: {exc}")

    def _request_stop(self, _signum, _frame) -> None:
        # Signals may interrupt a journal append. Do not reenter IO or its lock;
        # the ordinary run loop writes the final heartbeat after the boundary.
        self._stop_requested = True

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
            "unresolved_client_order_ids": list(report.unresolved_client_order_ids),
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
        if self._stop_requested or self.journal is None or self.reflection_memory is None:
            return
        if self.cycle_count % self.reflect_every == 0:
            evidence = self._latest_evidence_batch()
            if self._stop_requested:
                return
            self._reflect(evidence=evidence)
            if self._stop_requested:
                return
            self._manage_strategy_lifecycle()
            if self._stop_requested:
                return
        if self.cycle_count % self.curriculum_every == 0 and self.curriculum_agent is not None:
            self._curriculum_proposal()

    def _cumulative_cycles_by_strategy(self, records=None) -> tuple[dict[str, int], dict[str, int]]:
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
        for record in self.journal.read_all() if records is None else records:
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
            full_records = self.journal.read_all()
            if self.knowledge_admission is not None:
                self.knowledge_admission.knowledge_library.verify_journal_sources(full_records)
            recent = full_records[-self.config.reflection_window:]
            cumulative_cycles, cumulative_trade_attempts = self._cumulative_cycles_by_strategy(full_records)
            reflection = self.reflection_memory.reflect(
                recent,
                evidence=evidence,
                fills=self._confirmed_fills(),
                seeded_fills=self._confirmed_fill_activities(full_records),
                cumulative_cycles=cumulative_cycles,
                cumulative_trade_attempts=cumulative_trade_attempts,
                full_records=full_records,
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
        self, strategy_id: str, *, _events=None, _latest_payloads=None, _day_up=None, _spec=None
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
            spec = _spec or next((s for s in self.strategy_library.list() if s.strategy_id == strategy_id), None) if self.strategy_library else None
            decisions = offline_validation.collect_decisions(
                self.journal.read_events("COUNTERFACTUAL_EVALUATED")
                if _events is None else _events,
                strategy_id,
                strategy=spec,
            )
            if spec is None:
                decisions = []
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
        payload.update({
            "eligibility_version": ELIGIBILITY_VERSION,
            "strategy_provenance": capture(spec).model_dump(mode="json") if spec else None,
            "source_cycle_ids": [d.cycle_id for d in decisions],
        })
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
        current = {s.strategy_id: capture(s).behavior_digest for s in self.strategy_library.list()} if self.strategy_library else {}
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
            p = valid_provenance(event.payload.get("strategy_provenance"))
            if (event.payload.get("eligibility_version") != ELIGIBILITY_VERSION or p is None
                    or p.strategy_id != event.strategy_id
                    or current.get(event.strategy_id) != p.behavior_digest
                    or (event.payload.get("scored", 0) and not event.payload.get("source_cycle_ids"))):
                latest.pop(event.strategy_id, None)
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
        if self._stop_requested or self.journal is None or self.strategy_library is None:
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
        if self._stop_requested:
            return
        for spec in self.strategy_library.list():
            if self._stop_requested:
                break
            if spec.lifecycle in {"RETIRED"}:
                continue
            self._screen_candidate(
                spec.strategy_id, _events=events, _latest_payloads=latest_payloads,
                _day_up=day_up,
                _spec=spec,
            )

    def _recorded_counterfactual_verdicts(self, events) -> dict[str, tuple]:
        """The verdict last journalled for each cycle, as `{cycle_id: verdict}`."""
        recorded: dict[str, tuple] = {}
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            for row in coerce.field_dict_tuple(payload.get("rows"), "payload.rows"):
                cycle_id = coerce.field_str(row.get("cycle_id"), "row.cycle_id")
                if cycle_id:
                    recorded[cycle_id] = (
                        coerce.field_str(row.get("verdict"), "row.verdict"),
                        row.get("eligibility_version"), row.get("eligibility"), row.get("strategy_provenance"),
                    )
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
            symbols = list(self.config.symbols)
            report = counterfactual.evaluate_symbols(
                self.journal.read_all(),
                symbols=symbols,
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
            if recorded.get(row.cycle_id) != (
                row.verdict, row.eligibility_version, row.eligibility, row.strategy_provenance,
            )
        ]
        if not fresh:
            # Every decision already carries the verdict it has now, so there is
            # nothing to record. The aggregates below are computed over the whole
            # report and would be identical to the last event's, so writing them
            # again would add a row that says only that the pass ran.
            return
        counts = report.counts()
        # Per symbol, because "the screen has nothing to judge" and "the screen has nothing to
        # judge for TLT" are different facts and only the second one is a coverage defect.
        per_symbol: dict[str, dict[str, int]] = {}
        for row in report.rows:
            bucket = per_symbol.setdefault(row.symbol, {})
            bucket[row.verdict] = bucket.get(row.verdict, 0) + 1
        self._append_event(
            "COUNTERFACTUAL_EVALUATED",
            status="SUCCESS",
            message=(
                f"symbols={len(symbols)} "
                f"hold_quality={report.hold_quality()} "
                f"scored={len(report.scored)} pending={report.pending_count} "
                f"gap={report.gap_count} new_or_changed={len(fresh)}/{len(report.rows)}"
            ),
            payload={
                "symbols": symbols,
                "horizon_hours": self.config.counterfactual_horizon_hours,
                "assumed_round_trip_cost_pct": self.config.assumed_round_trip_cost_pct,
                "hold_quality": report.hold_quality(),
                "counts": counts,
                "counts_by_symbol": per_symbol,
                "scored": len(report.scored),
                "pending": report.pending_count,
                "gap": report.gap_count,
                "net_pct_total": round(report.net_pct_total(), 4),
                "rows": [
                    {
                        "cycle_id": row.cycle_id,
                        "action": row.action,
                        "strategy_id": row.strategy_id,
                        "symbol": row.symbol,
                        "decision_symbol": row.decision_symbol,
                        "decision_strategy_id": row.decision_strategy_id,
                        "strategy_provenance": row.strategy_provenance,
                        "eligibility_version": row.eligibility_version,
                        "eligibility": row.eligibility,
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
        """Positions from the latest recorded real snapshot, for curriculum context.

        This reads the journal, not a fresh broker snapshot. Include every nonzero
        signed holding; a recorded flat book must supersede older positions.
        """
        if self.journal is None:
            return []
        records = self.journal.last_n(1)
        if not records:
            return []
        return [
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "market_value": round(position.market_value, 2),
            }
            for position in records[-1].snapshot.positions
            if position.quantity != 0
        ]

    def _recent_rejections(self, limit: int = 6) -> list[dict[str, str]]:
        """Rejected proposals with the reason, newest verdict per strategy id.

        Admission already produces a specific, actionable refusal - it names the
        colliding strategy and the dimensions that must differ - and the journal
        already keeps it. Nothing read it back. The curriculum was therefore
        asked to "propose the next distinct strategy" while being shown only
        trading activity, never its own output, so a refused id looked unused.
        Deduplicated because the same id is reviewed again on every retry and six
        copies of one sentence teaches nothing a single copy does not.

        Dedup by id was not enough, because the ids are not what repeats. Measured
        2026-10-14: the six most recent refusals carried three distinct reason
        strings, and normalising the one number that differs between them left two.
        Five of the six were the same probation-capacity sentence - 1,240 of the
        2,002 characters sent. Grouping them was tried and withdrawn: it only merges
        byte-identical reasons, so it recovered about 400 of 8,192 tokens (5%), in
        exchange for changing the prompt shape the model reads. The budget is fixed
        at CURRICULUM_NUM_CTX instead, which buys four times that.
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
        if self._stop_requested or self.reflection_memory is None or self.curriculum_agent is None:
            return
        try:
            reflection = self.reflection_memory.load()
            if reflection is None:
                return
            strategies = self.strategy_library.list() if self.strategy_library else []
            exploration_summary = self._recent_exploration_summary()
            if self._stop_requested:
                return
            self._record_no_exploration_lesson(exploration_summary)
            # Retry once on transport/connection errors — the LLM endpoint
            # (Ollama) can transiently time out under load. A single retry with
            # a 5-second backstop is sufficient; anything worse should surface
            # as a curriculum failure event for operator attention.
            try:
                prior_rejections = self._recent_rejections()
                open_positions = self._open_book()
                last_price, symbol = self._last_market()
                if self._stop_requested:
                    return
                task = self.curriculum_agent.propose(
                    reflection=reflection,
                    current_strategies=strategies,
                    recent_exploration_summary=exploration_summary,
                    prior_rejections=prior_rejections,
                    guardian_max_position_value=self.config.max_position_value,
                    open_positions=open_positions,
                    last_price=last_price,
                    symbol=symbol,
                )
            except (ConnectionError, TimeoutError, OSError):
                # Keep the actual transport failure, but never start another
                # request after stop, including a stop during preparation.
                if self._stop_requested:
                    raise
                self.sleep(5)
                if self._stop_requested:
                    raise
                prior_rejections = self._recent_rejections()
                open_positions = self._open_book()
                last_price, symbol = self._last_market()
                if self._stop_requested:
                    raise
                task = self.curriculum_agent.propose(
                    reflection=reflection,
                    current_strategies=strategies,
                    recent_exploration_summary=exploration_summary,
                    prior_rejections=prior_rejections,
                    guardian_max_position_value=self.config.max_position_value,
                    open_positions=open_positions,
                    last_price=last_price,
                    symbol=symbol,
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
            if self._stop_requested:
                return
            if task.task_type == "STRATEGY_SPEC" and task.strategy_spec and self.strategy_admission is not None:
                # Give admission the real prices so a TREND_FOLLOW anchored to
                # a fabricated level is rejected instead of entering the library.
                self.strategy_admission.set_market_prices(self._last_known_prices())
                strategy_results = self._strategy_results()
                if self._stop_requested:
                    return
                result = self.strategy_admission.admit(
                    task.strategy_spec, strategy_results
                )
                self._append_event(
                    "STRATEGY_ADMISSION_REVIEWED",
                    status="ACCEPTED" if result.accepted else "REJECTED",
                    message=result.reason,
                    strategy_id=result.strategy_id,
                    task_id=task.task_id,
                    payload={"accepted": result.accepted, "reason": result.reason},
                )
                if self._stop_requested:
                    return
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
        if self._stop_requested or not self._due(self.last_maintenance_at, self.config.maintenance_interval_seconds):
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

        def stage(name: str) -> None:
            """Beat after a stage, so the heartbeat means work advanced and not only that a pass
            began.

            Measured 2026-10-18, and it is why this exists: an idle Sunday daemon sat at heartbeat
            age 1,287s with the message "maintenance in progress" while its journal had produced no
            event for twenty-one minutes. `doctor` called it dead, correctly - nothing in the
            heartbeat or the journal said otherwise - and the whole gate went red with it.

            The cause was arithmetic rather than a hang. The pass beats once, on entry, and then
            runs stages that block on the network; the curriculum stage alone makes two model calls
            at `CURRICULUM_TIMEOUT_S` each, so a single pass could outlive the 900s staleness budget
            without anything being wrong except that nobody had said "still working".

            A beat per stage bounds the gap by the longest *single* stage instead of by the whole
            pass. It keeps the check's teeth: a stage that wedges still goes stale, because the next
            beat only lands when that stage returns.
            """
            self._heartbeat("RUNNING", f"maintenance: {name} done")

        if self._stop_requested:
            return
        self._resolve_unresolved_submissions()
        stage("submission resolution")
        if self._stop_requested:
            return
        self._resolve_pending_fills()
        stage("fill resolution")
        if self._stop_requested:
            return
        evidence = self._latest_evidence_batch()
        if self._stop_requested:
            return
        if self._due(self.last_evidence_at, self.config.evidence_interval_seconds):
            evidence = self._ingest_broker_evidence()
            stage("broker evidence")
            if self._stop_requested:
                return
        # Bound before the branch below: `_manage_strategy_lifecycle` runs after it and
        # takes the same read, and a name bound only inside the `if` would be undefined
        # on the path where there is no journal - which is the path the tests exercise.
        validation_events = None
        self._appended = []
        if self.journal is not None:
            if self.knowledge_admission is not None:
                self.knowledge_admission.knowledge_library.verify_journal_sources(self.journal.read_all())
                if self._stop_requested:
                    return
            self._record_pnl_evidence(evidence)
            if self._stop_requested:
                return
            self._verify_profit_target(evidence)
            if self._stop_requested:
                return
            # One read of the counterfactual ledger, shared. It used to be parsed
            # twice per pass - once here and once in `_screen_all_strategies` - and
            # each read grew with the journal, so the cost of every maintenance pass
            # rose with the number of passes that had already run.
            counterfactual_events = self.journal.read_events("COUNTERFACTUAL_EVALUATED")
            if self._stop_requested:
                return
            # Also read once. `_offline_evidence_by_strategy` parsed every validation
            # event on every pass already, so this adds no scan - it removes one - and
            # the same read is what tells the screen whether a verdict actually moved.
            validation_events = self.journal.read_events("OFFLINE_VALIDATION_COMPLETED")
            if self._stop_requested:
                return
            # Each stage hands what it appended to the next, so a pass acts on its own
            # results. The screen used to run on the read taken before the counterfactuals
            # were recorded, and the lifecycle on the read taken before the screen - so every
            # newly scored decision reached a verdict one pass (15 minutes) late, and a new
            # verdict reached the lifecycle one pass after that.
            mark = len(self._appended)
            self._record_counterfactuals(_events=counterfactual_events)
            if self._stop_requested:
                return
            counterfactual_events = [
                *counterfactual_events,
                *self._appended_since(mark, "COUNTERFACTUAL_EVALUATED"),
            ]
            self._record_calibration_lesson()
            if self._stop_requested:
                return
            self._retire_lessons_without_effect()
            if self._stop_requested:
                return
            mark = len(self._appended)
            self._screen_all_strategies(
                _events=counterfactual_events, _validation=validation_events
            )
            stage("strategy screen")
            if self._stop_requested:
                return
            validation_events = [
                *validation_events,
                *self._appended_since(mark, "OFFLINE_VALIDATION_COMPLETED"),
            ]
        if self.reflection_memory is not None and self._due(self.last_reflection_at, self.config.reflection_interval_seconds):
            self._reflect(evidence=evidence)
            stage("reflection")
            if self._stop_requested:
                return
        if self.curriculum_agent is not None and self.reflection_memory is not None and self._due(self.last_curriculum_at, self.config.curriculum_interval_seconds):
            self._curriculum_proposal()
            stage("curriculum")
            if self._stop_requested:
                return
        self._manage_strategy_lifecycle(_validation=validation_events)
        stage("lifecycle")
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
                "ORDER_FILL_SNAPSHOT",
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

    def _resolve_unresolved_submissions(self) -> int:
        """Retry exact client-ID lookups during maintenance, not only at startup."""
        if self.startup_reconciler is None or self.journal is None:
            return 0
        try:
            unresolved = self.startup_reconciler.resolve_unresolved_submissions()
        except Exception as exc:
            self._append_event(
                "ORDER_RECONCILIATION_FAILED",
                status="FAILED",
                message=f"unresolved submission recovery failed: {exc}",
                payload={"error_type": type(exc).__name__},
            )
            return 0
        return len(unresolved)

    def _confirmed_fills(self) -> dict[str, float]:
        if self.journal is None:
            return {}
        fills: dict[str, float] = {}
        events = self.journal.read_events()
        for event in events:
            if event.event_type != FILL_EVENT:
                continue
            coid = event.payload.get("client_order_id")
            qty = event.payload.get("filled_quantity")
            if isinstance(coid, str) and isinstance(qty, (int, float)) and qty > 0:
                fills[coid] = max(fills.get(coid, 0.0), float(qty))
        for coid, event in latest_fill_snapshots(events).items():
            quantity = event.payload.get("cumulative_filled_quantity", event.payload.get("filled_quantity"))
            if isinstance(quantity, (int, float)) and quantity > 0:
                fills[coid] = float(quantity)
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

    def _confirmed_fill_activities(self, records=None):
        if self.journal is None:
            return []
        return confirmed_fill_activities(self.journal, self.journal.read_all() if records is None else records)

    def _record_pnl_evidence(self, evidence: BrokerEvidenceBatch | None) -> None:
        if self.journal is None:
            return
        report = DeterministicEvaluator().evaluate(
            self.journal.read_all(),
            evidence=evidence,
            fills=self._confirmed_fills(),
            seeded_fills=self._confirmed_fill_activities(),
            owner_fills=owner_fill_activities(self.journal),
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
            owner_fills=owner_fill_activities(self.journal),
        )
        pnl = report.pnl
        # Broker account history cannot establish the agent's capital or return.
        status = "unproven"
        # Only write a journal event when the target status transitions, so a
        # routine "unproven" reading does not flood the log.
        if status == self._last_profit_target_status:
            return
        self._last_profit_target_status = status
        self._append_event(
            "PROFIT_TARGET_CHECKED",
            status="SKIPPED",
            message=f"profit target {status}",
            payload={
                "target_daily_return_pct": self.config.profit_target_return_pct,
                "pnl_evidence": report.pnl_evidence,
                "satisfied": False,
                "status": status,
                "comparison_status": "UNKNOWN",
                "comparison_reason": "agent allocation, cash flows and aligned rolling equity history are unavailable",
                "account_return_pct": None if pnl is None else pnl.account_return_pct,
                "account_return_scope": "account_only_diagnostic",
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
        self.knowledge_admission.knowledge_library.verify_journal_sources(records)
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
                "computed by autopoiesis.calibration over the same counterfactual verdicts "
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
        return self.reflection_memory.strategy_results(reflection, self.strategy_library.list() if self.strategy_library else [])

    def _manage_strategy_lifecycle(self, *, _validation=None) -> None:
        if self.lifecycle_manager is None or self.strategy_library is None or self.reflection_memory is None:
            return
        reflection = self.reflection_memory.load()
        if reflection is None:
            return
        results = self.reflection_memory.strategy_results(reflection, self.strategy_library.list())
        # Phase 6: promotion is evidence-gated, so the lifecycle review is given the
        # offline validation verdict for each strategy. Without this the gate would
        # see "no evidence" for everyone and block every promotion permanently -
        # a gate that cannot be satisfied is not a gate, it is a freeze.
        evidence = self._offline_evidence_by_strategy(_validation=_validation)
        decisions = self.lifecycle_manager.review(self.strategy_library.list(), results, evidence)
        decisions.extend(self._reconsider_paused(results, evidence))
        decisions.extend(self._drifted_reference_retirements())
        decisions.extend(self._unproven_accumulation_retirements())
        for decision in decisions:
            self._apply_lifecycle_decision(decision)

    def _drifted_reference_retirements(self) -> list:
        """Retire a servable strategy that admission would refuse today for a drifted reference.

        The plan's P1 item, second half: "make the lifecycle able to retire a strategy whose
        reference has drifted from the market rather than waiting for a human". Measured three
        times now - TLT, XLF and SPY were each drained one share per cycle by a strategy whose
        trigger band emitted a single action forever, and each time a human paused it. The
        pause stops that instance and does not stop the next one.

        The predicate is admission's own, not a second number: a spec whose `reference_price`
        has drifted far enough that the gate would refuse it today is a spec that should not
        keep serving. Two answers to "is this reference usable" is how they start disagreeing,
        which is the mistake `VIX_STALE_AFTER_DAYS` was extracted to avoid in this same work.

        Only PROBATION and ACTIVE. PAUSED and RETIRED are already out of service, and a
        BASELINE holds no reference.
        """
        admission = getattr(self, "strategy_admission", None)
        if admission is None or self.strategy_library is None:
            return []
        out = []
        for spec in self.strategy_library.list():
            if spec.lifecycle not in {"PROBATION", "ACTIVE"} or not spec.enabled:
                continue
            reason = admission._reference_price_rejection_reason(spec)
            if reason is None:
                continue
            out.append(
                StrategyLifecycleDecision(
                    spec, "RETIRED",
                    f"reference drifted from the market: {reason}",
                )
            )
        return out

    def _unproven_accumulation_retirements(self) -> list:
        """Retire a BUY-only strategy that has spent its whole declared position budget.

        Measured 2026-10-19. `fixed-size-buy-bil-001` was serving as an incumbent, buying one share
        of a T-bill ETF every few cycles, at $19,962 of its own $25,000 `max_position_value`, with
        26 recorded cycles and no realized outcome anywhere - and nothing in the lifecycle could end
        it. The plan asked for exactly this decision to be the system's rather than a human's, which
        is what `_drifted_reference_retirements` above does for a drifted reference.

        Why a BUY-only spec is the right subject and not merely a convenient one: a spec whose only
        declared action is BUY cannot close a lot, so it cannot produce a realized outcome *by
        construction*. No amount of waiting turns it into evidence, and the screen that judges
        strategies on decision quality keeps judging an experiment that has no result to reach.
        Waiting is not a policy for a strategy that cannot finish.

        The trigger is the strategy's *own* declared budget rather than a number invented here.
        `max_position_value` is the limit the spec already carries and the Guardian already
        enforces, so "the budget is spent" needs no second opinion about how much is too much - the
        same reason the reference check above reuses admission's predicate instead of a threshold of
        its own. Two answers to "how much may this hold" is how they start disagreeing.

        What this does not do is sell. This system's exits are strategies, and BIL already has two -
        `trend-follow-bil-001` and `trend-follow-bil-sell-001` both declare SELL and both sit
        outside their trigger band. With the accumulator out of service the selector's only
        actionable BIL candidate is an exit, so every BIL cycle becomes a decision about leaving
        rather than about adding. That is the whole of the mechanism: the system ends the
        experiment at its own budget and hands the symbol to its own exit.
        """
        if self.strategy_library is None:
            return []
        # A recorded market value that is not a number is skipped rather than coerced: this rule
        # retires strategies, and retiring one on a guessed position size is worse than not firing.
        book: dict[str, float] = {}
        for row in self._open_book():
            raw = row.get("market_value")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                book[str(row.get("symbol") or "").upper()] = float(raw)
        if not book:
            return []
        out = []
        for spec in self.strategy_library.list():
            if spec.lifecycle not in {"PROBATION", "ACTIVE"} or not spec.enabled:
                continue
            if str(spec.parameters.get("action") or "").upper() != "BUY":
                continue
            budget = spec.max_position_value
            if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget <= 0:
                continue
            held = max((book.get(str(symbol).upper(), 0.0) for symbol in spec.symbols), default=0.0)
            if held < budget:
                continue
            out.append(
                StrategyLifecycleDecision(
                    spec, "RETIRED",
                    f"declared position budget spent: {held:.2f} of its own {budget:.2f} "
                    "max_position_value, with no realized outcome and a BUY-only spec that cannot "
                    "close a lot to produce one",
                )
            )
        return out

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
            if spec.probation_restarts >= MAX_PROBATION_RESTARTS:
                # The bound that ends the oscillation. The loop was here, not in the lifecycle
                # rules: a strategy paused for producing no exploration evidence is not served
                # again, so its reflection window empties, `review` returns no ruling for it
                # (`result.cycles <= 0`), and the guard below read that absence as permission -
                # "the rules would not pause it again" is true of a ruling nobody made. It was
                # therefore reopened every 3 days, forever.
                #
                # Measured on the live library 2026-10-12: `trend-follow-buy-010` had been
                # reopened 319 times. It had run 151 selected cycles that day without
                # submitting one order, and its probation slot was still holding the admission
                # gate shut for the whole session - 15 curriculum proposals, none admissible,
                # 151 of 155 probation cycles spent on a strategy that was being judged
                # incapable of producing an outcome. The retry count is now bounded by the
                # same `MAX_PROBATION_RESTARTS`, so a strategy is looked at again a few times
                # under rules that may since have been replaced and then left alone. Nothing
                # here can promote it or let it trade.
                continue
            reopened = spec.model_copy(update={
                "lifecycle": "PROBATION",
                # A count of re-admissions, which is what the field documents and what the
                # bounded retry in the lifecycle gate is measured against.
                #
                # It was `served // budget + 1`, derived from lifetime service instead. The
                # two agree only on a candidate served fewer than one budget, and diverge
                # without limit after that: a strategy with 692 cumulative cycles was given
                # 319 restarts, which put its probation budget at 4160 cycles and made it
                # permanently unreachable - and it was being reopened over and over, since
                # reopening is this method and nothing here was stopping it. Counting the
                # returns keeps the number small, which is the point of a retry count, and it
                # is what makes the bound mean a bounded number of chances.
                "probation_restarts": spec.probation_restarts + 1,
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

    def _round_symbols(self) -> list[str]:
        """The symbols this round looks at: the configured ones, then (under a universe wider than
        the allowlist) everything the account holds, then up to `attention_slots` of the broker's
        most-active liquid names. Any failure to ask the broker falls back to what is configured,
        so a data problem narrows the round rather than stopping it."""
        symbols = list(self.config.symbols)
        if self.config.universe == "allowlist":
            return symbols
        gateway: Any = getattr(self.loop, "data_gateway", None)
        if gateway is None:
            return symbols
        seen = set(symbols)
        held_symbols: set[str] = set()
        try:
            for held in gateway.held_symbols():
                held_symbols.add(str(held).upper())
                if held not in seen:
                    symbols.append(held)
                    seen.add(held)
        except Exception as exc:
            self._append_event("CYCLE_FAILED", status="FAILED", message=f"could not list held symbols: {exc}")
        if self.config.universe == "tradable" and self.config.attention_slots > 0:
            try:
                # The candidate list is wider than the slots, because the model is choosing
                # from it rather than receiving the answer. Asking for exactly
                # `attention_slots` and then choosing among them would be a choice with one
                # option.
                candidates = gateway.attention_symbols(
                    max(self.config.attention_slots, ATTENTION_CANDIDATES),
                    min_price=self.config.min_price, exclude=frozenset(seen),
                )
            except Exception as exc:
                self._append_event("CYCLE_FAILED", status="FAILED", message=f"could not pick attention symbols: {exc}")
                return symbols
            choice = attention.select_attention(
                candidates,
                k=self.config.attention_slots,
                transport=self.attention_transport,
                context={"held": len(seen), "slots": self.config.attention_slots},
            )
            # Recorded every round, including when the model did not choose. The plan's
            # 24-hour evaluation compares chosen symbols against unchosen ones, and it
            # cannot do that from a record that only keeps the rounds the model won.
            self._append_event(
                "ATTENTION_SELECTED", status="SUCCESS",
                message=f"watchlist from {choice.source}: {', '.join(choice.symbols) or 'none'}",
                payload={
                    "source": choice.source,
                    "symbols": list(choice.symbols),
                    "reasons": dict(choice.reasons),
                    "candidates": list(candidates),
                    "note": choice.note,
                },
            )
            symbols += [s for s in choice.symbols if s not in seen]
        return self._prioritise_round(symbols, held_symbols)

    def _prioritise_round(self, symbols: list[str], held_symbols: set[str]) -> list[str]:
        """Decide what can change something every round; rotate the rest.

        This is the account holder's "any symbol may appear, but the ones that meet the bar get
        special attention", with the bar taken from the system's own records rather than invented:
        a symbol qualifies if a serving strategy covers it or the account holds it, because in
        either case a decision there can change something. Everything else is still a symbol the
        model chose or the operator configured, so it is not dropped - it rotates, which keeps it
        in front of the model without letting four picks set the cadence of the five that matter.
        """
        served: set[str] = set()
        library = getattr(self, "strategy_library", None)
        if library is not None:
            try:
                for spec in library.list():
                    if spec.enabled and spec.lifecycle not in {"PAUSED", "RETIRED"}:
                        served.update(str(symbol).upper() for symbol in (spec.symbols or ()))
            except Exception:
                # An unreadable library is a reason to treat nothing as served, not to guess: the
                # rotation below then applies to everything, which shortens the round rather than
                # lengthening it.
                served = set()
        priority: list[str] = []
        rest: list[str] = []
        for symbol in symbols:
            key = str(symbol).upper()
            (priority if (key in served or key in held_symbols) else rest).append(symbol)
        if not priority:
            # Nothing is served and nothing is held: there is nothing to protect, so the round is
            # whatever the library and the model asked for. Rotation would hide symbols for no gain.
            return symbols
        if len(rest) > DISCOVERY_SYMBOLS_PER_ROUND:
            start = self.cycle_count % len(rest)
            rest = [rest[(start + offset) % len(rest)] for offset in range(DISCOVERY_SYMBOLS_PER_ROUND)]
        return priority + rest

    def _round_sleep_seconds(self, elapsed: float) -> int:
        """The rest of the trading interval, counted from the start of the round.

        With one symbol a round took about a minute and a fixed interval after it was close
        enough. Managing six symbols a round takes about eight minutes of model time, so a
        fixed 300s after it put each symbol thirteen minutes apart. A longer sleep (the market
        is closed) is left alone, and a 30s floor keeps the model and the broker from being
        hit back to back when a round overruns.
        """
        planned = self._sleep_seconds()
        if planned > self.config.daemon_interval_seconds:
            return planned
        return max(30, int(planned - elapsed))

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

    def _now_or_none(self):
        """The daemon's clock, or None when a constructed instance has not been given one.

        `AgentDaemon.__new__` is how this project's tests build an instance without running
        `__init__`, and several of them set only the fields a particular path needs. Recording a
        dependency's outcome must not then raise out of the trading loop, which is the one path
        that has to keep running; an unrecorded outcome is a missing field, and a crash in a
        recorder would take the cycle with it.
        """
        clock = getattr(self, "now", None)
        if not callable(clock):
            return None
        try:
            return clock()
        except Exception:
            return None

    def _record_broker(self, ok: bool) -> None:
        """When the broker last answered, and whether it did.

        Recorded rather than inferred from the cycle count, because a daemon that is alive and not
        cycling is ambiguous between "market closed" and "broker unreachable" and those need
        different people. The clock check is the cheapest real broker call the daemon makes, so it
        is the honest sample of reachability.
        """
        stamp = self._now_or_none()
        if stamp is not None:
            self._last_broker_at = stamp
        self._last_broker_ok = ok

    def _record_model(self, ok: bool) -> None:
        """When the model last answered, and whether it did.

        The decision path already fails closed to a HOLD when the engine raises, so a model outage
        looks exactly like a market with nothing to do. Recording the outcome is what tells them
        apart.
        """
        stamp = self._now_or_none()
        if stamp is not None:
            self._last_model_at = stamp
        self._last_model_ok = ok

    def _post_cycle_model_flag(self, record) -> None:
        """Record whether the model answered, from the one signal that says so.

        `decision_source == "llm"` means it did. `fallback_policy_engine` means it did not - and
        that is two different facts:

        - the loop's fail-closed path, when the engine's output could not be used. An outage, and
          what the flag is for.
        - the policy engine declining because the snapshot's symbol is outside the strategy's
          universe. The model was never asked. Nothing is wrong.

        Recording the second as a failure makes `last_model_ok` read False about a fifth of the
        time on a widened universe and look exactly like an outage. So a decline leaves the flag at
        its last known value, which is the honest statement: a cycle that did not ask the model
        tells us nothing new about it.
        """
        decision = getattr(record, "decision", None)
        if decision is None:
            return
        source = getattr(decision, "decision_source", None)
        rationale = getattr(decision, "rationale", "")
        if source == "llm":
            self._record_model(True)
        elif rationale == FAIL_CLOSED_RATIONALE:
            self._record_model(False)
        elif LLM_UNAVAILABLE_MARKER in rationale:
            # The model was asked and did not produce a usable answer. `_fallback` marks
            # every such decision with this marker and carries the cause after it - a
            # timeout, a refused connection, a response with no JSON object in it.
            #
            # Without this branch the flag kept its last known value, so a parse failure
            # was invisible to the monitor: reported 2026-10-14 as 23% of decisions on
            # 10-12 and 9% on 10-13, and unverifiable from the journal, because no event
            # recorded a decision-path failure at all. A failure the system does not
            # count is a failure nobody fixes.
            self._record_model(False)

    def _journal_recency(self) -> tuple[datetime | None, float | None]:
        """The newest cycle in the journal, and its age, read from the record itself.

        Read once per heartbeat from the tail of the record rather than tracked in memory: a
        counter kept in memory resets on restart and then claims nothing has run, which is the
        failure this field exists to make visible. The read is the same one `doctor`'s journal
        recency check does, so the two cannot disagree about what the record says.
        """
        journal = getattr(self, "journal", None)
        if journal is None:
            return None, None
        try:
            records = journal.read_all()
        except Exception:
            # A record that cannot be read has no recency to report. The heartbeat still carries a
            # status, an error count and a pid; a missing recency is a missing field rather than a
            # claim that nothing ran.
            return None, None
        stamps = sorted(
            r.snapshot.timestamp for r in records if getattr(r.snapshot, "timestamp", None)
        )
        if not stamps:
            return None, None
        latest = stamps[-1]
        clock = self._now_or_none()
        if clock is None:
            # The record's newest cycle is a fact the record holds; its age needs a clock, and a
            # constructed daemon without one reports the fact and not the age rather than raising.
            return latest, None
        return latest, max(0.0, (clock - latest).total_seconds())

    def _heartbeat(self, status: DaemonStatus, message: str) -> None:
        last_at, last_age = self._journal_recency()
        payload = HeartbeatPayload(
            pid=os.getpid(),
            status=status,
            timestamp=self.now(),
            cycle_count=self.cycle_count,
            error_count=self.error_count,
            last_cycle_id=self.last_cycle_id,
            last_cycle_at=last_at,
            last_cycle_age_seconds=last_age,
            last_broker_at=self._last_broker_at,
            last_broker_ok=self._last_broker_ok,
            last_model_at=self._last_model_at,
            last_model_ok=self._last_model_ok,
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
