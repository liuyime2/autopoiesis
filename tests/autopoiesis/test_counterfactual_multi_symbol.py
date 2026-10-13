"""The counterfactual must judge every configured symbol, each against its own future.

Measured 2026-10-13, the first open session after deploying source `9fb9066d820f27c9`.
`AgentDaemon._counterfactual_pass` passed `config.symbols[0]` to `counterfactual.evaluate`:

    symbol = self.config.symbols[0] if self.config.symbols else "SPY"

`AUTOPOIESIS_SYMBOLS=SPY,BIL,TLT,XLB,XLE,XLF`, so every pass scored SPY and nothing else. All
493 `COUNTERFACTUAL_EVALUATED` events in the live journal carry `symbol: SPY`, and no other
symbol has a single row anywhere.

That is not a shortage of data, it is an absence of judgement for the strategies that
actually traded. On 2026-10-13 `fixed-size-tlt-sell-001` submitted nine orders,
`fixed-size-xle-sell-001` two and `fixed-size-xlf-sell-001` one; all three had **zero**
decisions in the offline screen. A strategy with no rows cannot be screened on any horizon, so
it can never be promoted out of probation and never judged on its outcomes, whatever the
probation budget is set to.

The per-symbol correctness is not negotiable and is the reason `evaluate` keeps its shape: a
TLT decision is a question about TLT's next 24 hours, and grading it against SPY's future
would be a confidently wrong number rather than a rough one. This file pins that as well as
the widened coverage, because the change must add symbols without re-grading anything.

Usage: pytest tests/autopoiesis/test_counterfactual_multi_symbol.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from synthetic_evidence import synthetic_clean_record

from autopoiesis.counterfactual import (
    GOOD_HOLD,
    MISSED_ALPHA,
    evaluate,
    evaluate_symbols,
)
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)

BASE = datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)
CONFIGURED = ("SPY", "BIL", "TLT", "XLB", "XLE", "XLF")
#: `_horizon_quote` returns this when the forward quote does not exist yet. It is a bare
#: literal in `counterfactual`, not an exported constant, so it is named here rather than
#: imported - a rename in the module should fail this test loudly, not silently.
PENDING = "PENDING"


def _record(
    symbol: str,
    *,
    price: float,
    at: datetime,
    action: str = "HOLD",
    strategy_id: str | None = None,
    quantity: float = 0,
) -> CycleRecord:
    return synthetic_clean_record(CycleRecord(
        cycle_id=f"{symbol}-{at.isoformat()}",
        symbol=symbol,
        snapshot=DataSnapshot(
            symbol=symbol,
            timestamp=at,
            market_open=True,
            last_price=price,
            source="alpaca",
            account=AccountSnapshot(
                equity=100_000, cash=50_000, buying_power=150_000, portfolio_value=100_000, daily_loss=0
            ),
        ),
        decision=TradeDecision(
            symbol=symbol, action=action, quantity=quantity, confidence=0.6, rationale="t",
            strategy_id=strategy_id,
        ),
        guardian=GuardianResult(approved=False, reason="not submitted"),
        execution=ExecutionResult(status="SKIPPED", order_id=None, filled_quantity=0, message="m"),
        strategy_id=strategy_id,
    ))


#: Which way each symbol moves after its decision. SPY falls and TLT rises, so a verdict
#: identical across the two would be evidence that the series were shared rather than
#: per-symbol. The rest alternate so a symbol cannot be singled out.
_DIRECTION_BY_SYMBOL = {
    "SPY": 0.99, "BIL": 1.01, "TLT": 1.01, "XLB": 0.99, "XLE": 1.01, "XLF": 0.99,
}


def _ledger() -> list[CycleRecord]:
    """One day per symbol: a decision at the open, then a quote 26 hours later."""
    records: list[CycleRecord] = []
    for index, symbol in enumerate(CONFIGURED):
        price = 100.0 + index
        records.append(_record(symbol, price=price, at=BASE, strategy_id=f"strat-{symbol}"))
        records.append(_record(
            symbol, price=price * _DIRECTION_BY_SYMBOL[symbol], at=BASE + timedelta(hours=26),
        ))
    return records


def _by_cycle(report) -> dict[str, object]:
    return {row.cycle_id: row for row in report.rows}


# --- the defect ---------------------------------------------------------------


def test_a_tlt_decision_is_scored_rather_than_ignored():
    """The live gap: TLT traded nine times and had no row anywhere."""
    report = evaluate_symbols(_ledger(), symbols=CONFIGURED)
    cycle_ids = {row.cycle_id for row in report.rows}

    assert "TLT-2026-10-13T13:30:00+00:00" in cycle_ids
    assert "XLE-2026-10-13T13:30:00+00:00" in cycle_ids
    assert "XLF-2026-10-13T13:30:00+00:00" in cycle_ids


def test_every_configured_symbol_is_covered():
    report = evaluate_symbols(_ledger(), symbols=CONFIGURED)

    assert {row.symbol for row in report.rows} == set(CONFIGURED)


def test_rows_are_not_double_counted_across_symbols():
    report = evaluate_symbols(_ledger(), symbols=CONFIGURED)
    ids = [row.cycle_id for row in report.rows]

    assert len(ids) == len(set(ids)), "a cycle belongs to exactly one symbol and must appear once"


# --- the thing that must not change ------------------------------------------


def test_spy_verdicts_are_identical_to_the_single_symbol_path():
    """Widening coverage must not re-grade what is already graded."""
    records = _ledger()
    single = _by_cycle(evaluate(records, symbol="SPY"))
    multi = {k: v for k, v in _by_cycle(evaluate_symbols(records, symbols=CONFIGURED)).items() if k.startswith("SPY-")}

    assert set(single) == set(multi)
    for cycle_id, row in single.items():
        assert multi[cycle_id] == row, (
            f"{cycle_id} was re-graded: {multi[cycle_id]!r} is not {row!r}"
        )


def test_each_symbol_is_graded_against_its_own_future():
    """A TLT decision is a question about TLT, not about SPY.

    SPY and TLT are built to move in opposite directions here, so grading TLT against SPY's
    series would flip its verdict. Any single-symbol shortcut shows up as a tie or a flip.
    """
    report = evaluate_symbols(_ledger(), symbols=CONFIGURED)
    # Keyed on the decision cycle, not the symbol: the fixture's later quote is itself a
    # record and produces its own row, which is still inside its horizon.
    decisions = {row.symbol: row.verdict for row in report.rows if row.cycle_id.endswith("T13:30:00+00:00")}

    assert decisions["SPY"] == GOOD_HOLD, "SPY rose after its HOLD, so the HOLD was right"
    assert decisions["TLT"] == MISSED_ALPHA, "TLT fell after its HOLD, so it missed a rally"


def test_a_symbol_with_no_quotes_contributes_nothing_rather_than_being_invented():
    records = [r for r in _ledger() if r.snapshot.symbol in ("SPY", "TLT")]

    report = evaluate_symbols(records, symbols=("SPY", "TLT", "ZZZZ"))

    assert {row.symbol for row in report.rows} == {"SPY", "TLT"}
    assert all(row.price_at_decision > 0 for row in report.rows)


def test_an_empty_symbol_list_is_an_empty_report():
    """Not a fallback to a hard-coded symbol.

    The old caller's `else "SPY"` meant an empty configuration still produced SPY rows, which
    is how a misconfiguration could look like a working one.
    """
    assert evaluate_symbols(_ledger(), symbols=()).rows == []


# --- the horizon still governs ------------------------------------------------


def test_a_decision_within_its_horizon_is_still_pending_across_symbols():
    """Widening coverage must not also widen the horizon."""
    report = evaluate_symbols(_ledger(), symbols=("TLT",), horizon_hours=48.0)

    assert report.rows
    assert {row.verdict for row in report.rows} == {PENDING}
    assert report.pending_count == len(report.rows)
