# Bounded plan: score the counterfactual for every configured symbol, not just the first

Written 2026-10-13 18:20 EDT, from the 2026-10-13 open session. This is a pre-edit plan. No
code change has been made.

## Problem, with the measurement that establishes it

`AgentDaemon._counterfactual_pass` picks one symbol:

    symbol = self.config.symbols[0] if self.config.symbols else "SPY"

`AUTOPOIESIS_SYMBOLS=SPY,BIL,TLT,XLB,XLE,XLF`, so `symbols[0]` is `SPY` and every
`COUNTERFACTUAL_EVALUATED` pass scores SPY alone. Measured on the live journal: all **493**
events carry `symbol: SPY`, and no other symbol has a single row.

`counterfactual.evaluate` takes a single `symbol` and builds one price series from it, which
is correct for what it does - a TLT decision has to be compared against TLT's own future
quote, not SPY's. The defect is the caller treating a list of configured symbols as one
symbol.

The consequence is not "less data". It is that three of the strategies that actually traded
today have **zero** decisions in the offline screen:

| strategy | orders submitted 2026-10-13 | decisions in the screen |
|---|---|---|
| `fixed-size-tlt-sell-001` | 9 | **0** |
| `fixed-size-xle-sell-001` | 2 | **0** |
| `fixed-size-xlf-sell-001` | 1 | **0** |
| `trend-follow-20260824-001` | 0 | 43, all PENDING |

A strategy with no rows cannot be screened at any horizon, so it can never be promoted out of
probation and never be judged on its outcomes. Whatever the probation budget is set to, these
three are unjudgeable.

## What this is not

It is not a consequence of the provenance gap, and fixing provenance does not fix it. The
provenance gap and the PENDING horizon explain `scored == 0` for SPY today and will stop
explaining it once tomorrow's forward quotes exist. This defect is independent: it applies to
every symbol the daemon trades, now and after.

It is also not fixed by evaluating the attention names. `_round_symbols` adds BMBL, SOXS,
CIFR, NOK and SOXL beyond the configured list, but those cycles resolve to
`selection_unavailable:` and carry no strategy, so there is nothing to screen. The configured
symbols are the whole of the screenable universe.

## Design

One pass over each configured symbol, keeping each symbol's own price series, with the rows
merged into the single report the existing event already carries. Rows already carry their own
`symbol`, and `collect_decisions` filters on `row["symbol"] in provenance.universe`, so a
SPY-only strategy correctly rejects TLT rows and nothing leaks across symbols.

- `evaluate` keeps its signature and its per-symbol correctness. A thin `evaluate_symbols`
  wrapper calls it once per symbol and concatenates the rows, so the per-symbol grading rules,
  the dead band and the horizon logic are not reimplemented.
- The event payload's `symbol` becomes the list that was evaluated, alongside a per-symbol
  breakdown in the existing counts, so a reader can tell what was covered.
- The journal-growth guard is unchanged and still correct: `recorded` is keyed by cycle id,
  every cycle belongs to exactly one symbol, and only new-or-changed rows are written. The
  steady-state growth is the same order as before; the extra cost is the pending-to-resolved
  transitions for five more symbols, which is the evidence this is for.

## Success criterion

- A cycle on TLT produces a counterfactual row, and `fixed-size-tlt-sell-001` appears in
  `collect_decisions`.
- `day_direction` gains days. It reads rows through `row_eligible(row)` with no strategy, so it
  needs eligible rows with a `net_return_pct`; it currently returns **0** days for the same
  reason `scored` is 0 and will recover on its own once rows resolve. That recovery is the
  prediction to check tomorrow, not something to assert now.
- A SPY strategy is still judged only on SPY rows.
- Verdicts are identical for SPY before and after the change. That is the regression that
  matters most: this must widen coverage without re-grading anything already graded.

## Verification

- Focused tests: a TLT decision gets a row; a SPY-only strategy rejects TLT rows; SPY verdicts
  are byte-identical to the single-symbol path; an empty `config.symbols` still falls back.
- The existing counterfactual, offline-validation and governance suites unchanged.
- Frozen full check, mypy, ruff.

## Stop conditions

Stop and roll back if any part requires inventing a price for a symbol with no quotes,
comparing a decision against a different symbol's series, writing rows the journal-growth
guard would not have written, or changing any verdict an existing row already carries.

## Follow-ups this deliberately does not bundle

- The probation budget is not calibrated per strategy family. That reading was measured and
  is wrong; see the evidence file for the diagnosis.
- `_reference_price_rejection_reason` admits TREND_FOLLOW anchors up to ±50% from the market
  price, which is how a library accumulated 18 SPY variants anchored between 750 and 810 while
  SPY trades near 778. Whether admission should check whether the trigger band can ever be
  reached is a separate question.
- `probation_restarts` is persisted unbounded past `MAX_PROBATION_RESTARTS` and six live
  strategies carry counters of 33 to 362 that no longer affect anything.