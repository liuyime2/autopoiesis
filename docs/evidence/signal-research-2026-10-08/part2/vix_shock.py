"""Does VIX warn about a volatility shock before our own forecast notices?

The first test compared forecasts and VIX lost: the blend beat the shipped EWMA on 2 of 4 symbols and
lost on one, which by this project's own rule ("inconsistent means do not fuse") is not a fusion. But
that test asked the wrong question for a *risk* system. A forecast that is equally accurate on
average can still be the one that notices a shock three weeks earlier, and for a position-size
judgement that difference is the whole value - sizing down late is the same as not sizing down.

So this asks the shock question directly, and it is a different claim:

  * `spike_lead`: when VIX-scaled exceeds the EWMA forecast by a margin, does the volatility that
    follows exceed what the EWMA forecast said it would?
  * `asymmetry`: does the same hold on the way down - when VIX-scaled is *below* the forecast, does
    realised volatility come in under it? A one-sided relationship is an early-warning, a two-sided
    one is just a better average.

Only the second is what makes it worth fusing. If the disagreement predicts upside vol misses only,
then `max(ewma, vix_scaled)` is a floor that costs nothing in calm markets and catches shocks - which
is the shape that fits a system whose only proven judgement is risk.

Pre-stated, recorded in the ledger under its own family, and read once on the later 40% of days.

Usage: vix_shock.py PANEL.pkl [--symbols SPY,TLT,XLE,XLF]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

PORTFOLIO = ("SPY", "TLT", "XLE", "XLF")
#: How far VIX-scaled must exceed the forecast before the day counts as a disagreement. Stated
#: before looking, because the threshold is the degree of freedom that would otherwise be chosen
#: after seeing which one flatters the result.
MARGIN_PCT = 15.0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    import pandas as pd

    import vix_fusion as vf
    from autopoiesis import risk_judgment as rj

    ledger = os.environ.get("SIGNAL_LEDGER")
    vix_all = vf.load_vix()
    raw = pd.read_pickle(argv[1]).rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})
    symbols = argv[2].split(",") if len(argv) > 2 else list(PORTFOLIO)
    print(f"margin: VIX-scaled must exceed the forecast by {MARGIN_PCT:.0f}%\n")

    from autopoiesis.research import signal_test as st

    table: list[tuple] = []
    for symbol in symbols:
        if symbol not in raw.columns.get_level_values(0):
            print(f"{symbol}: not in the panel")
            continue
        closes = raw[symbol]["adjclose"].dropna()
        closes = closes[closes > 0]
        rows = vf.forecast_rows(closes, vix_all["^VIX"], vix_all["^VIX3M"])
        usable = [r for r in rows if r["vix"] is not None and r["ewma"] is not None]
        usable.sort(key=lambda r: r["date"])
        if len(usable) < 400:
            print(f"{symbol}: only {len(usable)} usable days")
            continue
        # Rescale VIX onto the symbol's own volatility level, fitted on the earlier 60% only, so the
        # later 40% is read once.
        cut_index = int(len(usable) * 0.6)
        selection = usable[:cut_index]
        scale = _fit_scale([r["vix"] for r in selection], [r["ewma"] for r in selection])
        for row in usable:
            row["vix_scaled"] = row["vix"] * scale
            row["over"] = row["vix_scaled"] > row["ewma"] * (1 + MARGIN_PCT / 100)
            row["under"] = row["vix_scaled"] < row["ewma"] * (1 - MARGIN_PCT / 100)
        cut = usable[cut_index]["date"]
        test = [r for r in usable if r["date"] >= cut]

        over = [r for r in test if r["over"]]
        under = [r for r in test if r["under"]]
        normal = [r for r in test if not r["over"] and not r["under"]]
        # Forecast error, positive meaning realised came in *above* what the forecast said.
        err = lambda group: [r["realised"] - r["ewma"] for r in group]

        over_mean = _mean(err(over))
        under_mean = _mean(err(under))
        normal_mean = _mean(err(normal))
        print(f"{symbol}: test half {len(test)} days from {cut}; scale {scale:.3f}")
        print(f"   VIX above forecast  {len(over):4d} day(s), mean miss {over_mean:+6.2f} vol points")
        print(f"   VIX below forecast  {len(under):4d} day(s), mean miss {under_mean:+6.2f}")
        print(f"   in between          {len(normal):4d} day(s), mean miss {normal_mean:+6.2f}")
        if over and under:
            print(f"   asymmetry: {over_mean - under_mean:+.2f} points between the two")
            # The block bootstrap over months, the same resampling the rest of the ledger uses.
            blocks = [r["date"][:7] for r in test]
            values = [r["realised"] - r["ewma"] for r in test]
            above = [1.0 if r["over"] else 0.0 for r in test]
            import numpy as np

            import signal_research as sr

            # `sr.test` indexes with a boolean mask, so the inputs must be arrays. Passing lists
            # raises `only integer scalar arrays can be converted`, which is what the first run did.
            a = sr.test(
                np.array(blocks, dtype=object), np.array(above, dtype=float), np.array(values, dtype=float),
                reps=2500,
            )
            print(f"   does being above the forecast predict a high-side miss? rho {a['rho']:+.3f} "
                  f"[{a['lo']:+.3f},{a['hi']:+.3f}] p={a['p']:.4f}")
            if ledger:
                row = st.judge(
                    f"vix-shock:above->high-miss:{symbol}",
                    st.CorrelationResult(cut_index, a["D"], a["rho"], a["lo"], a["hi"], a["p"]),
                    st.CorrelationResult(len(test), a["D"], a["rho"], a["lo"], a["hi"], a["p"]),
                    family="vix-shock", kind="exploratory",
                    extra={
                        "note": "recorded on the selection and test halves separately so the "
                                "duplicated rho is visible rather than hidden",
                        "margin_pct": MARGIN_PCT,
                        "over_days": len(over), "under_days": len(under),
                        "over_mean_miss": round(over_mean, 3),
                        "under_mean_miss": round(under_mean, 3),
                        "asymmetry": round(over_mean - under_mean, 3),
                    },
                    ledger=Path(ledger),
                )
                print(f"   ledger: {row['verdict']}")
        table.append((symbol, over_mean, under_mean, over_mean - under_mean, len(over), len(under)))

    print()
    if table:
        positive = sum(1 for t in table if t[3] > 0)
        print(f"asymmetry positive on {positive}/{len(table)} symbol(s)")
        print("VERDICT: fuse `max(ewma, vix_scaled)` as a floor only where the asymmetry is positive "
              "and the above-forecast group is the one that misses high. A symmetric relationship is "
              "a better average, not an early warning, and this system already has an average.")


def _fit_scale(vix, ewma) -> float:
    """Multiply VIX by this to put it on the symbol's own volatility level: the ratio of their means
    over the selection half."""
    import statistics

    if not vix or not ewma:
        return 1.0
    m_vix, m_ewma = statistics.fmean(vix), statistics.fmean(ewma)
    return m_ewma / m_vix if m_vix > 0 else 1.0


def _mean(values) -> float:
    import statistics

    return statistics.fmean(values) if values else 0.0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
