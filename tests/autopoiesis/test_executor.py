from datetime import datetime, timezone

import pytest

from autopoiesis.executor import AlpacaPaperExecutor
from autopoiesis.journal import JsonlJournal
from autopoiesis.models import ExecutionResult, GuardianResult, JournalEvent, TradeDecision


class FakeAlpacaClient:
    def __init__(self, *, is_open=True):
        self.orders = []
        self._is_open = is_open

    def get_clock(self):
        """The executor asks at submit time whether the session is still open.

        A fake without a clock is refused, by design: a client that cannot be asked cannot prove the
        session is open, and that is the last gate before an order leaves the process.
        """
        return type("Clock", (), {"is_open": self._is_open})()

    def submit_order(self, **kwargs):
        self.orders.append(kwargs)
        return type(
            "Order",
            (),
            {
                "id": "order-123",
                "client_order_id": kwargs.get("client_order_id"),
                "filled_qty": "0",
                "filled_avg_price": None,
                "filled_at": None,
                "status": "accepted",
            },
        )()


def test_an_order_is_not_submitted_after_the_close(tmp_path):
    """A day order placed after the close cannot fill, and it holds the symbol's open-order slot.

    Measured 2026-10-20: a cycle whose snapshot was taken at 15:59 placed a day order at 16:01:05.
    The Guardian had approved it against that snapshot - the model call in between took most of a
    minute - and the order sat `accepted` until it was cancelled by hand, which then tripped the
    conflicting-order guard on the next session.
    """
    class ClosedClient(FakeAlpacaClient):
        def get_clock(self):
            return type("Clock", (), {"is_open": False})()

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = ClosedClient()
    executor = AlpacaPaperExecutor(client=client, base_url="https://paper-api.alpaca.markets", journal=journal)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.6, rationale="t")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="c1")

    assert result.status == "SKIPPED"
    assert "market is closed at submit time" in result.message
    assert client.orders == [], "nothing may reach the broker once the session has closed"
    assert journal.read_events("ORDER_SUBMISSION_INTENT") == [], (
        "no write-ahead intent may be left for an order that was never placed, or reconciliation "
        "has to resolve a submission that never happened"
    )


def test_an_order_is_submitted_when_the_clock_says_open(tmp_path):
    """The check must not become a permanent refusal - the same failure the staleness gate had."""
    class OpenClient(FakeAlpacaClient):
        def get_clock(self):
            return type("Clock", (), {"is_open": True})()

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = OpenClient()
    executor = AlpacaPaperExecutor(client=client, base_url="https://paper-api.alpaca.markets", journal=journal)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.6, rationale="t")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="c2")

    assert client.orders, "an open session must still place the order"
    assert result.message != "submission refused: the market is closed at submit time, so a day order could not fill"


def test_executor_skips_hold_without_order():
    client = FakeAlpacaClient()
    journal = JsonlJournal("unused.jsonl")
    executor = AlpacaPaperExecutor(client=client, base_url="https://paper-api.alpaca.markets", journal=journal)
    decision = TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"))

    assert result == ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold")
    assert client.orders == []


def test_executor_submits_approved_paper_buy_order(tmp_path):
    client = FakeAlpacaClient()
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    executor = AlpacaPaperExecutor(client=client, base_url="https://paper-api.alpaca.markets", journal=journal)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=2, confidence=0.7, rationale="trend")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="cycle-1")

    assert result.status == "SUBMITTED"
    assert result.order_id == "order-123"
    assert result.client_order_id is not None
    assert result.message == "accepted"
    events = journal.read_events()
    assert [event.event_type for event in events] == [
        "ORDER_SUBMISSION_INTENT", "ORDER_SUBMISSION_ACKNOWLEDGED"
    ]
    assert events[0].payload["client_order_id"] == result.client_order_id
    assert events[0].payload["action"] == "BUY"
    assert events[0].payload["quantity"] == 2
    assert events[1].payload["order_id"] == "order-123"
    assert client.orders == [
        {
            "symbol": "SPY",
            "qty": 2,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "client_order_id": result.client_order_id,
        }
    ]


def test_executor_captures_broker_fill_fields_when_returned(tmp_path):
    class FilledClient(FakeAlpacaClient):
        def submit_order(self, **kwargs):
            self.orders.append(kwargs)
            return type(
                "Order",
                (),
                {
                    "id": "order-filled",
                    "client_order_id": kwargs.get("client_order_id"),
                    "filled_qty": "2",
                    "filled_avg_price": "101.25",
                    "filled_at": "2026-06-10T13:31:00Z",
                    "status": "filled",
                },
            )()

    executor = AlpacaPaperExecutor(
        client=FilledClient(), base_url="https://paper-api.alpaca.markets",
        journal=JsonlJournal(tmp_path / "journal.jsonl"),
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=2, confidence=0.7, rationale="trend")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="cycle-1")

    assert result.filled_quantity == 2
    assert result.filled_avg_price == 101.25
    assert result.filled_at is not None
    assert result.broker_status == "filled"


def test_executor_refuses_non_paper_base_url():
    client = FakeAlpacaClient()
    journal = JsonlJournal("unused.jsonl")

    try:
        AlpacaPaperExecutor(client=client, base_url="https://api.alpaca.markets", journal=journal)
    except ValueError as exc:
        assert "paper" in str(exc)
    else:
        raise AssertionError("expected paper URL validation")


def test_submit_timeout_is_durable_unknown_not_error(tmp_path):
    class AcceptedButLostResponse(FakeAlpacaClient):
        def submit_order(self, **kwargs):
            self.orders.append(kwargs)
            raise TimeoutError("fake test: accepted by broker, response lost")

    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = AcceptedButLostResponse()
    executor = AlpacaPaperExecutor(
        client=client, base_url="https://paper-api.alpaca.markets", journal=journal
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.8, rationale="test")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="cycle-1")

    assert result.status == "SUBMISSION_UNKNOWN"
    assert client.orders
    events = journal.read_events()
    assert [event.event_type for event in events] == [
        "ORDER_SUBMISSION_INTENT", "ORDER_SUBMISSION_UNKNOWN"
    ]
    assert events[0].payload["client_order_id"] == events[1].payload["client_order_id"]


def test_intent_append_failure_prevents_broker_submit(tmp_path, monkeypatch):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = FakeAlpacaClient()
    executor = AlpacaPaperExecutor(
        client=client, base_url="https://paper-api.alpaca.markets", journal=journal
    )

    def fail_intent(event):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(journal, "append_event", fail_intent)
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.8, rationale="test")

    with pytest.raises(OSError, match="fsync"):
        executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="cycle-1")
    assert client.orders == []


def test_unresolved_intent_blocks_every_later_non_hold_submission(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    journal.append_event(JournalEvent(
        event_id="intent-existing",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        cycle_id="cycle-old",
        payload={"client_order_id": "cid-existing", "symbol": "QQQ", "quantity": 1},
    ))
    client = FakeAlpacaClient()
    executor = AlpacaPaperExecutor(
        client=client, base_url="https://paper-api.alpaca.markets", journal=journal
    )
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=0.8, rationale="test")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="cycle-2")

    assert result.status == "SKIPPED"
    assert "unresolved" in result.message
    assert client.orders == []
