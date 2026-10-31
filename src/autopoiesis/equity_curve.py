"""The whole account against SPY on daily marks, and how long the current policy has been running.

`docs/AMPLIFICATION_STANDARD.md` asks three questions of a 60-day cohort: is the risk-adjusted return
no worse than SPY's, is the maximum drawdown smaller, and was a hard limit ever touched. Until now
the benchmark could say only "behind buy-and-hold by N points", which answers none of them. This is
the pure arithmetic for the first two and for the cohort length; it reads nothing itself.

Marks are the last journalled account equity and the last journalled SPY price of each New York day,
from live (`alpaca`) snapshots only. Cash flows are not adjusted for: the account has had none in the
recorded window, and a deposit would show as a return, which is why the caller states the window.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from autopoiesis.models import CycleRecord

NEW_YORK = ZoneInfo("America/New_York")
COHORT_TARGET_DAYS = 60
#: Fewer daily returns than this and a Sharpe ratio is noise, so none is reported.
MIN_DAYS_FOR_SHARPE = 20


@dataclass(frozen=True)
class DayMark:
    day: date
    equity: float
    spy: float | None


def daily_marks(records: Iterable[CycleRecord]) -> list[DayMark]:
    """The last account equity and SPY price of each New York day, live snapshots only."""
    equity: dict[date, tuple[datetime, float]] = {}
    spy: dict[date, tuple[datetime, float]] = {}
    for record in records:
        snap = record.snapshot
        if snap.source != "alpaca":
            continue
        day = snap.timestamp.astimezone(NEW_YORK).date()
        if day not in equity or snap.timestamp >= equity[day][0]:
            equity[day] = (snap.timestamp, snap.account.equity)
        if snap.symbol == "SPY" and (day not in spy or snap.timestamp >= spy[day][0]):
            spy[day] = (snap.timestamp, snap.last_price)
    return [DayMark(day, equity[day][1], spy[day][1] if day in spy else None) for day in sorted(equity)]


def returns(values: Sequence[float]) -> list[float]:
    return [b / a - 1.0 for a, b in pairwise(values) if a > 0]


def sharpe(values: Sequence[float]) -> float | None:
    """Annualised mean over standard deviation of daily returns, risk-free rate taken as zero."""
    r = returns(values)
    if len(r) < MIN_DAYS_FOR_SHARPE:
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return None if var <= 0 else mean / math.sqrt(var) * math.sqrt(252)


def max_drawdown(values: Sequence[float]) -> float:
    """Peak-to-trough fall as a negative fraction (0.0 when the series never fell)."""
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def compare_with_spy(marks: Sequence[DayMark]) -> dict[str, object] | None:
    """Account and SPY over the days that have both marks, and the standard's first two questions."""
    both = [m for m in marks if m.spy is not None]
    if len(both) < 2:
        return None
    acct = [m.equity for m in both]
    spy = [m.spy for m in both if m.spy is not None]
    s_acct, s_spy = sharpe(acct), sharpe(spy)
    dd_acct, dd_spy = max_drawdown(acct), max_drawdown(spy)
    enough = len(both) >= COHORT_TARGET_DAYS
    return {
        "days": len(both), "first": both[0].day.isoformat(), "last": both[-1].day.isoformat(),
        "account_return_pct": round((acct[-1] / acct[0] - 1) * 100, 3),
        "spy_return_pct": round((spy[-1] / spy[0] - 1) * 100, 3),
        "account_sharpe": None if s_acct is None else round(s_acct, 3),
        "spy_sharpe": None if s_spy is None else round(s_spy, 3),
        "account_max_drawdown_pct": round(dd_acct * 100, 3),
        "spy_max_drawdown_pct": round(dd_spy * 100, 3),
        # Items 1 and 2 of the standard. Not judged below 60 days: a figure on 29 days is 29 days.
        "risk_adjusted_not_below_spy": None if not enough or s_acct is None or s_spy is None else s_acct >= s_spy,
        "drawdown_smaller_than_spy": None if not enough else abs(dd_acct) < abs(dd_spy),
    }


def cohort_start(deployments: Path) -> tuple[datetime, str] | None:
    """When the current build began, from the deployment record: the first record of the trailing
    run that shares the latest fingerprint (a redeploy of the same build, or a docs-only commit,
    does not restart the count; a different source fingerprint does)."""
    try:
        rows = [json.loads(line) for line in deployments.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    if not rows:
        return None
    fingerprint = rows[-1]["fingerprint"]
    first = rows[-1]
    for row in reversed(rows):
        if row["fingerprint"] != fingerprint:
            break
        first = row
    return datetime.fromisoformat(first["recorded_at"]), fingerprint


def cohort_days(marks: Sequence[DayMark], started: datetime) -> int:
    """Trading days with cycles strictly after the day the build was installed."""
    start_day = started.astimezone(NEW_YORK).date()
    return sum(1 for m in marks if m.day > start_day)
