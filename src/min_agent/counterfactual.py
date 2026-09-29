"""What would have happened if a HOLD had been a trade.

864 of 920 journaled cycles are HOLD. Until now nothing could be learned from
them, because a decision is only right or wrong relative to what happened next,
and nothing recorded what happened next. So the system could count its decisions
and their realized PnL, but it could not answer the only question that matters
about a hold: was holding correct?

The counterfactual is deliberately narrow and honest:

* It only ever considers a **standard probe entry** - one share of the watched
  symbol at the price the broker quoted at that moment. That is a fixed, stated
  benchmark, not a claim about what any particular strategy would have done. A
  per-strategy counterfactual would have to model the strategy's own entry rule
  against future data, which is a backtest, and backtests are not evidence.

* It is **point-in-time**. A decision is only evaluated once the journal holds a
  later real quote for that symbol. A hold near the end of the journal stays
  pending rather than being filled with a guess. There is no interpolation, no
  forward fill, and no use of a price from before the decision.

* It is **after cost**. The paper broker reports zero commission on every one of
  the 24 fills on record, so a gross counterfactual would flatter the system by
  exactly the amount it would have paid in reality. `assumed_round_trip_cost_pct`
  is an explicit, configured assumption - not observed data - and it is recorded on
  every row so a reader can see what the numbers depend on.

Verdicts:

* ``MISSED_ALPHA``  the probe would have gained net of cost; holding cost us money
* ``GOOD_HOLD``     the probe would have lost net of cost; holding saved us money
* ``NEUTRAL``       the move did not clear the cost, so the hold taught us nothing
* ``FALSE_TRADE``   an order that was actually filled lost money net of cost, so
                    the same reasoning that produced the hold would have produced
                    a losing trade
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from min_agent.models import CycleRecord

GOOD_HOLD = "GOOD_HOLD"
MISSED_ALPHA = "MISSED_ALPHA"
NEUTRAL = "NEUTRAL"
FALSE_TRADE = "FALSE_TRADE"

#: Charged on every counterfactual, in percent of notional, both ways. The paper
#: broker reports zero commission, so this is the assumption standing in for the
#: spread and slippage a real venue would charge. It is a number, not a claim about
#: what Alpaca would have done.
DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT = 0.05

#: A move smaller than this is treated as no information at all rather than being
#: sorted into good or bad by rounding noise.
DEFAULT_DEAD_BAND_PCT = 0.05

#: How long to wait before scoring a decision, in wall-clock hours.
#:
#: A cycle *count* is the wrong unit and the first cut used one. Measured over the
#: live journal, a 6-cycle horizon spans a median of 8 minutes but a maximum of 97
#: days - the system was dead for 97 days, so "six cycles later" is a different
#: regime entirely. 49% of consecutive quotes are byte-identical too, so a
#: cycle-counted horizon is often measuring a repeated price, i.e. nothing. Time
#: is the only unit that means the same thing on every row.
DEFAULT_HORIZON_HOURS = 24.0

#: A gap larger than this between the decision and the horizon quote means the
#: market or the daemon was not running, so the two prices are not comparable.
#: Scored across such a gap the number is confident and wrong.
MAX_HORIZON_GAP_HOURS = 72.0


@dataclass(frozen=True)
class CounterfactualRow:
    """One scored decision. `price_at_horizon` is None while the row is pending."""

    cycle_id: str
    symbol: str
    action: str
    quantity: int
    strategy_id: str | None
    decided_at: datetime
    price_at_decision: float
    price_at_horizon: float | None
    horizon_hours: float
    gap_hours: float | None
    gross_return_pct: float | None
    assumed_cost_pct: float
    net_return_pct: float | None
    verdict: str
    decision_source: str | None = None

    @property
    def pending(self) -> bool:
        return self.price_at_horizon is None


@dataclass
class CounterfactualReport:
    rows: list[CounterfactualRow] = field(default_factory=list)

    @property
    def scored(self) -> list[CounterfactualRow]:
        """Rows that were actually scored.

        A GAP row was *refused*, not scored - including it would put a
        confidently wrong number into the counts, which is the failure this whole
        module exists to avoid.
        """
        return [row for row in self.rows if row.verdict not in {"PENDING", "GAP"}]

    @property
    def pending_count(self) -> int:
        return sum(1 for row in self.rows if row.verdict == "PENDING")

    @property
    def gap_count(self) -> int:
        """Rows dropped because the horizon quote was too far away to compare."""
        return sum(1 for row in self.rows if row.verdict == "GAP")

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.scored:
            out[row.verdict] = out.get(row.verdict, 0) + 1
        if self.gap_count:
            out["GAP"] = self.gap_count
        if self.pending_count:
            out["PENDING"] = self.pending_count
        return out

    def net_pct_total(self) -> float:
        return sum(row.net_return_pct or 0.0 for row in self.scored)

    def hold_quality(self) -> float | None:
        """Share of scored holds that were correct, or None if none were scored.

        Only holds count. A false trade says the model traded when it should not
        have; it is not evidence that a hold was good.
        """
        holds = [row for row in self.scored if row.action == "HOLD"]
        if not holds:
            return None
        good = sum(1 for row in holds if row.verdict == GOOD_HOLD)
        return round(good / len(holds), 6)


def _price_series(records: list[CycleRecord], symbol: str) -> list[tuple[datetime, float, CycleRecord]]:
    """Real quotes for `symbol`, in observation order, with repeats collapsed.

    A quote identical to the one before it is not a new observation - the gateway
    is reporting the same last trade because nothing traded in between. Keeping
    them made half the "horizons" a move of exactly zero, which is an artefact of
    the polling interval rather than a fact about the market.
    """
    ordered = sorted(
        (
            (record.snapshot.timestamp, record.snapshot.last_price, record)
            for record in records
            if record.snapshot.symbol == symbol and record.snapshot.last_price > 0
        ),
        key=lambda item: item[0],
    )
    series: list[tuple[datetime, float, CycleRecord]] = []
    for item in ordered:
        if series and series[-1][1] == item[1]:
            continue
        series.append(item)
    return series


def _horizon_quote(
    series: list[tuple[datetime, float, CycleRecord]],
    index: int,
    decided_at: datetime,
    horizon_hours: float,
    max_gap_hours: float,
) -> tuple[float | None, float | None, str]:
    """The first quote at or after the horizon, and how far away it was."""
    target = decided_at.timestamp() + horizon_hours * 3600.0
    for timestamp, price, _ in series[index + 1:]:
        if timestamp.timestamp() >= target:
            gap = (timestamp - decided_at).total_seconds() / 3600.0
            if gap > max_gap_hours:
                return None, gap, "GAP"
            return price, gap, "SCORED"
    return None, None, "PENDING"


def evaluate(
    records: list[CycleRecord],
    *,
    symbol: str = "SPY",
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
    max_gap_hours: float = MAX_HORIZON_GAP_HOURS,
    assumed_cost_pct: float = DEFAULT_ASSUMED_ROUND_TRIP_COST_PCT,
    dead_band_pct: float = DEFAULT_DEAD_BAND_PCT,
) -> CounterfactualReport:
    """Score every decision the journal holds, using only later real quotes."""
    series = _price_series(records, symbol)
    if not series:
        return CounterfactualReport()

    report = CounterfactualReport()
    for index, (decided_at, price, record) in enumerate(series):
        action = record.decision.action
        if action not in {"HOLD", "BUY", "SELL"}:
            continue
        future_price, gap, status = _horizon_quote(
            series, index, decided_at, horizon_hours, max_gap_hours
        )

        if future_price is None:
            # Not scoreable, and saying so is the point: a row that cannot be
            # scored honestly is labelled, not filled in with a guess.
            report.rows.append(
                CounterfactualRow(
                    cycle_id=record.cycle_id, symbol=symbol, action=action,
                    quantity=int(record.decision.quantity),
                    strategy_id=record.strategy_id, decided_at=decided_at,
                    price_at_decision=price, price_at_horizon=None,
                    horizon_hours=horizon_hours, gap_hours=(
                        round(gap, 4) if gap is not None else None
                    ),
                    gross_return_pct=None,
                    assumed_cost_pct=assumed_cost_pct, net_return_pct=None,
                    verdict=status, decision_source=record.decision.decision_source,
                )
            )
            continue

        gross = (future_price - price) / price * 100.0
        net = gross - assumed_cost_pct
        if action == "HOLD":
            # The counterfactual trade is a standard probe entry. A gain it would
            # have made is alpha the agent did not take; a loss is damage it avoided.
            if net > dead_band_pct:
                verdict = MISSED_ALPHA
            elif net < -dead_band_pct:
                verdict = GOOD_HOLD
            else:
                verdict = NEUTRAL
        else:
            # A real decision. Same arithmetic, signed for the side actually taken.
            signed = net if action == "BUY" else -net
            verdict = FALSE_TRADE if signed < -dead_band_pct else NEUTRAL

        report.rows.append(
            CounterfactualRow(
                cycle_id=record.cycle_id, symbol=symbol, action=action,
                quantity=int(record.decision.quantity),
                strategy_id=record.strategy_id, decided_at=decided_at,
                price_at_decision=price, price_at_horizon=future_price,
                horizon_hours=horizon_hours, gap_hours=round(gap or 0.0, 4),
                gross_return_pct=round(gross, 6),
                assumed_cost_pct=assumed_cost_pct, net_return_pct=round(net, 6),
                verdict=verdict, decision_source=record.decision.decision_source,
            )
        )
    return report
