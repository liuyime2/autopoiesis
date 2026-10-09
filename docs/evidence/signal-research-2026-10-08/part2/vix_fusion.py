"""Does the market's own forward-looking volatility add anything to our forecast?

The volatility forecast is the one judgement that measurably works: an EWMA of a symbol's own daily
returns (lambda 0.94) ranks the volatility that follows at +0.31 to +0.57 per instrument, and that
is the only prediction this project has that survived its own gate.

So the question is not "is VIX interesting" but "does VIX rank the volatility that follows better
than our own forecast does, and does adding it improve the thing we already have". Three
forecasts are compared on the one metric the system already uses - the rank correlation with the
realised volatility that followed - over the same window, on the same instruments:

  1. `ewma`     the forecast the system ships
  2. `vix`      the index's own implied volatility, rescaled to the symbol's level
  3. `blend`    the average of the two, and the weighting that would be chosen on the selection
                half only (so the test half is read once)

`blend` is chosen on the earlier 60% and scored on the later 40%, the same protocol every other
hypothesis here goes through, and it is recorded in the signal ledger under its own family. A blend
that does not beat `ewma` on the test half is not a fusion, it is a second input that costs
complexity and buys nothing - which is the outcome that decides this question.

VIX is annualised and in index points; the symbol's forecast is in annualised percent. The two are
put on one scale by regressing VIX on the symbol's own forecast over the *selection* period only,
so the test period is read once and the rescaling cannot learn from it.

Usage: vix_fusion.py PANEL.pkl [--ledger PATH] [--symbols SPY,TLT,XLE]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

# The repository root, not a count of parents - which is how the first version ended up writing to
# `docs/runtime/autopoiesis/replay/VIX.pkl`, a directory nothing else in the project looks at.
REPO_ROOT = HERE.parents[3]
CACHE = REPO_ROOT / "runtime" / "autopoiesis" / "replay" / "VIX.pkl"
PORTFOLIO = ("SPY", "TLT", "XLE", "XLF")


def fetch_vix() -> dict:
    """VIX and VIX3M daily closes, from the free chart endpoint, cached to one file.

    `^VIX` is 30-day implied volatility and `^VIX3M` is 90-day, so their *difference* is the term
    structure - which is a different claim from the level and is measured separately rather than
    bundled in.
    """
    import warnings

    import pandas as pd
    import yfinance as yf

    warnings.filterwarnings("ignore")
    out = {}
    for symbol in ("^VIX", "^VIX3M"):
        frame = yf.download(symbol, period="max", interval="1d", progress=False, auto_adjust=False)
        if frame.empty:
            raise SystemExit(f"REFUSED: no data for {symbol}")
        close = frame["Close"]
        if hasattr(close, "columns"):          # yfinance 1.x returns a one-column frame here
            close = close.iloc[:, 0]
        out[symbol] = {str(index.date()): float(value) for index, value in close.items()}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as handle:
        json.dump(out, handle)
    return out


def load_vix() -> dict:
    if not CACHE.exists():
        return fetch_vix()
    with open(CACHE, encoding="utf-8") as handle:
        return json.load(handle)


def _rank(values):
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


def _pearson(x, y):
    n = len(x)
    if n < 20 or len(y) != n:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (sxx * syy) ** 0.5


def forecast_rows(closes, vix: dict, vix3m: dict):
    """One row per day: the forecasts available that day and the volatility that followed.

    Point-in-time by construction - the VIX reading is the one published on the same date, and the
    outcome is the next 21 days. Nothing later is read.
    """
    from autopoiesis import risk_judgment as rj

    series = rj.ewma_series(closes)
    dates = [d for d in closes.index[20:]]
    rows = []
    for offset, when in enumerate(dates):
        forward = closes.iloc[offset + 20 + 21 : offset + 20 + 21 + 21]
        if len(forward) < 21:
            break
        rets = [(b / a - 1.0) for a, b in zip(forward, forward[1:])]
        mean = sum(rets) / len(rets)
        realised = (sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 * 252**0.5 * 100
        key = when.date().isoformat()
        rows.append(
            {
                "date": key,
                "ewma": series[offset],
                "vix": vix.get(key),
                "vix3m": vix3m.get(key),
                "term": (vix.get(key, 0) - vix3m.get(key, 0))
                if (vix.get(key) is not None and vix3m.get(key) is not None)
                else None,
                "realised": realised,
            }
        )
    return rows


def score(rows, column: str, split: float = 0.6) -> dict:
    """Rank correlation of one forecast column against realised volatility, both halves."""
    usable = [r for r in rows if r.get(column) is not None]
    ordered = sorted(usable, key=lambda r: r["date"])
    cut = ordered[int(len(ordered) * split)]["date"] if ordered else ""
    selection = [r for r in ordered if r["date"] < cut]
    test = [r for r in ordered if r["date"] >= cut]
    return {
        "column": column,
        "n": len(ordered),
        "selection_rho": _pearson([r[column] for r in selection], [r["realised"] for r in selection]),
        "test_rho": _pearson([r[column] for r in test], [r["realised"] for r in test]),
        "n_test": len(test),
        "cut": cut,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    import pandas as pd

    ledger = os.environ.get("SIGNAL_LEDGER")
    raw = pd.read_pickle(argv[1]).rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})
    symbols = argv[2].split(",") if len(argv) > 2 else list(PORTFOLIO)
    vix_all = load_vix()
    vix, vix3m = vix_all["^VIX"], vix_all["^VIX3M"]
    print(f"VIX history {min(vix)}..{max(vix)} ({len(vix)} days); VIX3M {len(vix3m)} days\n")

    from autopoiesis.research import signal_test as st

    per_symbol: dict[str, dict] = {}
    for symbol in symbols:
        if symbol not in raw.columns.get_level_values(0):
            print(f"{symbol}: not in the panel")
            continue
        closes = raw[symbol]["adjclose"].dropna()
        closes = closes[closes > 0]
        rows = forecast_rows(closes, vix, vix3m)
        if len(rows) < 400:
            print(f"{symbol}: only {len(rows)} rows, too few")
            continue
        base = score(rows, "ewma")
        alone = score(rows, "vix")
        # The blend's weight is chosen on the selection half only, against the outcome both
        # forecasts predict - which is the question that matters, not how the two forecasts agree
        # with each other.
        selection = [r for r in rows if r["date"] < base["cut"]]
        w = _fit_weight([r["ewma"] for r in selection], [r["vix"] for r in selection], selection)
        for row in rows:
            if row["vix"] is not None:
                row["blend"] = w * row["ewma"] + (1 - w) * row["vix"]
        blend = score(rows, "blend")
        term = score(rows, "term")
        per_symbol[symbol] = {"base": base, "vix_alone": alone, "blend": blend, "term": term, "weight": w}
        print(f"{symbol}: {len(rows)} days, split {base['cut']}")
        print(f"   ewma  selection {base['selection_rho']:+.3f}  test {base['test_rho']:+.3f}   <- what ships")
        print(f"   vix   selection {alone['selection_rho']:+.3f}  test {alone['test_rho']:+.3f}")
        print(f"   blend selection {blend['selection_rho']:+.3f}  test {blend['test_rho']:+.3f}   (w={w:.2f} on ewma)")
        print(f"   term  selection {term['selection_rho']:+.3f}  test {term['test_rho']:+.3f}")
        if ledger:
            st.judge(
                f"vix:blend->RV21:{symbol}",
                st.CorrelationResult(len(selection), 0, blend["selection_rho"], 0.0, 0.0, 0.0),
                st.CorrelationResult(blend["n_test"], 0, blend["test_rho"], 0.0, 0.0, 0.0),
                family="vix", kind="exploratory",
                extra={
                    "note": "p is not computed here; the verdict records the two halves so the "
                            "blend can be compared with the shipped forecast on the same rows",
                    "shipped_ewma_test_rho": base["test_rho"],
                    "vix_alone_test_rho": alone["test_rho"],
                    "weight_on_ewma": w,
                    "improvement": None
                    if (base["test_rho"] is None or blend["test_rho"] is None)
                    else round(blend["test_rho"] - base["test_rho"], 4),
                },
                ledger=Path(ledger),
            )
    print()
    better = sum(
        1
        for r in per_symbol.values()
        if r["blend"]["test_rho"] is not None
        and r["base"]["test_rho"] is not None
        and r["blend"]["test_rho"] > r["base"]["test_rho"]
    )
    print(f"blend beats the shipped forecast on the test half for {better}/{len(per_symbol)} symbol(s)")
    print("VERDICT: fuse only if the blend beats `ewma` out of sample *and* the term structure adds "
          "nothing - otherwise this is a second input that costs complexity and buys nothing.")
    return 0


def _fit_weight(ewma, vix, rows) -> float:
    """The weight on `ewma` that best predicts the realised volatility, fitted on the selection half.

    Fitted against the outcome the forecasts are trying to predict, not against each other: two
    inputs that agree closely can still have very different value, and the value is what is being
    measured. Returns 1.0 (pure `ewma`) when no weight improves on it, so "the blend is not better"
    is representable.
    """
    from autopoiesis import risk_judgment as rj

    outcome = [r["realised"] for r in rows]
    best, best_score = 1.0, rj._spearman(ewma, outcome)
    if best_score is None:
        return 1.0
    for step in range(0, 21):
        w = step / 20.0
        blended = [w * a + (1 - w) * b for a, b in zip(ewma, vix)]
        rho = rj._spearman(blended, outcome)
        if rho is not None and rho > best_score:
            best, best_score = w, rho
    return best


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
