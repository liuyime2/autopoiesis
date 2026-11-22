"""Point-in-time membership of a cross-sectional universe. Research only.

Membership on a date is decided only from bars that closed before it, so a backtest that asks
"what could I have held on day d" gets an answer that a trader on day d could have computed. A
name that was delisted is a member until its data ends, which is what keeps the universe free
of survivorship: a panel of today's names alone would never have held a company that failed.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from statistics import median

from autopoiesis.replay import ReplayBar


def members_at(
    day: date,
    bars_by_symbol: Mapping[str, Sequence[ReplayBar]],
    *,
    top_n: int,
    lookback: int = 63,
    min_price: float = 5.0,
    min_history: int = 126,
) -> list[str]:
    """The `top_n` names by median dollar volume over the `lookback` sessions before `day`.

    A name qualifies only with at least `min_history` sessions before `day`, a last close of at
    least `min_price`, and a bar within seven calendar days before `day` (so a name whose data has
    ended stops being a member). Ties break on the symbol, so the answer is deterministic.
    """
    scored: list[tuple[float, str]] = []
    for symbol, bars in bars_by_symbol.items():
        before = [b for b in bars if b.timestamp.date() < day]
        if len(before) < max(min_history, lookback):
            continue
        last = before[-1]
        if (day - last.timestamp.date()).days > 7 or last.close < min_price:
            continue
        window = before[-lookback:]
        scored.append((median(b.close * b.volume for b in window), symbol))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [symbol for _, symbol in scored[:top_n]]
