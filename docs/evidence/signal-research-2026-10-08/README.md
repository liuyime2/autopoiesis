# Why no brain showed judgment, and what does (2026-10-08)

The model-zoo comparison found that no local language model, of any size or family, and no
combination of them, predicted SPY's next hour. The question this answers: is that the system's
setup, the length of the context, or something else - and is there any method of judgment that
works and can improve itself?

Everything is read-only research on real Alpaca bars and Benzinga headlines. Nothing was ordered.

## The short answer

1. **It is not the context, and it is not the setup.** The measurement was under-powered (below),
   so it was repeated with enough power: 276 independent test days of 5-minute SPY bars, ten
   years of daily bars for eight ETFs, 120 large-cap stocks, 18,873 headlines. With the power, the
   answer is the same. **No simple feature of past prices predicts the direction of the next
   hour, day, week or month**: of 47 hypotheses stated in advance, none carried a direction. One
   survived, and it is not about direction (volatility persists); one cleared the bar and was
   confounded (high-volatility stocks earning more was beta).
2. **The task itself has no extractable direction at these horizons**, so a model, however large
   or well informed about prices, has nothing to find. This is what an efficient market predicts.
3. **A language model does have judgment where there is something to read: text.** It reads
   headlines correctly (the direction of the overnight move, rho +0.11 to +0.15), about as well as
   a keyword rule by rank correlation and better at the extremes, and not measurably better as it
   gets larger (a 0.8B decision model, a 9B one and a 14B general model are indistinguishable).
   But the market has priced the story by the time an agent running on five-minute cycles can
   act, so the judgment does not turn into a tradable edge.
4. **What does persist is risk.** Volatility is forecastable on every instrument tested. That is a
   judgment with a measurable accuracy, a use (smaller drawdowns), and a way to know when it has
   stopped working. It is what the system now carries, and it is what can evolve: a judgment is
   kept while the evidence says it works, and not otherwise.

## What was wrong with the measurement

| Finding | Number |
|---|---|
| The benchmark's 289 overlapping 1-hour outcomes are about 72 independent ones | only correlations above ~0.24 were detectable |
| A realistic intraday effect is 0.02-0.05 | needs 1,600-10,000 independent outcomes |
| The agent's `market` block, built from its own sparse journal, was empty or UNKNOWN | 23% of prompts (regime, trend, volatility), 17% (1-day return) |
| The strategy search tried only "always buy" and a fixed-price trigger | 70 trials, 0-4 out-of-sample trades each against 34-84 required |

So the earlier null was uninformative, not wrong. The question was whether more power changes it.

## Data

Alpaca historical bars (IEX feed): 5-minute SPY, 2024-01 to 2026-10 (690 full sessions, regular
hours only - the raw file includes pre- and after-market, which would have put an overnight gap
inside "the last hour"); daily bars adjusted for splits and dividends, 2016-01 to 2026-10, for
SPY, QQQ, IWM, DIA, TLT, XLF, XLE, XLB and 120 large-cap stocks; Benzinga headlines through the
Alpaca news endpoint. **Survivorship:** the stocks are today's large caps, which flatters anything
that picks past winners; it cannot be removed with this data.

## The protocol (the same for every hypothesis)

Hypotheses were listed before looking and none was added afterwards. The earlier 60% of days
select, the later 40% test, read once. The interval and p-value resample whole days or months
(neighbouring bars share their session, and a bar-level interval is far too narrow). The bar for
"significant" divides 0.05 by every hypothesis ever recorded. A survivor must keep its sign
between the two periods and, where it shares a driver with what it predicts, hold once that
driver is removed. This is `src/autopoiesis/research/signal_test.py`; the ledger holds all 61 (47 price hypotheses and 14 tradable news tests).

## 1. Intraday, SPY (17 testable hypotheses, `signal_research.py`)

Momentum and reversal over 1-48 bars, distance from a 12-bar average, the first half hour against
the last, the overnight gap; forward returns of 1 and 4 hours inside the session. Four
combinations (a 48-bar lookback with a 48-bar horizon, and the like) cannot exist inside one
78-bar session and were dropped. Test period: 276 sessions from 2025-09-02.

All 17: |rho| at most 0.074 on the test period, every interval contains zero, none survives.
The short-lookback tests are powerful (an interval of about +-0.015 to +-0.03 around 1-bar to
6-bar momentum, so an effect of the size usually reported would have shown); the long-lookback
ones are not (about +-0.08 to +-0.12).

## 2. Daily, eight ETFs (18 hypotheses, `daily_research.py`)

Short-term reversal, 1-6-month momentum, distance from the 50- and 200-day averages, RSI(2),
volatility against the later return, volatility against later volatility. Test: 2023-2026.

