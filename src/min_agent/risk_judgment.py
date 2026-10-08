"""Risk judgment: how volatile will this symbol be, and is that forecast still any good?

Why this and not a direction. Seventeen intraday, eighteen daily and twelve cross-sectional
hypotheses on real bars (docs/evidence/signal-research-2026-10-08/) found no usable prediction of
which way a price moves, from 5 minutes to 21 days, for SPY, eight ETFs or 120 large-cap stocks;
the one that looked like it was beta. What did carry through, on every instrument, was volatility:
the last month's volatility predicts the next month's, and an EWMA of daily returns (lambda 0.94)
cut the error of a long-run-average forecast by 47% out of sample, with a rank correlation of
about 0.31 per instrument. Scaling exposure by it (target volatility over forecast volatility)
roughly halved the maximum drawdown in the 2016-2022 selection period and cut SPY's from 18.8% to
11.1% in the 2023-2026 test, at the cost of return: a risk judgment, not an edge.

So the model is told how much risk a position carries, not where to go. And because the claim
is a number that either holds or does not, this module measures it on the symbol's own history
(`forecast_quality`) and, when the forecasts stop ranking the realised volatility, says so
(`status: SUSPENDED`) instead of presenting a stale judgment as current. The judgment has its own
lifecycle, the same way a strategy does.

Pure standard library; reads daily closes through a provider and never touches an order.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date
from itertools import pairwise
from typing import Any

TRADING_DAYS = 252
LAMBDA = 0.94
#: Daily closes needed for a usable forecast and for the forecast's own quality measure.
MIN_CLOSES = 30
QUALITY_HORIZON = 21
#: The rank correlation between the forecast and the volatility that followed must not be
#: confidently below this. Measured at +0.40 to +0.71 per instrument over ten years of daily bars
#: and about 0.31 out of sample after 2023; a tenth is what noise could produce.
MIN_QUALITY = 0.10
#: Fewer independent outcomes than this and the quality cannot be judged either way.
MIN_EFFECTIVE_N = 30
#: One-sided 95% bound used on both sides of the floor.
Z = 1.645


def log_returns(closes: Sequence[float]) -> list[float]:
    return [math.log(b / a) for a, b in pairwise(closes) if a > 0 and b > 0]


def ewma_series(closes: Sequence[float], lam: float = LAMBDA) -> list[float]:
    """Annualised EWMA volatility (percent) after each day's return, seeded with the first 20."""
    r = log_returns(closes)
    if len(r) < 20:
        return []
    var = sum(x * x for x in r[:20]) / 20
    out = [math.sqrt(var * TRADING_DAYS) * 100]
    for x in r[20:]:
        var = lam * var + (1 - lam) * x * x
        out.append(math.sqrt(var * TRADING_DAYS) * 100)
    return out


def forecast_vol_pct(closes: Sequence[float]) -> float | None:
    s = ewma_series(closes)
    return s[-1] if s and len(closes) >= MIN_CLOSES else None


