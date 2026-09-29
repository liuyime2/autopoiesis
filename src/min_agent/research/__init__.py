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
production module imports anything under `min_agent.research`, so a backtest cannot
quietly become a production dependency and start grading itself on its own output.
"""
