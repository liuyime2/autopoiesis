"""Probe: can the replay harness reach the account?

Run standalone (`python tools/replay_safety_probe.py`) it prints a JSON list of the
properties that failed, so a failure is legible rather than a traceback. `make verify`
calls it through `check_replay_cannot_reach_the_account`.

This lives in a file rather than inside `tools/verify.py` because a check this size
inlined into a string concatenation is unreadable, and an unreadable safety check is
one nobody updates when the harness changes.

The properties, and why each matters:

1. **No broker client, no paper/live executor.** A replay that could place an order
   would not be a replay. It holds no client and calls no broker endpoint.
2. **`REPLAYED` is distinct and carries no `order_id`.** Anything branching on
   `ExecutionResult.status` and unaware of the value will not count it as executed,
   which is the safe default; a null `order_id` means no reconciliation pass can
   match it to a real order, because a real fill always has one.
3. **A journal of replayed fills evaluates to zero realized PnL** and never claims
   broker verification. This is the property that actually matters: not "the evaluator
   ignores replay" but "the account says zero money was made".
4. **The source says what it is** - real bars, simulated account.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from min_agent.evaluator import DeterministicEvaluator  # noqa: E402
from min_agent.journal import JsonlJournal  # noqa: E402
from min_agent.models import (  # noqa: E402
    CycleRecord,
    ExecutionResult,
    GuardianResult,
    TradeDecision,
)
from min_agent.replay import (  # noqa: E402
    REPLAYED,
    REPLAY_SOURCE,
    ReplayBar,
    ReplayBook,
    ReplayDataGateway,
    ReplayExecutor,
)

BROKER_VERIFIED = {"broker_strategy_closed_lot_pnl_verified", "broker_account_pnl_verified"}
BASE = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)


def _bars(count: int = 6) -> list[ReplayBar]:
    return [
        ReplayBar(
            timestamp=BASE + timedelta(minutes=5 * index),
            open=766.0 + index, high=767.0 + index, low=765.5 + index,
            close=766.5 + index, volume=100,
        )
        for index in range(count)
    ]


def _decision(action: str) -> TradeDecision:
    return TradeDecision(
        symbol="SPY", action=action, quantity=2, confidence=0.5, rationale="probe",
    )


def _approve() -> GuardianResult:
    return GuardianResult(approved=True, reason="ok")


def probe() -> list[str]:
    problems: list[str] = []
    bars = _bars()
    source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "min_agent" / "replay.py").read_text()

    # 1. No broker client, no paper/live executor.
    for banned in ("AlpacaPaperExecutor", "submit_order", "tradeapi.REST", "tradeapi.REST("):
        if banned in source:
            problems.append(f"replay.py references {banned}")
    module_scope = source.split("def fetch_bars")[0]
    if "import alpaca_trade_api" in module_scope:
        problems.append("replay imports the broker SDK at module scope")

    # 2. Distinct status, no order id.
    if REPLAYED == "SUBMITTED":
        problems.append("REPLAYED collides with SUBMITTED")
    allowed = set(ExecutionResult.model_fields["status"].annotation.__args__)
    if REPLAYED not in allowed:
        problems.append("REPLAYED is not a declared execution status")

    book = ReplayBook()
    gateway = ReplayDataGateway(bars=bars, book=book)
    result = ReplayExecutor(gateway=gateway, book=book).execute(
        _decision("BUY"), _approve(), cycle_id="c1"
    )
    if result.status != REPLAYED:
        problems.append(f"a filled replay reported status {result.status!r}")
    if result.order_id is not None:
        problems.append("a replayed fill carried an order_id")

    # 4. Source says what it is.
    if gateway.snapshot("SPY").source != REPLAY_SOURCE:
        problems.append("the replay snapshot source is not labelled")

    # A refused decision must not move the book.
    ReplayExecutor(gateway=gateway, book=book).execute(
        _decision("BUY"), GuardianResult(approved=False, reason="limit"), cycle_id="c2"
    )
    if book.quantity != 2:
        problems.append("a guardian refusal still moved the book")

    # 3. Zero realized PnL, never broker-verified.
    with tempfile.TemporaryDirectory() as tmp:
        journal = JsonlJournal(pathlib.Path(tmp) / "journal.jsonl")
        book2 = ReplayBook()
        gateway2 = ReplayDataGateway(bars=bars, book=book2)
        executor2 = ReplayExecutor(gateway=gateway2, book=book2)
        for index in range(4):
            side = "BUY" if index % 2 == 0 else "SELL"
            fill = executor2.execute(_decision(side), _approve(), cycle_id=f"c{index}")
            gateway2.advance()
            journal.append(
                CycleRecord(
                    cycle_id=f"c{index}",
                    snapshot=gateway2.snapshot("SPY"),
                    decision=_decision(side),
                    guardian=_approve(),
                    execution=fill,
                )
            )
        report = DeterministicEvaluator().evaluate(journal.read_all())

    if report.submitted_orders != 0:
        problems.append(f"replay booked {report.submitted_orders} submitted order(s)")
    pnl = report.pnl
    if pnl.account_realized_or_reported_pnl is not None:
        problems.append(
            f"replayed fills booked {pnl.account_realized_or_reported_pnl} of realized PnL"
        )
    if pnl.strategy_realized_pnl:
        problems.append("replay attributed strategy-level realized PnL")
    if pnl.closed_lot_count:
        problems.append(f"replay produced {pnl.closed_lot_count} closed lot(s)")
    if pnl.linked_fill_count:
        problems.append(f"replay linked {pnl.linked_fill_count} fill(s)")
    if report.pnl_evidence in BROKER_VERIFIED:
        problems.append(f"replay claimed broker verification: {report.pnl_evidence}")

    return problems


if __name__ == "__main__":
    print(json.dumps(probe()))