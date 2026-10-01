"""Which of signal, model, strategy, allocation, execution, cost or regime made the PnL.

The objective asks this directly. The answer on the live record is that the model
contributed 0.00, and that was reachable from data the system already held: the lots
record which order opened them and the cycles record which decision source issued
that order, and nothing joined the two. So a number in a payload was read as
evidence that the decision engine worked, when it is evidence about the baseline.

These tests pin the join and, just as importantly, pin the refusals. `CANNOT
ATTRIBUTE` is a legitimate verdict; claiming a cause because the data is suggestive
is how a comfortable story replaces an honest one.
"""

from datetime import datetime, timezone

from min_agent import attribution
from min_agent.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)

TS = datetime(2026, 6, 11, tzinfo=timezone.utc)


def _cycle(order_id, source, strategy_id="s1"):
    return CycleRecord(
        cycle_id=f"cycle-{order_id}",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=TS, market_open=True, last_price=100.0,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=100_000, buying_power=100_000,
                portfolio_value=100_000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol="SPY", action="BUY", quantity=5, confidence=0.6,
            rationale="r", strategy_id=strategy_id, decision_source=source,
        ),
        guardian=GuardianResult(approved=True, reason="ok"),
        execution=ExecutionResult(
            status="SUBMITTED", order_id=order_id, filled_quantity=5,
            filled_avg_price=100.0, message="submitted",
        ),
        strategy_id=strategy_id,
    )


def _lot(order_id, pnl, strategy_id="s1", buy_fees=0.0, sell_fees=0.0):
    return {
        "buy_order_id": order_id, "sell_order_id": f"sell-{order_id}",
        "symbol": "SPY", "quantity": 5, "realized_pnl": pnl,
        "strategy_id": strategy_id, "buy_fees": buy_fees, "sell_fees": sell_fees,
        "opened_at": TS.isoformat(), "closed_at": TS.isoformat(), "source": "broker",
    }


def test_pnl_is_attributed_to_the_decision_source_that_opened_the_lot():
    """The whole point. The lot says which order opened it; the cycle says which
    source issued that order."""
    records = [_cycle("o1", "baseline"), _cycle("o2", "llm")]
    pnl = {"closed_lots": [_lot("o1", 300.0), _lot("o2", 50.0)]}

    result = attribution.attribute(records, pnl)

    assert result.by_source == {"baseline": 300.0, "llm": 50.0}
    assert result.model_pnl == 50.0
    assert result.baseline_pnl == 300.0
    assert result.total_realized_pnl == 350.0


def test_a_model_contribution_of_zero_is_stated_plainly():
    """The live finding. All 23 lots came from the baseline source, so the model's
    contribution is 0.00 and the headline must say so rather than quoting +564.39."""
    records = [_cycle(f"o{i}", "baseline") for i in range(3)]
    pnl = {"closed_lots": [_lot(f"o{i}", 100.0) for i in range(3)]}

    result = attribution.attribute(records, pnl)

    assert result.model_pnl == 0.0
    assert "the model contributed 0.00" in result.headline()
    model_row = next(c for c in result.causes if c.cause == "model")
    assert model_row.verdict == "NOT THE CAUSE"


def test_a_lot_with_no_matching_order_is_unlinked_not_assigned_to_a_source():
    """Silently bucketing an orphan under the model would be the exact error this
    module exists to prevent, in the opposite direction."""
    result = attribution.attribute([], {"closed_lots": [_lot("orphan", 42.0)]})

    assert result.by_source == {"UNLINKED": 42.0}
    assert "UNLINKED" in result.lots_by_source


def test_account_and_strategy_pnl_are_never_reconciled_against_each_other():
    """+564.39 is the agent's closed lots in one window. -406.54 is the broker's
    cumulative profit_loss for the whole account, BIL and TLT included. Their
    difference is not a loss."""
    records = [_cycle("o1", "baseline")]
    pnl = {
        "closed_lots": [_lot("o1", 564.39)],
        "account_realized_or_reported_pnl": -406.54,
    }

    payload = attribution.to_payload(attribution.attribute(records, pnl))

    assert payload["account_pnl"] == -406.54
    assert "cumulative" in payload["account_pnl_scope"]
    assert "not a loss" in payload["do_not_reconcile"]


def test_zero_fees_are_reported_as_gross_not_as_free():
    """The paper broker charged nothing on all 23 lots. Presenting that as a cost
    advantage would overstate what a live venue would have paid."""
    records = [_cycle("o1", "baseline")]
    pnl = {"closed_lots": [_lot("o1", 100.0, buy_fees=0.0, sell_fees=0.0)]}

    result = attribution.attribute(records, pnl)
    cost = next(c for c in result.causes if c.cause == "cost")

    assert result.fees == 0.0
    assert cost.verdict == "NOT A FACTOR HERE"
    assert "overstates" in cost.evidence


def test_regime_is_reported_as_cannot_attribute_not_guessed():
    """No regime context is computed anywhere, so no share of the PnL can be
    separated into regime versus signal."""
    records = [_cycle("o1", "llm")]
    result = attribution.attribute(records, {"closed_lots": [_lot("o1", 10.0)]})

    regime = next(c for c in result.causes if c.cause == "regime")
    assert regime.verdict == "CANNOT ATTRIBUTE"


