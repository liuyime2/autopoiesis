# Report what holding would have returned, so a promotion verdict can be read against it

Date: 2026-10-04
Status: **DONE - the benchmark is reported per strategy; the promotion gate is deliberately unchanged**
Baseline commit for rollback: `673232b`
Scope: `PnLEvidence` and the evaluator that builds it. No promotion-gate change, no config, no
new module.

---

## 0. The need, stated as an observation

The project promotes strategies on decision quality. Its success metric is beating holding.
Those are different quantities and nothing in the system reports the second, so a promotion
cannot be evaluated against the thing the project is trying to achieve.

The counterfactual ledger cannot supply it, and that is structural rather than a missing
feature. `counterfactual.py:262` computes

```python
gross = (future_price - price) / price * 100.0
net   = gross - assumed_cost_pct
```

with `price` the quote at the decision and `future_price` the quote at the horizon. A trade's
return is therefore *identical* to holding from that same instant, less the assumed cost.
Measured: every non-HOLD decision, from all three sources, shows an excess over holding of
**−0.05%** — the cost, to the basis point.

## 1. What the hand measurement already shows

Computed from the broker's own closed-lot record, which carries `buy_price`, `sell_price`,
`quantity` and timestamps:

```
market: SPY 739.24 -> 769.58 over 2026-06-09 -> 2026-10-02 = +4.10%

strategy                    lots  realized   capital   return   vs SPY
tiny-fixed-size-001           15   +366.03     11138   +3.29%   -0.82%
fixed-size-buy-001            16   +146.94     12120   +1.21%   -2.89%
trend-follow-buy-001           4    +81.36      2985   +2.73%   -1.38%
TOTAL                         35   +594.33     26243   +2.26%   -1.84%
```

**All three strategies underperformed the market**, by 0.8 to 2.9 points. The three that
produced any broker-verified PnL at all, and that is every strategy that has ever done so.

`capital` is the sum of each closed lot's entry cost, so it is cumulative turnover across
sequential round trips — $26,243 against the $20,000 the Guardian allows as *concurrent*
exposure. It is not a limit breach and must not be reported as one; the concurrent bound is
enforced separately and measured at about $19,793.

## 2. The minimal change

Two derived numbers per strategy on the `PnLEvidence` that already exists:

- `strategy_deployed_capital: dict[str, float]` — entry cost of that strategy's closed lots.
- `strategy_return_pct: dict[str, float]` — realized over that capital.
- `market_return_pct: float | None` — the same instrument's return over the same window, so
  the comparison needs no second price source.

And one derived comparison, because a number nobody reads changes nothing:

- `strategy_excess_vs_market_pct: dict[str, float]` — the difference, per strategy.

That is arithmetic on fields the broker record already carries. No new data is fetched, no new
event is journalled, and `PnLEvidence` is already the object the doctor and the journal print.

**The market return must come from the same instrument as the lots** — the prices the
evaluator already has — or the comparison would rest on two price sources that can disagree.
Where no price series covers the window, the fields stay `None` and the existing
`missing_reasons` mechanism says so, rather than a benchmark being invented.

## 3. What this deliberately does not do

It does **not** change the promotion gate. Requiring a strategy to beat holding before
promotion is a risk-adjacent policy change and it deserves its own experiment with its own
falsifier — but that experiment is only meaningful once the number exists and has been seen on
a real promotion. Changing the gate now would be a decision taken against a metric the loop
has never observed.

## 4. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| Every strategy with closed lots gets a return and an excess | A strategy with lots whose return is `None` while prices exist |
| The figures reproduce the hand computation | `strategy_return_pct` disagreeing with +3.29 / +1.21 / +2.73 |
| No price series means no benchmark, not a fabricated one | A market return appearing for a window with no prices |
| It stays a measurement | The promotion gate's behaviour changing |

The second row is the one that matters: it is checkable against the table above, which was
computed independently of this code.

## 5. Tests

- A lot at 100.00 entry and 110.00 exit yields realized 10.00 on capital 100.00, a return of
  +10.0%, and an excess equal to that return minus the market return over the same window.
- A strategy with no closed lots appears in no mapping rather than as a zero, so "did not
  trade" is not rendered as "returned nothing".
- With no price series, `market_return_pct` is `None` and a `missing_reasons` entry appears.

## 6. Verification

- `ruff`, `mypy`, the PnL and doctor tests, then the full `make verify`.
- The reproduction check: the emitted figures are compared against the table in §1, which was
  derived by hand from the broker record before this code existed.

## 7. Result

`PnLEvidence` carries the four mappings, `doctor` reports `pnl vs holding`, and the emitted
figures reproduce the hand computation in §1 exactly: market +4.10%, and −0.82 / −1.38 / −2.89
against it. Four tests: the benchmark comes from the evaluation's own prices, a missing price
series yields `None` rather than a guess, the per-cent arithmetic is stated on checkable
numbers, and a strategy that did not trade is absent rather than zero.

**Still open, deliberately.** The gate is unchanged. What §1 makes possible is a decision that
could not previously be taken on evidence: the gate's criterion has now been observed to
produce three strategies, and all three lost to the market. Whether promotion should require
beating holding is a risk-adjacent policy question and needs its own experiment with its own
falsifier — not a side effect of adding a report.

## 8. Correction: turnover was the wrong denominator

The first version of this reported each strategy's return on **cumulative turnover** — the
sum of every closed lot's entry cost — and named the field `strategy_deployed_capital`.

Both were wrong, and the error was mine, made in the same commit that added the measurement.
A strategy that cycles one share fifteen times turns over fifteen times the capital it ever
risked. Turnover as a denominator therefore understates the return, and then compares it
against a market return computed on capital held *once* — two different denominators, so the
"excess" was not an excess of anything.

Measured, on the live record:

```
                            turnover basis    peak-exposure basis
tiny-fixed-size-001              +3.29%                 +3.81%
fixed-size-buy-001               +1.21%                 +1.92%
trend-follow-buy-001             +2.73%                 +2.12%
market                            +4.10%                +4.10%
```

The peak exposures are $9,603, $7,641 and $3,833 — not the ~$769 a single-share assumption
would have given, which is what should have caught it. Turnover understated the largest
strategy by 0.5 points and the excess it reported was **larger** than the truth (−0.82 against
a real −0.29). It kept the right sign by luck, which is worse than being wrong in one
direction, because a correct-looking sign is what would have stopped the next reader looking.

Fixed by `_peak_exposure_by_strategy`: lots replayed in timestamp order across closed and
still-open positions, taking the maximum running cost basis. The field is renamed
`strategy_peak_exposure` because "deployed capital" is precisely the ambiguous phrase that
produced the mistake. A test states the distinction on numbers where turnover gives +8.33%
and peak exposure gives +12.5% for the same realized PnL.

**The conclusion survives**: all three strategies are behind the market, by −0.29, −1.98 and
−2.18 points, totalling −$270.86 on $21,077 at risk. What changed is the size of the claim and
the fact that it is now measured on the capital the strategies were actually given.