- **No direction.** The longer-horizon momentum and trend measures are *negatively* related to
  the following month in 2023-2026 on all eight ETFs (SPY: -0.32 for the 6-month return against
  the next 21 days), but individually p = 0.01-0.02 against a bar of 0.003, about -0.03 in the
  selection years, and eight ETFs that rise and fall together are one observation, not eight. One
  bull-market regime, not a rule.
- **Volatility persists.** The last 21 days' volatility against the next 21 days': rho +0.57
  (+0.67 in the selection years), p = 0.0003. Per instrument the range is +0.10 to +0.60 (the
  pooled figure is inflated by TLT being calmer than XLE; the per-instrument one is the honest one).

## 3. Cross-section, 120 stocks (12 hypotheses, `xs_research.py`)

Each day the stocks are ranked on a feature and the rank correlation with the later return is
averaged (943 test days). 1-day, 5-day and 1-month reversal, 12-1 and 6-1 momentum, 52-week-high
proximity, RSI(2): nothing. Trailing volatility against the next month: IC +0.08 to +0.09,
p = 0.0003, same sign in both periods - and **confounded**. It was +0.16 in months the market rose
and -0.09 in months it fell, and +0.019 [-0.027, +0.062] (p = 0.37) once beta times the market was
removed. The gate now carries that control and returns CONFOUNDED for it. (It is also the opposite
of the published low-volatility effect, which survivorship would explain.)

## 4. Volatility is the judgment (`riskmgmt.py`)

Out-of-sample (2023+), forecasting the next 21 days' realised volatility, mean over eight
instruments, against "the long-run average" (error of log volatility):

| Forecast | rank correlation | error vs baseline |
|---|---:|---:|
| EWMA, lambda 0.94 | 0.310 | -47.4% |
| blend of 21- and 63-day | 0.305 | -46.7% |
| 21-day realised | 0.308 | -42.0% |
| 63-day realised | 0.276 | -41.2% |

Over the whole ten years, per instrument, the EWMA ranks the volatility that followed at +0.40
to +0.71 (standard error about 0.09). What the forecast is *for*: holding `target / forecast` of
the position (never more than all of it, with the rest in T-bills, costs charged at the system's
0.05% round trip):

| Mean of eight instruments | CAGR | Sharpe | max drawdown | Calmar |
|---|---:|---:|---:|---:|
| 2016-2022 buy and hold | 9.4% | 0.46 | -42.3% | 0.24 |
| 2016-2022 volatility target | 6.4% | 0.53 | -21.9% | 0.40 |
| **2023-2026 test**, buy and hold | 14.8% | 0.59 | -20.9% | 0.74 |
| **2023-2026 test**, volatility target | 9.7% | 0.45 | -14.2% | 0.77 |
| **2023-2026 test**, 200-day trend filter | 8.3% | 0.21 | -18.5% | 0.72 |

In the test years (a rising market) the target gave up return for a shallower drawdown: SPY's
maximum drawdown went from -18.8% to -11.1% and its Sharpe from 1.13 to 0.99. That is risk
management, not an edge, and the trend filter did worse. It is the thing worth having, and it is
only claimed as that.

## 5. Text: a language model's real judgment (`fetch_news.py`, `news_score.py`, `news_eval.py`)

18,873 headlines for the 120 stocks, 3,000 sampled for scoring (2023-01 to 2026-09) with one
fixed question and no tuning: "how does this affect the company's stock price over the next
trading day?". For stories published after the close or before the open, the *reaction* is the
move from the previous close to the next open (not tradable; it shows whether the model reads the
story correctly). The *tradable* return starts at that open.

| Reader | reaction (n = 1,382) rho [95%] | next day, open to close rho | three days rho |
|---|---|---|---|
| keyword rule (upgrade / beats / downgrade / misses ...) | +0.133 [+0.071, +0.194] | +0.003 [-0.039, +0.042] | +0.002 [-0.046, +0.049] |
| tev1 0.8B (decision model) | +0.115 [+0.049, +0.178] | -0.010 [-0.047, +0.025] | +0.007 [-0.031, +0.046] |
| tev1 4B (decision model) | **+0.145** [+0.085, +0.207] | +0.007 [-0.033, +0.046] | +0.023 [-0.016, +0.062] |
| qwen3 14B (letter probabilities) | +0.106 [+0.043, +0.170] | +0.011 [-0.032, +0.051] | +0.046 [+0.005, +0.086] |
| nimble 9B (decision model; after-hours stories only, n = 1,382) | **+0.145** [+0.081, +0.209] | +0.023 [-0.043, +0.090] | +0.004 [-0.058, +0.064] |

