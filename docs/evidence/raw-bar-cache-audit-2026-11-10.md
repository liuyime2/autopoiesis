# Raw bar caches: which earlier results could the split jumps have touched (2026-11-10)

On 2026-11-07, G4 v3 was withdrawn because its daily bars were unadjusted: XLE and XLB fall 50% on 2025-12-05, NVDA 90% on 2024-06-10, and AAPL 74% on 2020-08-31, and those falls are splits, not market moves. This file checks every other consumer of `runtime/autopoiesis/replay/` for the same defect.

## 1. Which caches carry a split jump

Every non-`adjall` JSON cache was scanned for a bar-to-bar close ratio below 0.6 or above 1.6.

| Cache family | Jumps found |
|---|---|
| AAPL `1Day`, `5Min_2010…` | 2020-08-31 ×0.26 |
| AMZN `5Min_2010…` | 2022-06-06 ×0.05 |
| AVGO `5Min_2010…` | 2024-07-15 ×0.10 |
| BIL `5Min_2010…` | 2017-11-30 ×2.0 |
| GOOGL `5Min_2010…` | 2022-07-18 ×0.05 |
| NVDA `1Day`, `5Min_2010…` | 2021-07-20 ×0.25, 2024-06-10 ×0.10 |
| XLB `1Day`, `5Min_2010…`, `5Min_2024…` | 2025-12-05 ×0.50 |
| XLE `1Day`, `5Min_2010…`, `5Min_2024…` | 2025-12-05 ×0.50 |
| SPY, QQQ, IWM, DIA, TLT, GLD, XLF, MSFT, LLY, META (all caches) | none |

Despite their names, the `*_5Min_2010-01-04_*` files hold 2,706 bars, which is daily data.

## 2. Who read them, and with what effect

| Consumer | Inputs | Verdict |
|---|---|---|
| **Live trading path** (`data_gateway`, the counterfactual ledger, the admission screen) | live bars with `adjustment="all"`; the screen scores from the journal's ledger, not from these caches | **not affected** |
| G4 v3 (`reports/20261107/v3`) | raw `1Day` caches on split symbols | **withdrawn 11-07**, replaced by v4 on `adjall` caches |
| G4 v4 (`reports/20261107/v4*`) | `*_1Day_adjall_*` | not affected |
| 4-arm runs of 11-06 (`reports/20261106/*`) | `SPY_1Day_2020-07-27_2026-11-05.json` only (per `run_manifest.json`) | not affected: SPY has no split |
| `signal-research-2026-10-08/daily_research.py` | `*_5Min_2010…` for XLE and XLB | not affected: it rescales history at any jump above 15% before computing returns |
| `signal-research-2026-10-08/signal_research.py` | `*_5Min_2024…` for XLE and XLB | **measured, no verdict changes**; see §3 |
| `intraday_events.py`, `part2/options_iv.py`, `p6-iv-residual-2026-10-14/study.py` | SPY caches; the API with `adjustment="all"` | not affected |
| `xs_research.py`, `part2/fundamentals.py` | `PANEL_ADJ.pkl` for returns; `PANEL_RAW.pkl` only for market value (an unadjusted price times reported shares, which is the correct use) | not affected |
| `part2/long_history.py`, `long_robustness.py`, `dip_rule.py` | `YAHOO.pkl` (yfinance, auto-adjusted) | not affected |

## 3. The one measured case: `signal_research.py` on XLE and XLB

Its intraday features (M*, S*, f*) never cross the close, so a split cannot reach them. The two daily hypotheses that use the overnight `gap` do include 2025-12-05 in their test period, with a gap of −50%. The test is a Spearman rank correlation with a day-block bootstrap. The test was re-run on the same caches with and without that one day (the script below, run from the repository root on real data):

| Hypothesis | ρ raw | p raw | ρ without the split day | p without |
|---|---|---|---|---|
| XLE gap → day_ret | −0.096 | 0.112 | −0.099 | 0.099 |
| XLE gap → first30 | −0.035 | 0.578 | −0.029 | 0.629 |
| XLB gap → day_ret | −0.009 | 0.881 | −0.013 | 0.809 |
| XLB gap → first30 | +0.055 | 0.388 | +0.060 | 0.336 |

The Bonferroni α is 0.05/21 = 0.0024. No hypothesis is significant either way, so none of the four verdicts changes.

```python
"""R1: does the 2025-12-05 split day change signal_research's gap hypotheses for XLE/XLB?"""
import datetime as dt, sys
import numpy as np
sys.path.insert(0, 'docs/evidence/signal-research-2026-10-08')
import signal_research as sr
for sym in ('XLE', 'XLB'):
    df = sr.load(sym); t = sr.day_table(df)
    days = sorted(df.day.unique()); cut = days[int(len(days) * 0.6)]
    te = t[t.index >= cut]; split = dt.date(2025, 12, 5)
    print(sym, 'cut', cut, 'split day in test:', split in te.index, 'gap that day:', round(float(te.gap.get(split, np.nan)), 1))
    for fy in ('day_ret', 'first30'):
        a = sr.test(np.array(te.index), te.gap.to_numpy(), te[fy].to_numpy(), reps=4000)
        k = te.drop(index=[split], errors='ignore')
        b = sr.test(np.array(k.index), k.gap.to_numpy(), k[fy].to_numpy(), reps=4000)
        print(f'  gap->{fy}: raw rho {a["rho"]:+.3f} p {a["p"]:.4f} | without split day rho {b["rho"]:+.3f} p {b["p"]:.4f}')
```

## 4. Conclusion

Apart from G4 v3, which is already withdrawn, no recorded result depends on a split jump. The raw caches stay on disk because they are inputs to the withdrawn v3 and to `fundamentals.py`. Any new research on these symbols reads the `adjall` caches (`replay.fetch_bars(..., adjustment="all")`).
