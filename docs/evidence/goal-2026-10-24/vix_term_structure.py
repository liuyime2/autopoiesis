"""Does the VIX term structure add to the volatility forecast the system already has? Real data.

One hypothesis, stated before the run: adding slope = log(VIX3M / VIX) to a regression of the next
21 trading days' realised volatility on the production EWMA forecast and VIX lowers out-of-sample
squared error of log-volatility. Fit on dates before 2018-01-01, scored on 2018-01-01 onward, read
once. The interval for the improvement resamples calendar months (21-day outcomes overlap, so days
are not independent). Data: SPY adjusted closes (Yahoo, 2006-07-17 onward where VIX3M exists), ^VIX and
^VIX3M daily closes from the cached files. No VIX floor logic, no trading.

Usage: PYTHONPATH=src python vix_term_structure.py <YAHOO.pkl> <VIX.json> <out.json>
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from autopoiesis.risk_judgment import ewma_series

SPLIT = pd.Timestamp("2018-01-01")
HORIZON = 21


def build(yahoo: str, vix_path: str) -> pd.DataFrame:
    close = pd.read_pickle(yahoo)[("SPY", "adjclose")].dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    vix = json.load(open(vix_path))
    v1 = pd.Series(vix["^VIX"]); v1.index = pd.to_datetime(v1.index)
    v3 = pd.Series(vix["^VIX3M"]); v3.index = pd.to_datetime(v3.index)
    ewma = pd.Series(ewma_series(close.tolist()), index=close.index[20:])  # annualised %, known at that close
    logret = np.log(close).diff()
    fwd = logret[::-1].rolling(HORIZON).std()[::-1].shift(-1) * np.sqrt(252) * 100  # next 21 days
    df = pd.DataFrame({"ewma": ewma, "vix": v1, "vix3m": v3, "y": fwd}).dropna()
    df["slope"] = np.log(df.vix3m / df.vix)
    return df


def fit(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    x = np.column_stack([np.ones(len(df))] + [df[c] for c in cols])
    return np.linalg.lstsq(x, np.log(df.y), rcond=None)[0]


def sq_err(df: pd.DataFrame, cols: list[str], beta: np.ndarray) -> pd.Series:
    x = np.column_stack([np.ones(len(df))] + [df[c] for c in cols])
    return pd.Series((np.log(df.y) - x @ beta) ** 2, index=df.index)


def main() -> None:
    df = build(sys.argv[1], sys.argv[2])
    df["l_ewma"], df["l_vix"] = np.log(df.ewma), np.log(df.vix)
    sel, test = df[df.index < SPLIT], df[df.index >= SPLIT]
    base_cols, full_cols = ["l_ewma", "l_vix"], ["l_ewma", "l_vix", "slope"]
    b_base, b_full = fit(sel, base_cols), fit(sel, full_cols)
    gain = sq_err(test, base_cols, b_base) - sq_err(test, full_cols, b_full)  # >0: the slope helped
    months = gain.groupby([gain.index.year, gain.index.month]).agg(["sum", "count"])
    rng, boots = np.random.default_rng(7), []
    for _ in range(4000):
        pick = rng.integers(0, len(months), len(months))
        boots.append(months["sum"].to_numpy()[pick].sum() / months["count"].to_numpy()[pick].sum())
    lo, hi = np.quantile(boots, [0.025, 0.975])
    mse_base = float(sq_err(test, base_cols, b_base).mean())
    out = {
        "selection": [str(sel.index[0].date()), str(sel.index[-1].date()), len(sel)],
        "test": [str(test.index[0].date()), str(test.index[-1].date()), len(test)],
        "slope_coefficient_selection": round(float(b_full[3]), 4),
        "test_mse_without_slope": round(mse_base, 5), "test_mse_with_slope": round(float(sq_err(test, full_cols, b_full).mean()), 5),
        "mean_improvement": round(float(gain.mean()), 6), "ci95": [round(float(lo), 6), round(float(hi), 6)],
        "relative_improvement_pct": round(float(gain.mean()) / mse_base * 100, 2),
        "verdict": "SLOPE_HELPS" if lo > 0 else ("SLOPE_HURTS" if hi < 0 else "NO_DETECTABLE_EFFECT"),
    }
    json.dump(out, open(sys.argv[3], "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