Size does not order the readers: reaction rho is 0.115 (0.8B), 0.145 (4B), 0.145 (9B), 0.106 (14B)
and 0.133 (the keyword rule), with intervals of +-0.06 that overlap entirely. The decision models
separate the extremes more than the rule does: the best-minus-worst third spans +0.56% (0.8B),
+0.72% (4B) and +0.78% (9B) overnight against +0.24% for the keywords, the 14B in between at +0.41%.

For the 4B model, the third of stories it called best moved +0.18% overnight, the third it called
worst -0.55%. The models read the news correctly. After the open, nothing is left: every
next-day correlation is within 0.03 of zero. The one nominal exception (qwen3 14B at three days,
p = 0.027) is one of fourteen tradable news tests and about 0.18% a trade, before the 0.10-0.20%
it costs; it clears no multiple-testing bar and is a lead to replicate, nothing more. Read on the
later 40% of days alone (the ledger's test period) the decision models' next-day correlation is
slightly *negative* (-0.07 to -0.10, uncorrected p 0.02-0.07 against a bar of 0.001): if anything
part of the overnight move reverses by the close, which is not a continuation to trade either.

Stories published *during* the session (2,468, 2024-2026; `intraday_events.py`, `intraday_eval.py`),
entered at the first five-minute bar five minutes after publication: the keyword rule and the 0.8B
reader both show no reaction before entry (rho -0.016, +0.004) and no drift afterwards
(+30 min: +0.021, +0.014; +60 min: +0.016, +0.016; all intervals contain zero). Most in-session
headlines are commentary and the real reaction takes seconds, which an agent deciding every five
minutes cannot reach.

## 6. The Jev-style decision models on price (`systemone_bench.py`)

Through Ollama 0.40's `/v1/systemone` (a separate install; the trading Ollama is untouched),
same 289-cycle benchmark as the model zoo:

| Model | rho, next hour [90%] | Brier (uniform = 0.667) |
|---|---|---|
| nimble 9B | -0.079 [-0.29, +0.19] | 0.806 |
| tev1 4B | +0.036 [-0.10, +0.19] | 0.730 |
| tev1 0.8B | -0.179 [-0.36, +0.06] | 0.807 |

No skill, like the other seventeen models. Asked three differently-worded questions of the same
input, tev1 0.8B returned a correlation of -0.18 for one and +0.25 for another: the sign of a
small model's answer follows the wording, which is what noise looks like, and one of eighteen
numbers looking good is what chance produces.

## What changed in the system

- `risk_judgment`: the decision context carries a `risk` block - the forecast volatility, its
  regime, the share of a normal position a volatility target would hold, and the forecast's own
  measured quality with the number of independent outcomes behind it. ACTIVE only when the
  pessimistic end of that correlation clears 0.10; SUSPENDED when even the optimistic end falls
  short; UNVERIFIED otherwise, with no scaling advice. (A first draft judged quality on the last
  252 days: twelve independent outcomes, +-0.3, and flagged SPY at -0.22 while it scored +0.54 over
  ten years. The same under-power this document opens with, found in the first thing built.)
- A doctor check, `risk forecast`, reports the same quality per traded symbol.
- `research/signal_test`: the gate above, with a ledger.
- Nothing about orders, the Guardian, the allowlist or the strategies changed.

## What this does not show

- Not tried: options, order flow, fundamentals, anything finer than five minutes, intraday
  lead-lag between assets, news over horizons longer than three days, other markets.
- One mostly rising regime (2023-2026) is the test period for the daily work; a rule that needs a
  falling market to pay was not given one.
- Prices are IEX-feed bars; survivorship flatters the stock universe; the volatility backtest
  uses adjusted closes, executes at the next day's exposure, and ignores taxes.
- Absence of evidence at this power is not evidence of absence below it: the daily tests could
  not see an effect of 0.02-0.03, which is the size of most published ones.
- Managing every position in the account (rather than the six symbols listed in the environment)
  needs a change to the Guardian's allowlist rule that was declined by the permission layer; nothing
  here argues for it, since nothing here found an active strategy worth the extra exposure.

## Reproduce

Needs `PYTHONPATH=src`, the credentials in the usual env file, and `numpy`/`pandas`.
`fetch_adjusted.py PANEL.pkl SYMBOL...` makes the adjusted daily panel; `signal_research.py SPY`,
`daily_research.py` and `xs_research.py PANEL.pkl` run sections 1-3 (set `SIGNAL_LEDGER=path` to
record them in the signal ledger); `riskmgmt.py PANEL.pkl CASH.pkl` runs section 4;
`fetch_news.py`, `news_score.py`, `news_eval.py` section 5 (`news_eval.py --ledger ...` records
the tradable tests); `intraday_events.py` and `intraday_eval.py` the in-session test;
`systemone_bench.py` section 6.
