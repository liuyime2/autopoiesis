from min_agent.executor import AlpacaPaperExecutor
from min_agent.models import ExecutionResult, GuardianResult, TradeDecision


class FakeAlpacaClient:
    def __init__(self):
        self.orders = []

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


def test_executor_skips_hold_without_order():
    client = FakeAlpacaClient()
    executor = AlpacaPaperExecutor(client=client, base_url="https://paper-api.alpaca.markets")
    decision = TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"))

    assert result == ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="hold")
    assert client.orders == []


def test_executor_submits_approved_paper_buy_order():
    client = FakeAlpacaClient()
    executor = AlpacaPaperExecutor(client=client, base_url="https://paper-api.alpaca.markets")
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=2, confidence=0.7, rationale="trend")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="cycle-1")

    assert result.status == "SUBMITTED"
    assert result.order_id == "order-123"
    assert result.client_order_id is not None
    assert result.message == "accepted"
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


def test_executor_captures_broker_fill_fields_when_returned():
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

    executor = AlpacaPaperExecutor(client=FilledClient(), base_url="https://paper-api.alpaca.markets")
    decision = TradeDecision(symbol="SPY", action="BUY", quantity=2, confidence=0.7, rationale="trend")

    result = executor.execute(decision, GuardianResult(approved=True, reason="approved"), cycle_id="cycle-1")

    assert result.filled_quantity == 2
    assert result.filled_avg_price == 101.25
    assert result.filled_at is not None
    assert result.broker_status == "filled"


def test_executor_refuses_non_paper_base_url():
    client = FakeAlpacaClient()

    try:
        AlpacaPaperExecutor(client=client, base_url="https://api.alpaca.markets")
    except ValueError as exc:
        assert "paper" in str(exc)
    else:
        raise AssertionError("expected paper URL validation")
