"""Duplicate-order safety, end to end through the REAL executor, journal projection and reconciler.

Only the broker client is a fake (SYNTHETIC); every decision about whether to submit again is made
by production code. Each test is a failure the plan (G1 1.1) names: a crash after the write-ahead
intent, a submit that raises, a response with the wrong identity, a half-written journal line, and
recovery by client order id.
"""
from __future__ import annotations

from types import SimpleNamespace

from autopoiesis.executor import AlpacaPaperExecutor
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import GuardianResult, TradeDecision
from autopoiesis.order_reconciler import OrderReconciler
from autopoiesis.submission_state import project_submission_state

PAPER = "https://paper-api.alpaca.markets"
APPROVED = GuardianResult(approved=True, reason="approved")


def decision(qty=1, strategy="s1"):
    return TradeDecision(symbol="XLE", action="BUY", quantity=qty, confidence=0.8, rationale="t", strategy_id=strategy)


class FakeBroker:
    """SYNTHETIC broker: records submissions; behaviour is switched per test."""

    def __init__(self):
        self.submitted: list[dict] = []
        self.mode = "ok"
        self.by_client_id: dict[str, SimpleNamespace] = {}

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def submit_order(self, **kw):
        self.submitted.append(kw)
        coid = kw["client_order_id"]
        order = SimpleNamespace(
            id=f"oid-{len(self.submitted)}", client_order_id="someone-else" if self.mode == "wrong_id" else coid,
            status="accepted", filled_qty=0, filled_avg_price=None, submitted_at=None, filled_at=None,
        )
        # The broker has the order whether or not the response reaches us: that is the case the
        # write-ahead intent exists for.
        self.by_client_id[coid] = SimpleNamespace(**{**vars(order), "client_order_id": coid})
        if self.mode == "raise":
            raise ConnectionError("response lost")
        return order

    def get_order_by_client_order_id(self, coid):
        return self.by_client_id.get(coid)

    def get_orders(self, *a, **k):
        return []


def make(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    assert journal.archive_manifest_status()["trusted"]  # a fresh journal, as the daemon's is at first use
    broker = FakeBroker()
    return journal, broker, AlpacaPaperExecutor(client=broker, base_url=PAPER, journal=journal)


def test_a_clean_submission_is_acknowledged_and_does_not_block_the_next_one(tmp_path):
    journal, broker, executor = make(tmp_path)
    first = executor.execute(decision(), APPROVED, cycle_id="c1")
    second = executor.execute(decision(), APPROVED, cycle_id="c2")
    assert (first.status, second.status) == ("SUBMITTED", "SUBMITTED") and len(broker.submitted) == 2
    assert project_submission_state(journal).unresolved == {}


def test_a_crash_after_the_intent_blocks_every_later_submission_across_a_restart(tmp_path):
    journal, broker, executor = make(tmp_path)
    # Simulate the process dying between the write-ahead intent and the broker call: write the
    # intent exactly as the executor does, then "restart" with a new executor on the same file.
    from autopoiesis.executor import _submission_event

    identity = {
        "client_order_id": executor.client_order_id(decision(), cycle_id="c1"), "cycle_id": "c1", "symbol": "XLE",
        "action": "BUY", "side": "buy", "quantity": 1.0, "order_type": "market", "time_in_force": "day",
        "strategy_id": "s1", "paper_only": True,
    }
    journal.append_event(_submission_event("ORDER_SUBMISSION_INTENT", identity, status="ACCEPTED", message="intent"))

    restarted = AlpacaPaperExecutor(client=broker, base_url=PAPER, journal=JsonlJournal(tmp_path / "journal.jsonl"))
    result = restarted.execute(decision(qty=5, strategy="another"), APPROVED, cycle_id="c2")
    assert result.status == "SKIPPED" and "unresolved broker order" in result.message
    assert broker.submitted == [], "nothing may reach the broker while an intent is unresolved"


def test_a_submit_that_raises_is_unknown_and_blocks_a_retry(tmp_path):
    _journal, broker, executor = make(tmp_path)
    broker.mode = "raise"
    first = executor.execute(decision(), APPROVED, cycle_id="c1")
    broker.mode = "ok"
    retry = executor.execute(decision(), APPROVED, cycle_id="c2")
    assert first.status == "SUBMISSION_UNKNOWN"
    assert retry.status == "SKIPPED" and len(broker.submitted) == 1, "the retry must not double the order"


def test_a_response_with_the_wrong_identity_is_unknown_not_a_success(tmp_path):
    journal, broker, executor = make(tmp_path)
    broker.mode = "wrong_id"
    result = executor.execute(decision(), APPROVED, cycle_id="c1")
    assert result.status == "SUBMISSION_UNKNOWN"
    assert len(project_submission_state(journal).unresolved) == 1


def test_a_truncated_final_journal_line_stops_the_projection_and_so_the_submission(tmp_path):
    _journal, broker, executor = make(tmp_path)
    executor.execute(decision(), APPROVED, cycle_id="c1")
    path = tmp_path / "journal.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event_type": "ORDER_SUBMISSION_INTENT", "payload": {"client_order_id": "autop')  # half a write
    fresh = AlpacaPaperExecutor(client=broker, base_url=PAPER, journal=JsonlJournal(path))
    before = len(broker.submitted)
    result = fresh.execute(decision(qty=2), APPROVED, cycle_id="c2")
    assert result.status == "SKIPPED" and len(broker.submitted) == before, result.message


def test_recovery_by_client_order_id_unblocks_exactly_one_unknown(tmp_path):
    journal, broker, executor = make(tmp_path)
    broker.mode = "raise"
    executor.execute(decision(), APPROVED, cycle_id="c1")          # the broker actually received it
    broker.mode = "ok"
    assert executor.execute(decision(), APPROVED, cycle_id="c2").status == "SKIPPED"

    # Startup reconciliation looks the order up by its client id and resolves the unknown.
    OrderReconciler(client=broker, journal=journal).resolve_unresolved_submissions()
    assert project_submission_state(journal).unresolved == {}
    again = executor.execute(decision(), APPROVED, cycle_id="c3")
    assert again.status == "SUBMITTED" and len(broker.submitted) == 2


def test_an_unknown_the_broker_cannot_find_stays_blocking(tmp_path):
    journal, broker, executor = make(tmp_path)
    broker.mode = "raise"
    executor.execute(decision(), APPROVED, cycle_id="c1")
    broker.by_client_id.clear()                                    # the broker has no record of it
    broker.mode = "ok"
    OrderReconciler(client=broker, journal=journal).resolve_unresolved_submissions()
    assert len(project_submission_state(journal).unresolved) == 1
    assert executor.execute(decision(), APPROVED, cycle_id="c2").status == "SKIPPED"


def test_the_market_closed_at_submit_time_leaves_no_intent_behind(tmp_path):
    journal, broker, executor = make(tmp_path)
    broker.get_clock = lambda: SimpleNamespace(is_open=False)
    result = executor.execute(decision(), APPROVED, cycle_id="c1")
    assert result.status == "SKIPPED" and broker.submitted == []
    assert project_submission_state(journal).unresolved == {}
