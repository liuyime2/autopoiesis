"""Replay: run the real loop over real bars, against a simulated account.

The loop already takes its collaborators by injection
(`loop.py: def __init__(self, *, data_gateway, decision_engine, guardian, executor,
journal, mode, trade_counter=None)`), so driving it over history is a substitution rather
than a second implementation. That matters under the objective's "one authoritative
implementation, one canonical execution path": a simulator with its own loop would be
grading a different system from the one that trades, which is the failure `shadow.py`
already documents and refuses.

Only two collaborators change. The decision engine and the Guardian are the real ones,
deliberately: a replay with its own softer risk layer would test something nobody ships.
The journal is a separate file.

**The bars are real.** They come from Alpaca's historical endpoint, cached to disk on first
fetch so a replay is reproducible without a network. Probed before this was designed:
451 real five-minute SPY bars covering 2026-09-30 to 2026-10-02, closing 766.74 -> 769.29,
which brackets the live journal exactly. Nothing here generates a price.

**What it is for.** P1-a changes which strategies trade. Testing that on the live paper
account would need market hours and would write the change's behaviour into the same
evidence stream Monday's first real test of the probation budget depends on, making two
measurements indistinguishable. Replay lets the change be verified without that.

**Safety.** A replayed fill leaking into the PnL ledger would be the most dangerous bug
available: the account would show profit from money never risked, and an unvalidated path
would look like the best one on record. Four things prevent it, in the manner
`shadow.py` established:

1. `ReplayExecutor` holds no broker client and calls no broker endpoint. There is no code
   path from a replay to a live order.
2. Replay execution reports ``status="REPLAYED"``, a distinct value outside the set the
   evaluator treats as executed. Anything branching on the field and unaware of it will not
   count it, which is the safe default.
3. ``order_id`` is always ``None``, so no reconciliation pass can match it to a broker
   order. A real fill always has one.
4. The snapshot's ``source`` is ``alpaca_replay`` - real bars, simulated account, stated
   exactly. It is not in `_FORBIDDEN_EVIDENCE_SOURCES` because the bars genuinely are real,
   and broker-verified PnL keys off ``pnl_evidence`` rather than this field, so a replay can
   never acquire it.

**What replay output is not.** It is not trading evidence. A replayed profit is not a profit,
and `tools/verify.py` asserts the four properties above rather than trusting this docstring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from min_agent.atomicio import write_json_atomic
from min_agent.models import (
    AccountSnapshot,
    DataSnapshot,
    ExecutionResult,
    PositionSnapshot,
    TradeDecision,
)

#: Real bars, simulated account. Says both, so nothing downstream can mistake it for a
#: broker-sourced snapshot.
REPLAY_SOURCE = "alpaca_replay"

#: The one value that must never be mistaken for a fill. See the module docstring.
REPLAYED = "REPLAYED"


@dataclass(frozen=True)
class ReplayBar:
    """One real interval bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict) -> ReplayBar:
        stamp = raw["t"]
        return cls(
            timestamp=datetime.fromtimestamp(float(stamp), tz=timezone.utc)
            if not isinstance(stamp, str)
            else datetime.fromisoformat(stamp.replace("Z", "+00:00")),
            open=float(raw["o"]),
            high=float(raw["h"]),
            low=float(raw["l"]),
            close=float(raw["c"]),
            volume=float(raw.get("v", 0.0)),
        )

    def to_dict(self) -> dict:
        return {
            "t": self.timestamp.isoformat(),
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
        }


def fetch_bars(symbol: str, start: str, end: str, *, client, interval: str = "5Min") -> list[ReplayBar]:
    """Real bars from the broker's historical endpoint.

    Deliberately the broker's own history rather than a generator: a replay over invented
    prices cannot tell you whether the system works, only that it runs.
    """
    from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit

    units = {"Min": TimeFrameUnit.Minute, "Hour": TimeFrameUnit.Hour, "Day": TimeFrameUnit.Day}
    digits = ""
    for char in interval:
        if not char.isdigit():
            break
        digits += char
    if not digits or interval[len(digits):] not in units:
        raise ValueError(f"unsupported interval {interval!r}; want e.g. 5Min, 1Hour, 1Day")
    timeframe = TimeFrame(int(digits), units[interval[len(digits):]])
    bars = client.get_bars(symbol, timeframe, start, end)
    frame = bars.df
    return [
        ReplayBar(
            timestamp=(
                stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
            ),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0) or 0.0),
        )
        for stamp, row in frame.iterrows()
    ]


