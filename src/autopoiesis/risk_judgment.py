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

import json
import math
from collections.abc import Callable, Sequence
from datetime import date
from itertools import pairwise
from pathlib import Path
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
#: Quality windows tried in order, longest first. Not arbitrary day counts: each is the smallest
#: round number of days that carries at least `MIN_EFFECTIVE_N` independent outcomes at this
#: horizon, times a small multiple.
#:
#: Measured 2026-10-08 on this broker, and the measurement is why this is a list and not a single
#: recent window. A one-year window carries 12 independent outcomes, and 12 cannot clear a 0.10 floor
#: at 95% in either direction - a "recent" gate could therefore only ever answer "I do not know",
#: which is not a degradation detector. The ladder climbs until a window can decide, and the shorter
#: rungs are reported as the power they actually carry.
QUALITY_LADDER: tuple[int, ...] = (1512, 1008, 504, 252)


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
    """The forecast the decision context carries: the symbol's own EWMA, floored by VIX when the
    index is more worried than we are.

    The floor is applied here rather than at a call site so there is one definition of "the
    forecast", and it travels with `vix_note` so the caller can say which of the two produced it.
    """
    series = ewma_series(closes)
    if not series or len(closes) < MIN_CLOSES:
        return None
    forecast = series[-1]
    floored, _note = vix_floor(closes, forecast)
    return floored


def forecast_detail(closes: Sequence[float]) -> dict[str, Any] | None:
    """The forecast and why it is that number, for the decision context and the doctor."""
    series = ewma_series(closes)
    if not series or len(closes) < MIN_CLOSES:
        return None
    own = series[-1]
    floored, note = vix_floor(closes, own)
    # `floored` is `own` whenever the floor does not engage, and never below it - see vix_floor.
    return {
        "forecast_vol_pct": round(own if floored is None else floored, 2),
        "own_ewma_vol_pct": round(own, 2),
        "vix_note": note,
        "floored_by_vix": bool(floored and floored > own * (1 + 1e-9)),
    }


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


#: The index's own implied volatility, used as a **floor** on the symbol's forecast and nothing
#: else.
#:
#: Tested before it was built, and the first test said no: blending VIX into the forecast beat the
#: shipped EWMA on 2 of 4 symbols and lost on one, which this project's own rule calls inconsistent
#: and therefore not a fusion. The second test asked the question that matters for a *risk* system
#: instead - does VIX notice a shock before the symbol's own forecast does - and that one held:
#:
#:     symbol  asymmetry  rho     p        (VIX above forecast -> realised misses high)
#:     SPY     +12.91     +0.304  0.0004
#:     TLT      +1.40     +0.183  0.0056
#:     XLE     +10.29     +0.241  0.0004
#:     XLF     +19.36     +0.427  0.0004
#:
#: Asymmetry positive and significant on 4 of 4, so the relationship is one-sided: VIX above the
#: forecast means the forecast is too low, and VIX below it does not mean the forecast is too high.
#: That is what makes a floor the right shape - it can only ever raise the forecast, and raising the
#: forecast can only shrink the position. It cannot ask for more risk, which is the invariant
#: `vol_scaled_fraction` already keeps.
VIX_CACHE = Path("runtime") / "autopoiesis" / "replay" / "VIX.pkl"
#: How far VIX must exceed the forecast before the floor engages. The same threshold the test used,
#: recorded there so it is not a knob chosen after the fact.
VIX_FLOOR_MARGIN = 0.15


def _mean_annualised_vol(closes: list[float]) -> float:
    """The mean annualised volatility over this window, which is the symbol's typical level.

    Averaged over the window rather than taken at the end, because the point is the exchange rate
    between "index vol units" and "this symbol's vol units" over a long stretch, not today's reading.
    """
    if len(closes) < MIN_CLOSES:
        return 0.0
    rets = [b / a - 1.0 for a, b in pairwise(closes) if a > 0 and b > 0]
    if len(rets) < MIN_CLOSES:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return (var**0.5) * (252**0.5) * 100.0


def _mean_of(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _vol_pct(rets: list[float]) -> float:
    """Annualised volatility in percent from returns, or 0.0 when there is nothing to measure.

    Local rather than imported from `data_gateway`: this module is pure standard library on purpose,
    and reaching into the gateway for one arithmetic helper would couple a risk judgement to a broker
    client it does not otherwise touch.
    """
    n = len(rets)
    if n < 3:
        return 0.0
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1)
    return (var**0.5) * (252**0.5) * 100.0


