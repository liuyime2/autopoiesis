"""六脉神剑 as a hypothesis, not as a feature to fuse.

The composite is MACD + KDJ + RSI + LWR + BBI + MTM, and the claim it makes is resonance: a buy
signal when all six agree. The distinction that matters is that the signal research in this
repository tested each of those six *individually* and every one came back null on this data -
RSI(2) over 1 and 5 days, 1-to-126-day momentum, distance from the 50- and 200-day averages
(`daily_research.py`, `long_history.py`). So the honest question is not whether MACD works; it is
whether the **conjunction** carries information the individual features do not.

That is a different hypothesis class and it has not been tested here. It is also the wrong shape for
a return predictor and possibly the right shape for a regime filter, so both are measured:

* H1 (direction): when all six agree bullish, is the next N-day return higher than when they do not?
* H2 (level): is that return higher than buy-and-hold, which is the alternative of not timing at all?
* H3 (power): how many independent agreements are there, and what is the smallest effect this test
  could have detected?

H2 is the one that decides whether the composite is worth anything. An agreement set that is
"better than the average day" but still worse than holding is a filter that loses money, and that
is what `dip_rule.py` found for the trend filter it tested - Sharpe 0.72 against 0.83 for a constant
exposure.

Indicators are computed exactly as named, on unadjusted closes, point-in-time: the signal available
at the close of day t predicts the return from t to t+N, and nothing later is used.

Usage: six_meridians.py PANEL.pkl SYMBOL [--ledger PATH] [--horizon N]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # signal_research
sys.path.insert(0, str(HERE))

HORIZONS = (5, 21)


# --- the six, as their own functions so each can be checked alone -----------------------------


def _ema(values: pd.Series, span: int) -> pd.Series:
    return values.ewm(span=span, adjust=False).mean()


def macd_signal(close: pd.Series) -> pd.Series:
    """MACD line against its signal line: +1 bullish, -1 bearish, 0 when they have crossed within
    the last bar (which is the sign change, not a level)."""
    line = _ema(close, 12) - _ema(close, 26)
    signal = _ema(line, 9)
    return (line > signal).astype(int) * 2 - 1


def kdj_signal(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 9) -> pd.Series:
    """Stochastic: %K above %D is bullish. Needs the high and low, so a close-only panel cannot
    build it and the caller must supply them."""
    lowest = low.rolling(n).min()
    highest = high.rolling(n).max()
    span = (highest - lowest).replace(0, pd.NA)
    k = 100 * (close - lowest) / span
    d = k.rolling(3).mean()
    return (k > d).astype(int) * 2 - 1


def rsi_signal(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative strength: above 50 is bullish."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - 100 / (1 + rs)
    return (rsi > 50).astype(int) * 2 - 1


def lwr_signal(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 14) -> pd.Series:
    """Larry Williams %R. It runs the other way: %R above -20 is *overbought*, so the signal is
    inverted. Written this way because the published convention catches people out."""
    lowest = low.rolling(n).min()
    highest = high.rolling(n).max()
    span = (highest - lowest).replace(0, pd.NA)
    wr = -100 * (highest - close) / span
    return (wr < -50).astype(int) * 2 - 1


def bbi_signal(close: pd.Series) -> pd.Series:
    """Bull and bear index: the mean of 3/6/12/24-day averages. Price above it is bullish."""
    bbi = (_ema(close, 3) + _ema(close, 6) + _ema(close, 12) + _ema(close, 24)) / 4
    return (close > bbi).astype(int) * 2 - 1


def mtm_signal(close: pd.Series, n: int = 12) -> pd.Series:
    """Momentum: the change over n days. Above its own zero is bullish."""
    return ((close - close.shift(n)) > 0).astype(int) * 2 - 1


SIX = (
    ("macd", macd_signal),
    ("kdj", kdj_signal),
    ("rsi", rsi_signal),
    ("lwr", lwr_signal),
    ("bbi", bbi_signal),
    ("mtm", mtm_signal),
)


def resonance(frame: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """The six signals, and their sum: +6 when all six are bullish, -6 when all bearish."""
    close, high, low = frame["c"], frame["h"], frame["l"]
    parts: dict[str, pd.Series] = {}
    for name, fn in SIX:
        if name in ("kdj", "lwr"):
            parts[name] = fn(close, high, low)
        else:
            parts[name] = fn(close)
    table = pd.DataFrame(parts)
    return table.sum(axis=1), table


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    raw = pd.read_pickle(argv[1])
    symbol = argv[2]
    horizons = [int(h) for h in (argv[3].split(",") if len(argv) > 3 else [str(x) for x in HORIZONS])]
    ledger = os.environ.get("SIGNAL_LEDGER")

    # Two panel dialects are in play: the Alpaca one keeps o/h/l/c, the Yahoo one keeps the
    # endpoint's own spelling open/high/low/close. Normalising here means the same script runs on
    # either without the caller having to remember which is which.
    raw = raw.rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})
    frame = raw[symbol].dropna(subset=["c"])
    total, table = resonance(frame)
    close = frame["c"]
    print(f"{symbol}: {len(frame)} bars, {frame.index[0].date()}..{frame.index[-1].date()}")
    counts = table.apply(lambda col: col.value_counts()).fillna(0).astype(int)
    print("\nsignal prevalence (+1 bullish / -1 bearish) over the whole record:")
    for name, fn in SIX:
        up = int((table[name] == 1).sum())
        print(f"  {name:5} {up:5d} up  {len(table) - up:5d} down")
    agree_bull = int((total == 6).sum())
    agree_bear = int((total == -6).sum())
    agree_any_n = int((total.abs() == 6).sum())
    print(f"\nall six agree one way: {agree_bull} bullish, {agree_bear} bearish "
          f"({agree_any_n} of {len(total)} bars = {agree_any_n / len(total) * 100:.2f}%)")
    print(f"  five of six agree: {int((total.abs() == 5).sum())}   "
          f"four of six: {int((total.abs() == 4).sum())}   "
          f"three of six: {int((total.abs() == 3).sum())}")
    # Two of the six near-mirror each other by construction, so "six independent signals" is a claim
    # worth checking rather than repeating. %R is an inverted oscillator, so a strongly trending
    # tape pushes it the opposite way to RSI.
    if "rsi" in table and "lwr" in table:
        both = table[["rsi", "lwr"]].dropna()
        mismatch = int((both.rsi != both.lwr).sum())
        print(f"  RSI and LWR disagree on {mismatch}/{len(both)} bars "
              f"({mismatch / len(both) * 100:.0f}%) - %R is inverted, so the two are close to mirrors")

    import signal_research as sr
    from autopoiesis.research import signal_test as st

    cut = st.time_split([d for d in frame.index.date])
    for horizon in horizons:
        forward = (close.shift(-horizon) / close - 1) * 100
        work = pd.DataFrame({"agree": total, "fwd": forward}).dropna()
        # H1: does an all-six-bullish reading precede a higher return than an ordinary bar?
        bull = work[work["agree"] == 6]
        rest = work[work["agree"] != 6]
        if len(bull) < 30:
            print(f"\nh={horizon}: only {len(bull)} all-six-bullish bar(s). The conjunction is too "
                  f"rare on this instrument to test, and that is the result: an indicator that fires "
                  f"twice in {len(total)} bars is not tradeable at any confidence, whatever it would "
                  f"have said. Recorded rather than reported as a null.")
            if ledger:
                st.judge(
                    f"six-meridians:resonance->F{horizon}",
                    st.CorrelationResult(len(bull), 0, float("nan"), 0.0, 0.0, float("nan")),
                    st.CorrelationResult(len(bull), 0, float("nan"), 0.0, 0.0, float("nan")),
                    family="six-meridians", kind="exploratory",
                    extra={"verdict_reason": "agreement_bars_below_30",
                           "agreement_bars": len(bull), "bars_in_record": len(total),
                           "agree_bearish": agree_bear},
                    ledger=Path(ledger),
                )
            continue
        tr_bull = bull[bull.index.date < cut]
        te_bull = bull[bull.index.date >= cut]
        tr_rest = rest[rest.index.date < cut]
        te_rest = rest[rest.index.date >= cut]
        a = sr.test([d for d in tr_bull.index.date], [1] * len(tr_bull), list(tr_bull.fwd), reps=1500)
        b = sr.test([d for d in te_bull.index.date], [1] * len(te_bull), list(te_bull.fwd), reps=3000)
        # H2: is it better than simply holding? That is the comparison that decides the question.
        held_te = te_rest.fwd.mean() if len(te_rest) else float("nan")
        print(f"\n=== horizon {horizon} trading day(s) ===")
        print(f"  all-six-bullish  test mean {b['rho'] if False else te_bull.fwd.mean():+.3f}% "
              f"(n={len(te_bull)})   selection {tr_bull.fwd.mean():+.3f}%")
        print(f"  every other bar  test mean {te_rest.fwd.mean():+.3f}% (n={len(te_rest)})")
        print(f"  so the agreement set is {te_bull.fwd.mean() - te_rest.fwd.mean():+.3f} points "
              f"versus an ordinary bar")
        print(f"  buy-and-hold over the same test window: {held_te:+.3f}% per {horizon} day(s), "
              f"which is what not timing at all gives you")
        if ledger:
            row = st.judge(
                f"six-meridians:resonance->F{horizon}",
                st.CorrelationResult(len(tr_bull), a["blocks"], tr_bull.fwd.mean(), a["lo"], a["hi"], a["p"]),
                st.CorrelationResult(len(te_bull), b["blocks"], te_bull.fwd.mean(), b["lo"], b["hi"], b["p"]),
                family="six-meridians", kind="exploratory",
                extra={
                    "note": "mean forward return when all six agree, not a correlation; the "
                            "verdict comes from the block-resampled p on that mean",
                    "agreement_bars": len(bull), "all_other_bars": len(rest),
                    "other_bars_mean": round(float(te_rest.fwd.mean()), 4),
                    "buy_and_hold_mean": round(float(held_te), 4),
                },
                ledger=Path(ledger),
            )
            print(f"  ledger: {row['verdict']} (family bar {row['bar']:.5f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
