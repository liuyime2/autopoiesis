"""Shadow orders are not fills, and the system must never believe otherwise.

Phase 5 of the objective asks for shadow trading as its own stage. Its purpose is to
run the real decision path without placing real orders, so that "would this have
made money" can be answered before risking anything.

The dangerous failure is not shadow not working. It is shadow working *too well*:
if a shadowed order ever reached the PnL ledger as an executed trade, the account
would book profits from money that was never risked, and an unvalidated path would
show up as the best one on record. That would make the whole loop lie. So the
negative property is what these tests are for.

A second property matters as much: shadow must not be a simplified decision path. If
shadow approved trades the Guardian blocks, or used different data, it would be
grading a different system from the one that will trade, and would pass while the
real path failed.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from min_agent.evaluator import DeterministicEvaluator
from min_agent.journal import JsonlJournal
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)
from min_agent.shadow import SHADOW_EVENT, ShadowExecutor

TS = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _decision(action="BUY", quantity=5, strategy_id="s1"):
    return TradeDecision(
        symbol="SPY", action=action, quantity=quantity, confidence=0.8,
        rationale="test", strategy_id=strategy_id, decision_source="llm",
    )


def _cycle(execution, action="BUY", quantity=5, price=100.0):
    return CycleRecord(
        cycle_id="c1",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=TS, market_open=True, last_price=price,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action=action, quantity=quantity, confidence=0.8,
            rationale="test", strategy_id="s1",
        ),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=execution,
        strategy_id="s1",
    )


# --------------------------------------------------------------------------
# the negative property
# --------------------------------------------------------------------------

def test_a_shadow_order_reports_a_distinct_status_and_no_order_id():
    """Distinct status so no branch can treat it as executed; no order_id so no
    reconciliation pass can match it to a broker order."""
    result = ShadowExecutor().execute(_decision(), GuardianResult(approved=True, reason="ok"),
                                     cycle_id="c1")

    assert result.status == "SHADOWED"
    assert result.order_id is None
    assert result.filled_quantity == 0
    assert result.filled_avg_price is None, (
        "a shadow order has no fill price; inventing one would mean it was priced "
        "by something other than the real path"
    )


def test_a_journal_of_shadow_orders_produces_zero_realized_pnl(tmp_path):
    """The property that actually matters. Not "the evaluator ignores shadow", but
    "the account says zero money was made"."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    shadow = ShadowExecutor(journal=journal)
    for i in range(4):
        result = shadow.execute(
            _decision(action="BUY" if i % 2 == 0 else "SELL"),
            GuardianResult(approved=True, reason="ok"), cycle_id=f"c{i}",
        )
        journal.append(_cycle(result, action="BUY" if i % 2 == 0 else "SELL"))

    report = DeterministicEvaluator().evaluate(journal.read_all())

    assert report.shadowed_orders == 4, "shadow orders must still be counted"
    assert report.submitted_orders == 0
    pnl = report.pnl
    # `pnl` is an evidence object and is legitimately present; what must be absent
    # is every monetary value in it. An earlier version of this assertion checked
    # `pnl is None` and failed for the right reason with the wrong expectation.
    assert pnl.account_realized_or_reported_pnl is None, (
        f"shadow orders booked {pnl.account_realized_or_reported_pnl!r} of realized "
        "PnL; that is profit from money that was never risked"
    )
    assert pnl.account_return_pct is None
    assert pnl.strategy_realized_pnl == {}
    assert pnl.strategy_fees == {}
    assert pnl.unattributed_pnl is None
    assert pnl.closed_lot_count == 0
    assert pnl.linked_fill_count == 0
    assert report.pnl_evidence in {
        "missing_fill_price_and_broker_activity",
        "broker_fills_matched_without_portfolio_history",
    }, (
        f"unexpected PnL evidence {report.pnl_evidence!r}: no broker activity exists "
        "for shadow orders, so anything claiming verified broker evidence is wrong"
    )