def load_bars(path: str | Path) -> list[ReplayBar]:
    """Read cached bars, so a replay is reproducible without a network."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ReplayBar.from_dict(row) for row in payload["bars"]]


def save_bars(path: str | Path, symbol: str, bars: list[ReplayBar]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic, because the rest of the repository writes through atomicio and a half-written
    # bars file is a replay that silently starts mid-session.
    write_json_atomic(
        path,
        {
            "symbol": symbol,
            "provenance": "alpaca historical bars; not synthetic",
            "bars": [bar.to_dict() for bar in bars],
        },
    )


class ReplayBook:
    """The simulated account: the thing the broker would otherwise be.

    Kept separate from the gateway and the executor for the same reason the live path has
    two: the executor writes the book, the gateway reads it, and neither reaches into the
    other. A replay that let the gateway mutate positions would be testing a shape the live
    system does not have.
    """

    def __init__(self, *, starting_equity: float = 100_000.0, symbol: str = "SPY"):
        self.symbol = symbol.upper()
        self.starting_equity = starting_equity
        self.cash = starting_equity
        self.quantity = 0.0
        self.day_start_equity = starting_equity
        self.filled: list[tuple[str, float, float]] = []

    def apply(self, side: str, quantity: float, price: float) -> None:
        signed = quantity if side == "BUY" else -quantity
        self.quantity += signed
        self.cash -= signed * price
        self.filled.append((side, quantity, price))

    def equity(self, price: float) -> float:
        return self.cash + self.quantity * price

    def snapshot(self, price: float) -> tuple[AccountSnapshot, tuple[PositionSnapshot, ...]]:
        equity = self.equity(price)
        loss = max(0.0, self.day_start_equity - equity)
        account = AccountSnapshot(
            equity=equity,
            cash=self.cash,
            buying_power=self.cash,
            portfolio_value=equity,
            daily_loss=loss,
            day_start_equity_known=True,
        )
        positions = (
            (PositionSnapshot(symbol=self.symbol, quantity=self.quantity, market_value=abs(self.quantity * price)),)
            if self.quantity
            else ()
        )
        return account, positions


class ReplayDataGateway:
    """Serves real bars, one per cycle, with the simulated account attached.

    Same `snapshot(symbol)` contract as `AlpacaDataGateway`, so the real loop cannot tell
    the difference - which is the property that makes a replay worth anything.
    """

    def __init__(self, *, bars: list[ReplayBar], book: ReplayBook, symbol: str = "SPY"):
        if not bars:
            raise ValueError("a replay over zero bars would prove nothing")
        self.bars = bars
        self.book = book
        self.symbol = symbol.upper()
        self.cursor = 0

    @property
    def exhausted(self) -> bool:
        return self.cursor >= len(self.bars)

    @property
    def current(self) -> ReplayBar:
        # Clamped, because a caller can legitimately advance past the end - the daemon's
        # sleep hook advances between cycles and it does maintenance before it checks
        # whether it is done. `snapshot` already clamped; this did not, so the same
        # over-advance that produced a valid last-bar snapshot raised IndexError the moment
        # anything asked for the current time.
        return self.bars[min(self.cursor, len(self.bars) - 1)]

    def snapshot(self, symbol: str) -> DataSnapshot:
        symbol = symbol.upper()
        if symbol != self.symbol:
            raise ValueError(f"replay holds {self.symbol} bars, asked for {symbol}")
        bar = self.bars[min(self.cursor, len(self.bars) - 1)]
        account, positions = self.book.snapshot(bar.close)
        return DataSnapshot(
            symbol=symbol,
            timestamp=bar.timestamp,
            market_open=True,
            last_price=bar.close,
            source=REPLAY_SOURCE,
            account=account,
            day_start_equity=self.book.day_start_equity,
            positions=positions,
            open_orders=(),
        )

    def advance(self) -> None:
        self.cursor += 1

    def next_open(self) -> float:
        """The fill price for an order decided on the current bar.

        The *next* bar's open, not this bar's close. Filling on the close of the bar the
        decision was made from is the classic way to manufacture a backtest: it assumes
        the close was knowable before it printed.
        """
        following = self.bars[self.cursor + 1] if self.cursor + 1 < len(self.bars) else self.bars[-1]
        return following.open


class ReplayExecutor:
    """Fills against the simulated book. Holds no broker client and cannot place an order.

    Returns `REPLAYED`, never `SUBMITTED`, and never an `order_id`.
    """

    def __init__(self, *, gateway: ReplayDataGateway, book: ReplayBook, clock=None):
        self.gateway = gateway
        self.book = book
        self.clock = clock or (lambda: gateway.current.timestamp)

    def execute(self, decision: TradeDecision, guardian_result, *, cycle_id: str) -> ExecutionResult:
        # The canonical call passes cycle_id by keyword and the real executor correlates the
        # order with it. A replay places no order and has nothing to correlate, so the name is
        # kept for the interface and dropped here rather than renamed out of the signature.
        del cycle_id
        if not getattr(guardian_result, "approved", False):
            return ExecutionResult(
                status="SKIPPED",
                order_id=None,
                client_order_id=None,
                filled_quantity=0,
                message=f"replay: guardian refused - {getattr(guardian_result, 'reason', '')}",
            )
        if decision.action == "HOLD" or decision.quantity <= 0:
            return ExecutionResult(
                status="SKIPPED",
                order_id=None,
                client_order_id=None,
                filled_quantity=0,
                message="replay: hold",
            )
        price = self.gateway.next_open()
        self.book.apply(decision.action, float(decision.quantity), price)
        return ExecutionResult(
            status=REPLAYED,
            order_id=None,
            client_order_id=None,
            filled_quantity=float(decision.quantity),
            filled_avg_price=price,
            filled_at=self.clock(),
            broker_status=REPLAYED,
            message=(
                f"replay: {decision.action} {decision.quantity} filled at next-bar open "
                f"{price:.2f}; not a broker fill and not trading evidence"
            ),
        )