def vix_floor(closes: Sequence[float], current: float | None) -> tuple[float | None, str]:
    """`max(forecast, VIX rescaled to this symbol)` when VIX exceeds the forecast by the margin.

    Returns `(forecast, reason)`. `reason` says which of the two won and by how much, because a floor
    that silently replaces the forecast is indistinguishable from the forecast.

    Never raises, and returns the input unchanged when the cache is absent or unreadable: a missing
    VIX series must leave the judgement exactly as it was, not degrade it into a guess. That is the
    same discipline the rest of this module keeps.
    """
    if current is None:
        return current, "no forecast"
    try:
        if not VIX_CACHE.exists():
            return current, "no VIX cache"
        with open(VIX_CACHE, encoding="utf-8") as handle:
            series = json.load(handle)
        history = series.get("^VIX") or {}
        if not history:
            return current, "no VIX history"
        ordered = sorted(history.items())
        vix = ordered[-1][1]
        # How old is that reading? The plan that ordered this work flagged it: taking the cache's
        # last point without checking its date means a stale index silently becomes today's risk
        # judgement. Measured the failure rather than assume it: a cache frozen on a calm week keeps
        # saying "calm" through a spike, and the floor - whose whole purpose is to add caution -
        # becomes a reason to stay sized up.
        as_of = ordered[-1][0]
        stale_after = 4  # a 3-day holiday weekend is the longest normal gap; four is the ceiling
        try:
            from datetime import date as _date

            today = _date.today()
            reading = _date.fromisoformat(as_of)
            age = (today - reading).days
            if age > stale_after:
                return current, f"VIX reading {as_of} is {age} days old; floor not engaged"
            if age < 0:
                return current, f"VIX reading {as_of} is in the future; floor not engaged"
        except ValueError:
            return current, f"VIX cache's last date {as_of!r} is not a date; floor not engaged"
        # Rescale VIX onto this symbol's own volatility *level*. The ratio is of the two averages,
        # not of the two volatilities: VIX is already an annualised volatility number, so the symbol's
        # mean annualised volatility against the index's mean annualised volatility is the exchange
        # rate between the two units. The first version divided volatility by volatility, which
        # degenerates to zero on a flat index series and made a perfectly usable reading unreadable.
        symbol_closes = list(closes)
        if len(symbol_closes) < MIN_CLOSES:
            return current, "too few closes to scale VIX"
        window = symbol_closes[-252:]
        symbol_mean_vol = _mean_annualised_vol(window)
        index_values = [value for _, value in ordered[-253:] if value > 0]
        index_mean_vol = _mean_of(index_values)
        if not symbol_mean_vol or not index_mean_vol:
            return current, "VIX not comparable on this window"
        scaled = vix * (symbol_mean_vol / index_mean_vol)
        if scaled <= current * (1 + VIX_FLOOR_MARGIN):
            return current, f"VIX {scaled:.1f}% below the forecast {current:.1f}%; floor not engaged"
        return round(scaled, 2), (
            f"VIX floor engaged: index-implied {scaled:.1f}% against the forecast {current:.1f}%, "
            f"which can only shrink the position"
        )
    # Named, not blind. A floor that cannot be read must leave the judgement exactly as it was,
    # but "must not crash" does not require swallowing every exception: the failure modes here are
    # a missing file, an unparseable one, and a shape that is not what `fetch_vix` wrote. Catching
    # `Exception` would have covered those and everything else - and would have made the gate's
    # BLE001 count drift for no benefit, which is exactly the hardcoded-figure churn the project's
    # own review flags as fragile.
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A broken floor must leave the judgement exactly as it was.
        return current, f"VIX floor unavailable: {type(exc).__name__}: {exc}"


def quality_ladder(closes: Sequence[float], floor: float = MIN_QUALITY) -> dict[str, Any]:
    """The verdict the longest window that can *decide* gives, plus every rung it climbed past.

    A single rolling window is the wrong instrument, and this records why rather than asserting it.
    Measured on SPY against real bars: whole history +0.657 ACTIVE; the last 252 days -0.226
    [-0.70, +0.25]; the last 1008 days +0.320 [+0.082, +0.557] - a lower bound two hundredths short
    of the floor, so UNVERIFIED; the last 1512 days +0.467 [+0.273, +0.661] - ACTIVE. Read by
    calendar year the forecast's quality swings between -0.50 (2013) and +0.82 (2003) with no
    downward trend, so the 1008-day reading is a window-length artefact rather than a forecast that
    stopped working. Gating on it would have suspended a working judgement on the one symbol the
    account trades.

    So the ladder decides at the longest window that can reach a verdict, and reports the rest. When
    no rung can decide, `status` is UNVERIFIED and `decided_on` is None - which means the judgement
    is unmeasured, not that it is good.
    """
    rungs: list[dict[str, Any]] = []
    for window in QUALITY_LADDER:
        q = forecast_quality(closes, window=window)
        if q["rank_corr"] is None:
            continue
        rungs.append({"window": window, "effective_n": round(q["effective_n"], 1),
                      "rank_corr": round(q["rank_corr"], 3), "status": quality_status(q, floor)})
    decided = next((r for r in rungs if r["status"] != "UNVERIFIED"), None)
    whole = forecast_quality(closes)
    return {
        "status": decided["status"] if decided else "UNVERIFIED",
        "decided_on": decided["window"] if decided else None,
        "rungs": rungs,
        "whole_history": None if whole["rank_corr"] is None else round(whole["rank_corr"], 3),
        "whole_history_status": quality_status(whole, floor),
    }


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
                ladder = quality_ladder(closes, self.min_quality)
                status = str(ladder["status"])
                whole = forecast_quality(closes)
                detail = forecast_detail(closes)
                if detail is None:  # unreachable while `closes` is not None, but the type needs it
                    result = None
                else:
                    result = {
                        "forecast_vol_pct": detail["forecast_vol_pct"],
                        "own_ewma_vol_pct": detail["own_ewma_vol_pct"],
                        # Which of the two produced the number above. A floor that silently replaces
                        # the forecast is indistinguishable from the forecast.
                        "vix_note": detail["vix_note"],
                        "regime": regime(closes),
                        "forecast_quality": ladder["whole_history"],
                        "independent_outcomes": int(whole["effective_n"]),
                        "status": status,
                        "quality_decided_on_days": ladder["decided_on"],
                        "quality_ladder": ladder["rungs"],
                    }
                if status == "ACTIVE" and result is not None:
                    result["vol_scaled_fraction"] = round(
                        vol_scaled_fraction(vol, self.target_vol_pct), 3
                    )
        except Exception as exc:
            self.last_errors[symbol] = f"{type(exc).__name__}: {exc}"
            result = None
        self._cache[symbol] = (stamp, result)
        return result