def test_shadow_orders_are_counted_so_the_stage_is_visible(tmp_path):
    """A stage that produces no signal is indistinguishable from a stage that is
    broken. The count is how anyone can tell the two apart."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    shadow = ShadowExecutor(journal=journal)
    result = shadow.execute(_decision(), GuardianResult(approved=True, reason="ok"),
                            cycle_id="c1")
    journal.append(_cycle(result))

    report = DeterministicEvaluator().evaluate(journal.read_all())
    assert report.shadowed_orders == 1
    assert len(shadow.intents) == 1


# --------------------------------------------------------------------------
# the decision path is the real one
# --------------------------------------------------------------------------

def test_shadow_honours_a_guardian_rejection_exactly_as_the_real_executor_does():
    """A shadow stage that approved orders the Guardian blocks would report phantom
    edge on trades that could never happen."""
    result = ShadowExecutor().execute(
        _decision(), GuardianResult(approved=False, reason="exposure cap"), cycle_id="c1",
    )

    assert result.status == "REJECTED"
    assert "exposure cap" in result.message
    assert result.order_id is None


def test_shadow_never_contacts_a_broker():
    """It is constructed with no client at all, so there is nothing to contact. A
    shadow executor holding a broker client is a shadow executor that can trade."""
    shadow = ShadowExecutor()
    assert not hasattr(shadow, "client")
    assert not hasattr(shadow, "base_url")


def test_shadow_holds_are_not_shadow_orders():
    """Counting holds as shadow orders would inflate the stage's activity and hide
    that nothing was actually proposed."""
    result = ShadowExecutor().execute(
        _decision(action="HOLD", quantity=0),
        GuardianResult(approved=True, reason="ok"), cycle_id="c1",
    )
    assert result.status == "SKIPPED"
    assert result.client_order_id is None


def test_the_shadow_client_order_id_is_deterministic_and_matches_the_real_shape():
    """So "the shadow said BUY and the live run also said BUY" is a checkable
    statement rather than an impression."""
    a = ShadowExecutor().execute(_decision(), GuardianResult(approved=True, reason="ok"),
                                 cycle_id="c1")
    b = ShadowExecutor().execute(_decision(), GuardianResult(approved=True, reason="ok"),
                                 cycle_id="c1")
    c = ShadowExecutor().execute(_decision(), GuardianResult(approved=True, reason="ok"),
                                 cycle_id="c2")

    assert a.client_order_id == b.client_order_id
    assert a.client_order_id != c.client_order_id
    assert a.client_order_id.startswith("shadow-")


# --------------------------------------------------------------------------
# durability
# --------------------------------------------------------------------------

def test_a_shadow_intent_is_journalled_durably(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    ShadowExecutor(journal=journal).execute(
        _decision(), GuardianResult(approved=True, reason="ok"), cycle_id="c1",
    )

    events = journal.read_events(SHADOW_EVENT)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["action"] == "BUY"
    assert payload["quantity"] == 5
    assert payload["guardian_approved"] is True
    assert payload["shadowed"] is True
    assert events[0].cycle_id == "c1"


def test_a_journal_failure_does_not_turn_a_shadow_cycle_into_a_real_one():
    """The worst case is an unrecorded shadow intent, which shows up as a gap. The
    unacceptable case is a cycle that falls through to a real broker call."""

    class Broken:
        def append_event(self, **kwargs):
            raise RuntimeError("disk full")

    shadow = ShadowExecutor(journal=Broken())
    result = shadow.execute(_decision(), GuardianResult(approved=True, reason="ok"),
                            cycle_id="c1")

    assert result.status == "SHADOWED", "a journal error must not change the outcome"
    assert result.order_id is None
    assert shadow.journal_failures, (
        "a swallowed journal error must be recorded, not discarded - the first "
        "version ate a ValidationError silently and the stage looked wired while "
        "journalling nothing"
    )


def test_a_shadow_executor_with_a_healthy_journal_records_no_failure(tmp_path):
    """The counter must mean something: a working journal leaves it empty."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    shadow = ShadowExecutor(journal=journal)
    shadow.execute(_decision(), GuardianResult(approved=True, reason="ok"), cycle_id="c1")

    assert shadow.journal_failures == []
    assert len(journal.read_events(SHADOW_EVENT)) == 1