def test_every_cause_the_objective_names_is_reported():
    result = attribution.attribute(
        [_cycle("o1", "llm")], {"closed_lots": [_lot("o1", 10.0)]},
    )
    assert {c.cause for c in result.causes} == set(attribution.CAUSES)


def test_nothing_to_attribute_says_so():
    result = attribution.attribute([], {"closed_lots": []})
    assert result.headline().startswith("no closed lots")
    assert next(c for c in result.causes if c.cause == "model").verdict == \
        "CANNOT ATTRIBUTE"


def test_refused_orders_are_reported_as_allocation_constraining_the_outcome():
    records = [_cycle("o1", "baseline")]
    result = attribution.attribute(
        records, {"closed_lots": [_lot("o1", 10.0)]},
        {"rejected_orders": 36, "fill_quantity_ratio": 1.0},
    )
    allocation = next(c for c in result.causes if c.cause == "allocation")

    assert allocation.verdict == "PARTLY ATTRIBUTABLE"
    assert "what got through" in allocation.evidence


def test_severity_distinguishes_not_yet_traded_from_traded_and_failed(tmp_path):
    """The distinction that decides whether this is a code fault or a fact waiting
    on market hours. Downgrading "the model has not traded" to make a gate green
    would be masking, so the split is drawn on evidence and tested.

    The second case is a FAIL: the model opened lots, the total is positive, and the
    model contributed none of it. That is a model trading and failing to beat the
    baseline, which is a real problem.
    """
    from min_agent.config import AgentConfig
    from min_agent.doctor import DoctorReport, _check_pnl_attribution
    from min_agent.journal import JsonlJournal
    from min_agent.models import JournalEvent

    def run(tmp_path, lots, records):
        cfg = AgentConfig(
            mode="paper", symbols=("SPY",), allowlist=frozenset({"SPY"}),
            alpaca_api_key="k", alpaca_secret_key="s",
            alpaca_base_url="https://paper-api.alpaca.markets",
            ollama_base_url="http://localhost:11434", model="q", gpu_devices="0",
            journal_path=tmp_path / "journal.jsonl",
            max_position_value=5_000, max_daily_loss=500,
        )
        journal = JsonlJournal(cfg.journal_path)
        for i, lot in enumerate(lots):
            journal.append_event(JournalEvent(
                event_id=f"e{i}", event_type="PNL_EVIDENCE_RECORDED",
                timestamp=TS, status="SUCCESS", message="m",
                payload={"pnl": {"closed_lots": [lot]}, "rejected_orders": 0,
                         "fill_quantity_ratio": 1.0},
            ))
        report = DoctorReport()
        _check_pnl_attribution(report, cfg, journal, records)
        return {c.name: str(c.status.value).upper()
                for c in report.checks}["pnl attribution"]

    # The model has never traded: a fact waiting on market hours.
    baseline_only = run(
        tmp_path, [_lot("o1", 500.0)], [_cycle("o1", "baseline")],
    )
    assert baseline_only == "WARN"

    # The model traded, the account is up, and the model contributed nothing.
    mixed = run(
        tmp_path,
        [_lot("o1", 500.0), _lot("o2", -20.0)],
        [_cycle("o1", "baseline"), _cycle("o2", "llm")],
    )
    # The model opened a lot and lost money on it while the total stayed positive.
    # A `== 0.0` test missed this and reported OK, which is the same defect as the
    # original one: a model that subtracts from a positive total is not a pass.
    assert mixed == "FAIL", (
        "a model that opened lots and contributed nothing to a positive total is a "
        "real failure and must not be downgraded to keep the gate green"
    )


def test_the_cost_cause_reports_the_assumption_rather_than_claiming_no_cost():
    """The row said 'not a factor' while the ledger charged nothing, and was true.
    The moment the assumption was added the same row became false - a health report
    contradicting the number printed beside it."""
    records = [_cycle("o1", "baseline")]
    pnl = {
        "closed_lots": [_lot("o1", 100.0)],
        "assumed_cost": {"s1": 1.25},
        "assumed_cost_pct": 0.05,
        "net_after_assumed_cost": 98.75,
    }
    result = attribution.attribute(records, pnl)
    cost = next(c for c in result.causes if c.cause == "cost")

    assert cost.verdict == "ATTRIBUTABLE"
    assert "98.75" in cost.evidence
    assert "not an observation" in cost.evidence
    assert result.cost_headline().startswith("+98.75 after an assumed")


def test_the_after_cost_figure_is_the_one_reported():
    """The objective's criterion is after-cost. Reporting the gross figure as the
    headline answers a question nobody asked."""
    records = [_cycle("o1", "baseline")]
    pnl = {
        "closed_lots": [_lot("o1", 564.39)],
        "assumed_cost": {"s1": 8.67},
        "assumed_cost_pct": 0.05,
        "net_after_assumed_cost": 555.72,
    }
    payload = attribution.to_payload(attribution.attribute(records, pnl))

    assert payload["after_cost_pnl"] == 555.72
    assert payload["cost_headline"].startswith("+555.72")
    assert payload["observed_fees"] == 0.0
