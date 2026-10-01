"""What kind of market was this? Computed from the data, recorded for the record.

`attribution.py` reports `regime: CANNOT ATTRIBUTE` because nothing in the system
computes or records a regime. That makes it the one cause the objective asks about
that the loop cannot answer, and it is also the prerequisite for Phase 8's
regime-aware allocation. This module closes the measurement half of that. It
deliberately does **not** allocate by regime — allocation is Phase 8, and starting it
now would be entering a phase whose predecessor has not finished.

What is available is one `last_price` per cycle. There is no OHLC, no volume, no VIX.
So the regime is derived from the realized price series, and that carries a limitation
worth stating rather than burying: these are last-trade prints sampled on a 5-minute
cycle, not exchange bars. Volatility estimated from them is noisier and slightly
inflated by whatever happened inside each interval, and a gap between samples reads as
a move that may have occurred at any point within it. It is a description of the price
path the system actually saw, which is the right thing to attribute against and not
the same as a market microstructure measure.

Two properties are enforced rather than assumed:

* **Point-in-time.** A regime at bar *i* is computed from bars up to and including *i*
  only. This matters more here than anywhere else in the system, because a regime
  label attached to a decision is exactly the kind of context that leaks if it is
  computed over the full series and then joined backwards.

* **A gap is a gap.** Quotes separated by more than the expected sampling interval are
  marked, and the sample is refused rather than treated as a continuous path. The live
  journal contains a 97-day outage, and computing a "recent volatility" straight
  across it would describe two different months as one market.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

TRENDING_UP = "TRENDING_UP"
TRENDING_DOWN = "TRENDING_DOWN"
RANGING = "RANGING"
UNSTABLE = "UNSTABLE"
UNKNOWN = "UNKNOWN"

#: Bars in the trailing window. Roughly a session at the 5-minute cycle, which is
#: long enough to say something and short enough not to describe last month.
DEFAULT_WINDOW = 78

#: Realized volatility above this annualised figure marks the market unstable.
VOLATILITY_THRESHOLD_PCT = 0.8

#: Gap larger than this multiple of the expected sampling interval is an outage, not
#: a move. 6x a 5-minute cycle is 30 minutes.
GAP_MULTIPLE = 6.0

#: Trading minutes between consecutive bars, used only to spot gaps.
SAMPLING_MINUTES = 5.0


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    price: float


@dataclass(frozen=True)
class Regime:
    label: str
    volatility_pct: float | None
    trend_pct: float | None
    drawdown_pct: float | None
    bars: int
    contiguous: bool
    note: str = ""

    def to_payload(self) -> dict:
        return {
            "label": self.label,
            "volatility_pct": self.volatility_pct,
            "trend_pct": self.trend_pct,
            "drawdown_pct": self.drawdown_pct,
            "bars": self.bars,
            "contiguous": self.contiguous,
            "note": self.note,
        }


def classify(
    bars: Sequence[Bar],
    *,
    window: int = DEFAULT_WINDOW,
    volatility_threshold_pct: float = VOLATILITY_THRESHOLD_PCT,
) -> Regime:
    """Label the market at the last bar, using only bars up to that point.

    Returns `UNKNOWN` rather than a guess when there is not enough contiguous data.
    A regime label is only useful if it means something on the day it is attached; a
    label computed across an outage describes no market at all.
    """
    if not bars:
        return Regime(UNKNOWN, None, None, None, 0, True, "no bars on record")

    recent = list(bars[-window:]) if len(bars) > window else list(bars)
    if len(recent) < 3:
        return Regime(
            UNKNOWN, None, None, None, len(recent), True,
            f"only {len(recent)} bar(s) available",
        )

    gap_limit = SAMPLING_MINUTES * GAP_MULTIPLE
    contiguous = all(
        (b.timestamp - a.timestamp).total_seconds() / 60.0 <= gap_limit
        for a, b in itertools.pairwise(recent)
    )
    if not contiguous:
        return Regime(
            UNKNOWN, None, None, None, len(recent), False,
            "the window spans a gap larger than "
            f"{gap_limit:.0f} minutes, so it describes more than one market",
        )

    prices = [b.price for b in recent]
    returns = [
        (prices[i] - prices[i - 1]) / prices[i - 1]
        for i in range(1, len(prices))
        if prices[i - 1]
    ]
    if not returns:
        return Regime(UNKNOWN, None, None, None, len(recent), True, "no returns")

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    # Annualised from 5-minute returns, and reported as a fraction rather than a
    # percentage so it is not confused with a move.
    volatility = (variance ** 0.5) * (252 * 78) ** 0.5
    trend = prices[-1] / prices[0] - 1.0
    peak = prices[0]
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak:
            worst = min(worst, (price - peak) / peak)
    drawdown = abs(worst)

    if volatility > volatility_threshold_pct:
        label = UNSTABLE
        note = (
            f"realized volatility {volatility:.2f} annualised is above the "
            f"{volatility_threshold_pct:.2f} threshold"
        )
    elif trend > 0.005:
        label = TRENDING_UP
        note = f"moved {trend:+.2%} across {len(recent)} bars"
    elif trend < -0.005:
        label = TRENDING_DOWN
        note = f"moved {trend:+.2%} across {len(recent)} bars"
    else:
        label = RANGING
        note = f"moved {trend:+.2%} across {len(recent)} bars, inside the band"

    return Regime(
        label=label,
        volatility_pct=round(volatility, 6),
        trend_pct=round(trend, 6),
        drawdown_pct=round(drawdown, 6),
        bars=len(recent),
        contiguous=True,
        note=note,
    )


def regime_timeline(
    bars: Sequence[Bar],
    *,
    window: int = DEFAULT_WINDOW,
) -> list[tuple[datetime, Regime]]:
    """A label per bar, each computed only from bars up to that one.

    This is the point-in-time discipline made explicit: entry *i* is classified from
    `bars[:i + 1]`, never from the whole series. A test asserts that appending future
    bars cannot change an earlier label, which is the property that makes the timeline
    safe to join onto past decisions.
    """
    return [
        (bars[i].timestamp, classify(bars[: i + 1], window=window))
        for i in range(len(bars))
    ]


def bars_from_records(records: Sequence[object], symbol: str = "SPY") -> list[Bar]:
    """Real observations, with repeated quotes collapsed.

    Uses the same collapse rule as the counterfactual. 49% of consecutive journaled
    quotes are byte-identical because the gateway reports the same last trade when
    nothing traded; counting them would halve the sample and make a flat market look
    volatile.
    """
    ordered = sorted(
        (
            (r.snapshot.timestamp, r.snapshot.last_price)
            for r in records
            if getattr(getattr(r, "snapshot", None), "symbol", None) == symbol
            and r.snapshot.last_price > 0
        ),
        key=lambda item: item[0],
    )
    bars: list[Bar] = []
    for timestamp, price in ordered:
        if bars and bars[-1].price == price:
            continue
        bars.append(Bar(timestamp, price))
    return bars
