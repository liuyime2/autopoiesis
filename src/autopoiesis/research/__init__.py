"""The research and evolution layer. Production must never import this.

The objective requires the production system to be cleanly separated from research
and evolution:

* production: data -> decision -> allocation -> immutable Guardian -> execution ->
  reconciliation
* research: diagnosis -> hypothesis -> candidate generation -> backtest ->
  walk-forward OOS -> robustness -> multiple-testing control -> shadow -> probation
  -> promotion

The separation is structural rather than a convention, because a convention is
exactly the kind of thing that erodes. `make verify` runs a check that fails if any
production module imports anything under `autopoiesis.research`, so a backtest cannot
quietly become a production dependency and start grading itself on its own output.

That separation means research cannot write to the production journal, which appears
to conflict with the objective's requirement to record every failed trial. It does
not: `trials.py` is a **separate** append-only sink in its own file that nothing in
production reads. Every backtest is recorded there, pass or fail, with the figures
behind the verdict - so a search that failed leaves evidence that it ran, without
giving a backtest any way to influence what trades.
"""
