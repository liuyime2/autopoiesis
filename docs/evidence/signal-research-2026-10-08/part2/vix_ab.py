"""Does the VIX floor actually help, measured on the same basis as the thing it changes?

The floor is live. It was fused because `vix_shock.py` showed a one-sided relationship on 4 of 4
instruments - VIX above the forecast means realised volatility misses high - and because a floor can
only raise the forecast and raising the forecast can only shrink the position. Both of those are
statements about the *forecast*, not about the *outcome*.

So this measures the outcome: the same bars, the same cost, the same window, the same target
volatility, with the floor on and with it off. Report return, max drawdown, Sharpe, the share of
capital deployed, and how many days the floor engaged - because a floor that engages on 2% of days
is a floor that does nothing, and one that engages on 60% is a floor that has quietly replaced the
forecast.

Pre-stated before the run, and both arms are reported whatever they say.

Usage: vix_ab.py PANEL.pkl [--symbols SPY,TLT,XLE,XLF]
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

COST = 0.00025  # one way, per unit of exposure traded - the project's own assumed round trip
PORTFOLIO = ("SPY", "TLT", "XLE", "XLF")


def run_arm(closes, target: float, *, floor: bool, vix: dict) -> dict:
    """One arm of the comparison: the same exposure rule, with or without the VIX floor."""
    from autopoiesis import risk_judgment as rj

    series = rj.ewma_series(closes)
    if not series:
        return {}
    dates = list(closes.index[20:])
    weights, notes = [], []
    history = sorted(vix.items())          # the index's own daily readings, point in time
    for offset in range(len(series)):
        forecast = series[offset]
        note = "own ewma"
        if floor:
            # The reading for *this* day, not the cache's last one. Judging every historical day
            # against today's index is a backfill, and it shows up as the floor engaging on
            # ~100% of days - which is what the first run of this script measured before the fix.
            day = dates[offset].date().isoformat()
            prior = [v for d, v in history if d <= day]
            forecast, note = rj.vix_floor(
                closes[: 20 + offset + 1], forecast, prior[-1] if prior else None
            )
        weights.append(min(1.0, target / forecast))
        notes.append(note)
    weight = pd_series(weights, dates).shift(1)
    price = pd_series(list(closes), list(closes.index))
    returns = price.pct_change().reindex(weight.index).fillna(0.0)
    net = (weight * returns - weight.diff().abs().fillna(0) * COST).dropna()
    nav = (1 + net).cumprod()
    # "floor not engaged" contains "engaged", so the first version of this counted every day.
    engaged = sum(1 for n in notes if "floor engaged" in n)
    return {
        "cagr": round((nav.iloc[-1] ** (252 / len(net)) - 1) * 100, 2),
        "sharpe": round(net.mean() / net.std() * 252**0.5, 3) if net.std() > 0 else None,
        "max_drawdown": round((nav / nav.cummax() - 1).min() * 100, 2),
        "mean_exposure": round(float(weight.mean()), 4),
        "days": len(net),
        "floor_engaged_days": engaged,
        "floor_engaged_pct": round(engaged / max(1, len(notes)) * 100, 2),
    }


def pd_series(values, index):
    import pandas as pd

    return pd.Series(values, index=index)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    import pandas as pd
    import vix_fusion as vf

    vix = vf.load_vix()["^VIX"]
    raw = pd.read_pickle(argv[1]).rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})
    symbols = argv[2].split(",") if len(argv) > 2 else list(PORTFOLIO)
    target = float(__import__("os").environ.get("AB_TARGET_VOL", "12"))

    print(f"target volatility {target:.0f}%, cost {COST * 100:.3f}% per unit of exposure per side\n")
    print(f"{'sym':5} {'arm':10} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>8} {'exp':>6} {'floor on':>9}")
    rows = []
    for symbol in symbols:
        if symbol not in raw.columns.get_level_values(0):
            print(f"{symbol}: not in the panel")
            continue
        closes = raw[symbol]["adjclose"].dropna()
        closes = closes[closes > 0]
        off = run_arm(closes, target, floor=False, vix=vix)
        on = run_arm(closes, target, floor=True, vix=vix)
        rows.append((symbol, off, on))
        for label, arm in (("no floor", off), ("VIX floor", on)):
            print(f"{symbol:5} {label:10} {arm['cagr']:7.2f} {arm['sharpe']!s:>7} "
                  f"{arm['max_drawdown']:8.2f} {arm['mean_exposure']:6.3f} "
                  f"{str(arm.get('floor_engaged_pct', '-')) + '%':>9}")

    print()
    better_dd = sum(1 for _, o, n in rows if n["max_drawdown"] > o["max_drawdown"])
    better_sh = sum(1 for _, o, n in rows if (n["sharpe"] or 0) > (o["sharpe"] or 0))
    worse_ret = sum(1 for _, o, n in rows if n["cagr"] < o["cagr"])
    print(f"shallower drawdown on {better_dd}/{len(rows)}   higher Sharpe on {better_sh}/{len(rows)}   "
          f"lower return on {worse_ret}/{len(rows)}")
    engaged = [r[2]["floor_engaged_pct"] for r in rows]
    print(f"floor engaged on {min(engaged):.2f}%-{max(engaged):.2f}% of days across the panel")
    print("\nA floor is doing its job when it cuts drawdown at a stated cost in return. A floor that")
    print("never engages is inert, and one that engages most days has replaced the forecast rather")
    print("than guarding it - the engagement percentage is what tells those apart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
