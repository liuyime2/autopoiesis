# v6 look 1: interpretation (2026-11-24)

`look1.{json,md}` were opened once (exclusive create) at commit 5488b95 against spec `cf952923…`, already registered and verified. Those two files are not modified; this note only interprets them.

## Result

| | Value |
|---|---|
| Sessions | 2025-11-03 .. 2026-11-20 (265), 53 rebalances, mean turnover 12.4% |
| Excess over all-member equal weight (after cost) | **−9.65%/yr** |
| 95% bootstrap CI | [−85.06%, +73.33%]/yr |
| p (two-sided) | 0.931; required < 0.0167 |
| Verdict | **not PROVEN**. CI_high > 0, so the negative close is not triggered either. Per the spec, continue to look 2 (2028-11-17). |

The prior power was 5.9% (assuming IR 0.83). This result is within expectation and supports no profitability claim.

## Why the interval is so wide: real volatility, not a data error

- **Tracking error:** 45.0%/yr over the window, against 18.3% in development. The days with the largest daily excess (7/30 +13%, 7/02 −8%, 11/05 −8% …) come from the momentum names held at the time: MUU (2× MU), IREN, CIFR, NBIS, AXTI, SNDK, AAOI and similar AI, semiconductor and crypto-mining stocks, with single-day moves of 15–36%.
- **Data check:** on 7/21, 7/28 and 7/30, MUU's daily return is exactly 2.0× MU's (0.24/0.12, −0.18/−0.088, 0.362/0.184). The bars are consistent; this is not a bad bar.
- **Consequence:** momentum top-tenth TE is strongly regime-dependent. At 45% TE, even the development excess of 14%/yr would be IR ≈ 0.3. The look-2/look-3 power in the spec (16.9% / 29.5%, assuming IR 0.83) should be read as an **upper bound**.

## A side defect found: the universe contains ETFs (diagnosed, no look changed)

- **What:** `tools/fetch_universe_bars.py` keeps every `us_equity` on a listed exchange. The broker's asset listing has no ETF flag, so ETF/ETNs are included. By name, they are 8–11 of the top 500 in 2021–23 and 19–22 in 2025–26, of which 0–4 are leveraged or inverse. This contradicts the spec's wording "listed equities".
- **Effect, diagnostic only** (ETFs removed from the members, not a retest):

  | Window | As registered | ETFs removed | TE |
  |---|---:|---:|---:|
  | Development | +13.83% | +15.46% | 18.3% → 18.4% |
  | Look 1 | −9.65% | −10.95% | 45.0% → 45.3% |

  ETFs average 0.6% of the held tenth, at most 4%. The defect does not change the look-1 conclusion.
- **Handling:** the v6 spec freezes the universe procedure ("no parameter changes between looks"). Looks 2 and 3 keep the same procedure, including ETFs, so the three looks remain comparable. Excluding ETFs belongs only to a new family registered in future, which must use a reliable ETF identification and not the name regex used here.

## Where the overall goal stands

- No strategy has yet been proven profitable.
- v6 continues accumulating evidence on future data. The next look is 2028-11-17.
- At the current data scale (daily bars, top 500, since 2020-07), a single factor with IR ≲ 0.9 cannot be confirmed statistically within a year. This conclusion now has three independent supports: the power study, the probe, and look 1.
