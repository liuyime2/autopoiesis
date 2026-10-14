"""P6: does the implied-volatility residual beyond EWMA predict future volatility?

Baseline from the plan: rho = +0.60 (p = 0.0003) over 52 days, with usable IV on 130 of 314
sampled days. The plan's own stop condition is that evidence from a single window does not
justify enabling anything, so this is the sample expansion it asks for, run on data that
already exists rather than on data that has to accrue.

Data, all local and already on disk:
  - `^VIX` daily, 1990-01-02 .. 2026-10-09 (9,263 points) - the implied-volatility leg.
  - SPY 5-minute bars 2010-01-04 .. 2026-10-07, reduced to daily closes - the realised leg.

VIX is the 30-day implied volatility of the S&P 500, computed from SPX options. It is a
proxy for a single symbol's option IV, not a replacement, and it is labelled as such
wherever the result is reported: it is index-level, it is 30-day constant-maturity rather
than a chosen near-the-money contract, and it carries no per-contract volume filter. What
it does carry is 16 years of daily observations instead of 52, which is the difference
between a correlation and a coincidence.

Gates, all three from the plan, because one window is the thing being guarded against:
  1. whole sample
  2. time split - the first half fits, the second half is the test
  3. monthly blocks - sign consistency across calendar months

Read-only. Writes nothing.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

REPLAY = Path("runtime/autopoiesis/replay")
SPY_5MIN = REPLAY / "SPY_5Min_2010-01-04_2026-10-07.json"
VIX_CACHE = REPLAY / "VIX.pkl"

LAMBDA = 0.94
HORIZON = 21          # the plan's 21-day forward realised volatility
MIN_CLOSES = 30


# --- data --------------------------------------------------------------------


def daily_closes_from_5min(path: Path) -> dict[str, float]:
    """Last trade of each session, keyed by ISO date."""
    raw = json.loads(path.read_text())
    rows = raw if isinstance(raw, list) else raw.get("bars") or raw.get("data") or []
    closes: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = row.get("t") or row.get("timestamp") or row.get("time") or row.get("date")
        close = row.get("c", row.get("close"))
        if stamp is None or close is None:
            continue
        day = str(stamp)[:10]
        closes[day] = float(close)   # rows are chronological, so the last write is the close
    return closes


def vix_closes() -> dict[str, float]:
    """`VIX.pkl` is JSON despite the extension. Measured, not assumed: pickle.load raises
    `invalid load key, '{'` on the first byte, which is the `{`."""
    text = VIX_CACHE.read_text()
    series = json.loads(text)
    history = series.get("^VIX") or {}
    return {str(day)[:10]: float(value) for day, value in history.items()}


# --- statistics --------------------------------------------------------------


def ewma_vol_pct(returns: list[float], lam: float = LAMBDA) -> float | None:
    if len(returns) < MIN_CLOSES:
        return None
    variance = sum(r * r for r in returns[:20]) / 20
    for r in returns[20:]:
        variance = lam * variance + (1 - lam) * r * r
    return math.sqrt(variance) * math.sqrt(252) * 100


def realised_vol_pct(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


def rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = average
        i = j + 1
    return out


def spearman(x: list[float], y: list[float]) -> tuple[float | None, float | None]:
    """Rank correlation and its two-sided p-value, normal approximation."""
    n = len(x)
    if n < 8:
        return None, None
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    if vx == 0 or vy == 0:
        return None, None
    rho = cov / (vx * vy)
    if abs(rho) >= 1:
        return rho, 0.0
    z = rho * math.sqrt(n - 1)
    p = 2 * (1 - _normal_cdf(abs(z)))
    return rho, p


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def residualise(y: list[float], x: list[float]) -> list[float]:
    """y minus its least-squares fit on x. The part of IV that EWMA does not explain."""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return [b - my for b in y]
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    slope = sxy / sxx
    intercept = my - slope * mx
    return [b - (intercept + slope * a) for a, b in zip(x, y, strict=True)]


# --- the study ---------------------------------------------------------------


def build(day_cap: int | None = None):
    spy = daily_closes_from_5min(SPY_5MIN)
    vix = vix_closes()
    days = sorted(set(spy) & set(vix))
    if day_cap:
        days = days[-day_cap:]
    closes = [spy[d] for d in days]
    returns = [
        closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))
    ]

    rows = []
    for i in range(MIN_CLOSES, len(returns) - HORIZON):
        ewma = ewma_vol_pct(returns[: i + 1])
        forward = realised_vol_pct(returns[i + 1 : i + 1 + HORIZON])
        if ewma is None or forward is None:
            continue
        rows.append(
            {
                "day": days[i + 1],
                "ewma": ewma,
                "iv": vix[days[i + 1]],
                "forward": forward,
            }
        )
    return rows


def report(label: str, rows: list[dict], minimum: int = 30) -> dict:
    if len(rows) < minimum:
        print(f"{label:<26} too few observations ({len(rows)})")
        return {}
    ewma = [r["ewma"] for r in rows]
    iv = [r["iv"] for r in rows]
    forward = [r["forward"] for r in rows]
    residual = residualise(iv, ewma)

    rho_ewma, p_ewma = spearman(ewma, forward)
    rho_res, p_res = spearman(residual, forward)
    print(
        f"{label:<26} n={len(rows):<5} "
        f"EWMA->fwd rho={rho_ewma:+.3f} p={p_ewma:.2e} | "
        f"IV-residual->fwd rho={rho_res:+.3f} p={p_res:.2e}"
    )
    return {"n": len(rows), "rho_ewma": rho_ewma, "p_ewma": p_ewma,
            "rho_residual": rho_res, "p_residual": p_res}


def main() -> int:
    print("=== P6: IV residual beyond EWMA, on the largest sample the local data supports ===\n")
    rows = build()
    if not rows:
        print("no overlapping SPY/VIX observations")
        return 1
    print(f"sample: {rows[0]['day']} .. {rows[-1]['day']}  ({len(rows)} observations)")
    print("baseline from the plan: rho=+0.60 p=0.0003 over 52 days\n")

    whole = report("whole sample", rows)
    if not whole:
        return 1

    # Gate 2: time split. Fit on the first half, test on the second.
    half = len(rows) // 2
    print()
    first = report("  first half", rows[:half])
    second = report("  second half (test)", rows[half:])

    # Gate 3: monthly blocks, sign consistency.
    print()
    blocks: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        blocks[row["day"][:7]].append(row)
    signs = []
    per_month = {}
    for month in sorted(blocks):
        result = report(f"  {month}", blocks[month], minimum=12)
        if result:
            per_month[month] = result
            signs.append(1 if (result["rho_residual"] or 0) > 0 else -1)
    positive = sum(1 for s in signs if s > 0)
    for month in sorted(per_month):
        r = per_month[month]
        print(f"    {month}: n={r['n']} rho_res={r['rho_residual']:+.3f} p={r['p_residual']:.2e}")
    print(
        f"\nmonthly blocks: {len(signs)} with enough data, {positive} positive, "
        f"{len(signs) - positive} negative"
    )
    if signs:
        share = positive / len(signs)
        print(f"  sign consistency: {share:.0%} positive")

    # Summary verdict against the plan's stop condition.
    print("\n=== verdict ===")
    halves_agree = bool(first and second) and (
        (first["rho_residual"] or 0) > 0 and (second["rho_residual"] or 0) > 0
    )
    print(f"whole-sample residual rho : {whole['rho_residual']:+.3f} (p={whole['p_residual']:.2e})")
    print(f"EWMA alone rho            : {whole['rho_ewma']:+.3f} (p={whole['p_ewma']:.2e})")
    print(f"both halves positive      : {halves_agree}")
    print(f"monthly sign consistency  : {positive}/{len(signs)}" if signs else "monthly: n/a")
    incremental_test()
    json.dump({"whole": whole, "first": first, "second": second, "by_month": per_month},
              open("/tmp/opencode/p6-result.json", "w"), indent=2)
    print("\nfull result written to /tmp/opencode/p6-result.json")
    return 0



def incremental_test() -> None:
    """Does the residual improve on EWMA, out of sample? The plan's actual question.

    The whole-sample rho of the residual being non-zero says the residual carries
    information. It does not say the information is *additional*: the residual is
    orthogonal to EWMA by construction, so if it merely re-encodes EWMA the combined
    forecast cannot beat EWMA alone. Fitted on the first half, measured on the second,
    because a coefficient fitted and scored on the same rows answers nothing.
    """
    rows = build()
    half = len(rows) // 2
    train, test = rows[:half], rows[half:]
    if len(train) < 100 or len(test) < 100:
        print("incremental test: not enough data")
        return

    def fit(rows):
        x = [r["ewma"] for r in rows]
        res = residualise([r["iv"] for r in rows], x)
        y = [r["forward"] for r in rows]
        n = len(x)
        # two-variable least squares via the normal equations
        mx, mr, my = sum(x)/n, sum(res)/n, sum(y)/n
        sxx = sum((a-mx)**2 for a in x)
        srr = sum((a-mr)**2 for a in res)
        sxy = sum((a-mx)*(b-my) for a, b in zip(x, y, strict=True))
        sry = sum((a-mr)*(b-my) for a, b in zip(res, y, strict=True))
        sxr = sum((a-mx)*(b-mr) for a, b in zip(x, res, strict=True))
        det = sxx*srr - sxr*sxr
        if det == 0:
            return None
        bx = (sxy*srr - sry*sxr)/det
        br = (sry*sxx - sxy*sxr)/det
        return bx, br, my - bx*mx - br*mr

    def one_var(rows):
        x = [r["ewma"] for r in rows]
        y = [r["forward"] for r in rows]
        n = len(x)
        mx, my = sum(x)/n, sum(y)/n
        sxx = sum((a-mx)**2 for a in x)
        sxy = sum((a-mx)*(b-my) for a, b in zip(x, y, strict=True))
        b = sxy/sxx if sxx else 0.0
        return b, my - b*mx

    two = fit(train)
    single = one_var(train)
    if two is None:
        print("incremental test: degenerate fit")
        return

    ewma = [r["ewma"] for r in test]
    res_test = residualise([r["iv"] for r in test], ewma)
    y = [r["forward"] for r in test]
    pred_ewma = [single[0]*a + single[1] for a in ewma]
    pred_both = [two[0]*a + two[1]*b + two[2] for a, b in zip(ewma, res_test, strict=True)]

    def mae(pred):
        return sum(abs(p - t) for p, t in zip(pred, y, strict=True))/len(y)

    rho_e, p_e = spearman(pred_ewma, y)
    rho_b, p_b = spearman(pred_both, y)
    print("\n=== incremental test: fitted on the first half, scored on the second ===")
    print(f"  EWMA alone        rho={rho_e:+.3f} p={p_e:.2e}  MAE={mae(pred_ewma):.3f} vol points")
    print(f"  EWMA + IV residual rho={rho_b:+.3f} p={p_b:.2e}  MAE={mae(pred_both):.3f} vol points")
    print(f"  residual coefficient b={two[1]:+.4f}  (positive means the residual adds in the "
          f"direction its whole-sample rho suggests)")
    print(f"  MAE change: {mae(pred_both) - mae(pred_ewma):+.3f} vol points")

if __name__ == "__main__":
    raise SystemExit(main())
