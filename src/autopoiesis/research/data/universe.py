"""Universe reconstruction as of a date.

Step 2.5 of docs/EXECUTION_PLAN_2026-11-05.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

LIMITED_UNIVERSE = "LIMITED_UNIVERSE"

#: Research starter sample (plan step 2.6). Not a restriction on tradable scope.
RESEARCH_STARTER_SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "GLD", "XLF", "XLE")

#: Known inception dates for the starter symbols (ETFs). Stocks would come from
#: a filings source; missing entries are reported as LIMITED_UNIVERSE.
_INCEPTION: dict[str, date] = {
    "SPY": date(1993, 1, 29),
    "QQQ": date(1999, 3, 10),
    "IWM": date(2000, 5, 26),
    "TLT": date(2002, 7, 30),
    "GLD": date(2004, 11, 18),
    "XLF": date(1998, 12, 22),
    "XLE": date(1998, 12, 22),
}


def universe_as_of(
    as_of: date | datetime | str, *, symbols: Iterable[str] | None = None
) -> tuple[list[str], list[str]]:
    """Return (available, limited) symbol lists for `as_of`.

    A symbol whose inception is unknown or after `as_of` is reported as limited
    rather than silently dropped.
    """
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of[:10])
    elif isinstance(as_of, datetime):
        as_of = as_of.date()

    candidates = list(symbols) if symbols is not None else list(RESEARCH_STARTER_SYMBOLS)
    available: list[str] = []
    limited: list[str] = []
    for sym in candidates:
        su = sym.upper()
        inc = _INCEPTION.get(su)
        if inc is None or inc > as_of:
            limited.append(su)
        else:
            available.append(su)
    return available, limited