def _rank(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def _spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 20:
        return None
    rx, ry = _rank(x), _rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / math.sqrt(sxx * syy)


def forecast_quality(closes: Sequence[float], *, horizon: int = QUALITY_HORIZON, window: int | None = None) -> dict[str, Any]:
    """Rank correlation between the forecast made on each past day and the volatility that
    actually followed it over `horizon` days, over the last `window` days with an outcome
    (all of them by default).

    Uses only forecasts whose outcome is already known, so it can be computed today without
    waiting for anything and never leaks the future into the forecast.

    Consecutive outcomes overlap by `horizon - 1` days, so `n` days carry only `n / horizon`
    independent outcomes (`effective_n`) and the correlation's standard error is about
    `1 / sqrt(effective_n)`. On the last 252 days that is twelve outcomes and +-0.3: it flagged
    SPY at -0.22 while the same forecast scored +0.54 over ten years. Quality is therefore
    judged on the longest history there is, and reported with the uncertainty it carries.
    """
    series = ewma_series(closes)
    r = log_returns(closes)
    if not series or len(r) < 20 + horizon + 20:
        return {"rank_corr": None, "n": 0, "effective_n": 0.0, "se": None}
    # series[i] is known after return index 19 + i; its outcome is the next `horizon` returns.
    forecasts, realised = [], []
    for i in range(len(series) - horizon):
        nxt = r[20 + i: 20 + i + horizon]
        if len(nxt) < horizon:
            break
        mean = sum(nxt) / horizon
        sd = math.sqrt(sum((x - mean) ** 2 for x in nxt) / (horizon - 1))
        forecasts.append(series[i])
        realised.append(sd * math.sqrt(TRADING_DAYS) * 100)
    if window is not None:
        forecasts, realised = forecasts[-window:], realised[-window:]
    n_eff = len(forecasts) / horizon
    return {
        "rank_corr": _spearman(forecasts, realised), "n": len(forecasts), "effective_n": n_eff,
        "se": (1.0 / math.sqrt(n_eff)) if n_eff >= 4 else None,
    }


def quality_status(quality: dict[str, Any], floor: float = MIN_QUALITY) -> str:
    """ACTIVE only on evidence that the forecast works; SUSPENDED only on evidence that it does not.

    * ACTIVE: even the pessimistic end of the correlation (rank_corr - 1.645 se) clears the floor.
    * SUSPENDED: even the optimistic end (rank_corr + 1.645 se) falls below it.
    * UNVERIFIED: anything in between, or too few independent outcomes. No scaling advice is given:
      a judgment is not trusted for having failed to be disproved, the rule every strategy here lives
      under. (A first draft suspended only on the second condition; a forecast with no skill at all
      scored +0.05 +- 0.07 and stayed ACTIVE, so the lifecycle could not have stopped anything.)
    """
    rc, se, n_eff = quality.get("rank_corr"), quality.get("se"), quality.get("effective_n", 0.0)
    if rc is None or se is None or n_eff < MIN_EFFECTIVE_N:
        return "UNVERIFIED"
    if rc - Z * se >= floor:
        return "ACTIVE"
    if rc + Z * se < floor:
        return "SUSPENDED"
    return "UNVERIFIED"


def regime(closes: Sequence[float], window: int = TRADING_DAYS) -> str | None:
    """Where today's forecast sits in the last year of its own forecasts: LOW, NORMAL or HIGH."""
    s = ewma_series(closes)
    if len(s) < 60:
        return None
    recent = s[-window:]
    rank = sum(1 for v in recent if v <= recent[-1]) / len(recent)
    return "LOW" if rank <= 1 / 3 else ("HIGH" if rank > 2 / 3 else "NORMAL")


def vol_scaled_fraction(vol_pct: float, target_vol_pct: float) -> float:
    """The share of a normal position a volatility target would hold: target / forecast, never above 1.

    Capped at 1 on purpose: this can only ask for *less* risk, never for leverage.
    """
    if vol_pct <= 0 or target_vol_pct <= 0:
        return 1.0
    return min(1.0, target_vol_pct / vol_pct)


class RiskJudgment:
    """Per-symbol volatility forecast with its own measured quality, cached once per day."""

    def __init__(
        self,
        daily_closes: Callable[[str], Sequence[float] | None],
        *,
        target_vol_pct: float = 12.0,
        min_quality: float = MIN_QUALITY,
        today: Callable[[], date] = date.today,
    ):
        self.daily_closes = daily_closes
        self.target_vol_pct = target_vol_pct
        self.min_quality = min_quality
        self.today = today
        self._cache: dict[str, tuple[date, dict[str, Any] | None]] = {}
        #: Why a symbol's block could not be computed, by symbol. A risk judgment that fails must
        #: leave the decision as it was, and must say that it failed.
        self.last_errors: dict[str, str] = {}

    def block(self, symbol: str) -> dict[str, Any] | None:
        """The `risk` block for the decision context, or None when it cannot be computed.

        Never raises: a risk judgment that fails must leave the decision as it was, not stop it.
        """
        stamp = self.today()
        cached = self._cache.get(symbol)
        if cached and cached[0] == stamp:
            return cached[1]
        result: dict[str, Any] | None
        try:
            closes = self.daily_closes(symbol)
            vol = forecast_vol_pct(closes) if closes else None
            if closes is None or vol is None:
                result = None
            else:
                quality = forecast_quality(closes)
                rc, n_eff = quality["rank_corr"], quality["effective_n"]
                status = quality_status(quality, self.min_quality)
                result = {
                    "forecast_vol_pct": round(vol, 2),
                    "regime": regime(closes),
                    "forecast_quality": None if rc is None else round(rc, 3),
                    "independent_outcomes": int(n_eff),
                    "status": status,
                }
                if status == "ACTIVE":
                    result["vol_scaled_fraction"] = round(vol_scaled_fraction(vol, self.target_vol_pct), 3)
        except Exception as exc:
            self.last_errors[symbol] = f"{type(exc).__name__}: {exc}"
            result = None
        self._cache[symbol] = (stamp, result)
        return result