def test_shadow_with_no_journal_still_produces_a_correct_result():
    """Shadow must be usable before the journal exists, and must not require it."""
    result = ShadowExecutor().execute(
        _decision(), GuardianResult(approved=True, reason="ok"), cycle_id="c1",
    )
    assert result.status == "SHADOWED"


# --------------------------------------------------------------------------
# end to end through the real loop
# --------------------------------------------------------------------------

def test_the_real_loop_with_a_shadow_sink_reaches_no_broker(tmp_path):
    """The stage only means something if the *real* loop is what runs. This drives
    TradingLoop end to end with a broker client that raises on any use, so reaching
    the broker is a test failure rather than an untested assumption."""
    from min_agent.guardian import Guardian
    from min_agent.loop import TradingLoop

    class Tripwire:
        def __getattr__(self, name):
            raise AssertionError(
                f"shadow trading touched the broker: {name} was called"
            )

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    shadow = ShadowExecutor(journal=journal)

    class Gateway:
        symbols = ("SPY",)
        def snapshot(self, symbol):
            from min_agent.models import AccountSnapshot
            # A current timestamp. With the fixed test timestamp the Guardian
            # rejected the order as a stale snapshot and the cycle came back
            # REJECTED - correct behaviour, and a reminder that the Guardian
            # above shadow is the real one and not bypassed.
            return DataSnapshot(
                symbol="SPY",
                timestamp=datetime.now(timezone.utc) - timedelta(seconds=1),
                market_open=True, last_price=100.0,
                source="alpaca",
                account=AccountSnapshot(
                    equity=100_000, cash=100_000, buying_power=100_000,
                    portfolio_value=100_000, daily_loss=0,
                ),
            )

    class Engine:
        def decide(self, snapshot, **kwargs):
            return _decision()

    loop = TradingLoop(
        data_gateway=Gateway(),
        decision_engine=Engine(),
        guardian=Guardian(
            allowlist={"SPY"}, max_position_value=5_000, max_daily_loss=500,
        ),
        executor=shadow,
        journal=journal,
        mode="paper",
    )
    record = loop.run_once("SPY")

    assert record is not None
    assert record.guardian.approved, "the real Guardian must have been consulted"
    assert record.execution.status == "SHADOWED"
    assert record.execution.order_id is None
    assert len(journal.read_events(SHADOW_EVENT)) == 1
    report = DeterministicEvaluator().evaluate(journal.read_all())
    assert report.shadowed_orders == 1
    assert report.submitted_orders == 0
    assert report.pnl.strategy_realized_pnl == {}


def test_the_sink_choice_is_driven_only_by_the_config_flag():
    """One switch, one place. A shadow executor appearing anywhere else would be a
    second execution truth."""
    from dataclasses import replace

    from min_agent.cli import _execution_sink
    from min_agent.config import AgentConfig

    cfg = AgentConfig(
        mode="paper", symbols=("SPY",), allowlist=frozenset({"SPY"}),
        alpaca_api_key="k", alpaca_secret_key="s",
        alpaca_base_url="https://paper-api.alpaca.markets",
        ollama_base_url="http://localhost:11434", model="q", gpu_devices="0",
        journal_path=Path("/tmp/shadow-sink-selection-does-not-write"),
        max_position_value=5_000, max_daily_loss=500,
    )
    journal = JsonlJournal(cfg.journal_path)

    from min_agent.executor import AlpacaPaperExecutor
    real = _execution_sink(cfg, object(), journal)
    shadowed = _execution_sink(replace(cfg, shadow=True), object(), journal)

    assert isinstance(real, AlpacaPaperExecutor)
    assert isinstance(shadowed, ShadowExecutor)
    assert not isinstance(shadowed, AlpacaPaperExecutor)
