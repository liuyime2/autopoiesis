"""Does a volatility-targeted SPY sleeve beat buy-and-hold on risk-adjusted terms? Real history.

Pre-stated before looking (docs/PLAN, WP2): primary target 10% annualised, band 10 points, cost 5 bp
per unit of weight traded, weight never above 1 (no leverage), forecast = the production EWMA
(`autopoiesis.risk_judgment.ewma_series`, lambda 0.94, no VIX floor here - the floor can only reduce
exposure further). Other targets are reported, not chosen from. Cash earns 0% (conservative) and,
as a second column, 2% a year. Periods: 1993-2012, 2013-2022 (selection side), 2023- (read once).

Usage: PYTHONPATH=src python vol_target_eval.py <yahoo_long.pkl> <out.json>
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from autopoiesis.risk_judgment import ewma_series, vol_scaled_fraction

COST_BP = 5.0
BAND = 0.10
TARGETS = (8.0, 10.0, 12.0, 15.0)
PRIMARY = 10.0
PERIODS = {"1993-2012": ("1993-01-01", "2012-12-31"), "2013-2022": ("2013-01-01", "2022-12-31"),
           "2023-": ("2023-01-01", "2100-01-01"), "full": ("1993-01-01", "2100-01-01")}


def sleeve_returns(close: pd.Series, target: float, cash_yield: float) -> tuple[pd.Series, pd.Series]:
    ret = close.pct_change().fillna(0.0)
    vols = ewma_series(close.tolist())
    w = pd.Series(np.nan, index=close.index)
    held, weights = 0.0, []
    for i in range(len(close)):
        k = i - 20
        if k >= 0 and k < len(vols):
            goal = vol_scaled_fraction(vols[k], target)
            if abs(goal - held) > BAND or held == 0.0:
                held = goal
        weights.append(held)
    w[:] = weights
    lag = w.shift(1).fillna(0.0)  # a weight set at close t earns day t+1
    turnover = lag.diff().abs().fillna(lag.abs())
    daily_cash = (1 + cash_yield) ** (1 / 252) - 1
    out = lag * ret + (1 - lag) * daily_cash - turnover * COST_BP / 1e4
    return out, turnover


def stats(r: pd.Series) -> dict[str, float]:
    if len(r) < 60:
        return {}
    eq = (1 + r).cumprod()
    years = len(r) / 252
    return {
        "cagr_pct": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 2),
        "vol_pct": round(r.std() * np.sqrt(252) * 100, 2),
        "sharpe": round(r.mean() / r.std() * np.sqrt(252), 3) if r.std() > 0 else 0.0,
        "max_dd_pct": round(((eq / eq.cummax()) - 1).min() * 100, 2),
        "worst_year_pct": round(r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1).min() * 100, 2),
    }


def main() -> None:
    y = pd.read_pickle(sys.argv[1])
    close = y[("SPY", "adjclose")].dropna()
    result: dict[str, dict] = {"meta": {"symbol": "SPY", "first": str(close.index[0].date()),
                                        "last": str(close.index[-1].date()), "days": len(close),
                                        "cost_bp": COST_BP, "band": BAND, "primary_target": PRIMARY}}
    for cash in (0.0, 0.02):
        bh = close.pct_change().fillna(0.0)
        bh = bh + 0.0
        key = f"cash{int(cash * 100)}"
        for pname, (a, b) in PERIODS.items():
            sl = slice(a, b)
            result.setdefault(key, {}).setdefault(pname, {})["buy_and_hold"] = stats(bh[sl])
        for t in TARGETS:
            r, turn = sleeve_returns(close, t, cash)
            for pname, (a, b) in PERIODS.items():
                s = stats(r[slice(a, b)])
                s["annual_turnover"] = round(float(turn[slice(a, b)].sum() / (len(r[slice(a, b)]) / 252)), 2)
                result[key][pname][f"vt{int(t)}"] = s
    json.dump(result, open(sys.argv[2], "w"), indent=1)
    for key in ("cash0", "cash2"):
        print(key)
        for pname in PERIODS:
            print(" ", pname)
            for name, s in result[key][pname].items():
                print(f"    {name:12} {s}")


if __name__ == "__main__":
    main()
