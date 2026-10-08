# Changelog

Notable changes, newest first. The reasoning and the measurements behind each one are in
the commit message; the long-form record is in [`docs/history/`](docs/history/README.md).

## Unreleased

### Added
- `make benchmark`: the falsifiable target - beat SPY buy-and-hold within the risk budget,
  after costs, over a rolling 60-trading-day window. Exit code 0 met / 1 behind / 2 nothing
  to measure. Currently 1 (behind).
- Community files: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and PR
  templates.

### Changed
- `STATUS.md` and `docs/superpowers/` moved to `docs/history/`. Git history is kept, not
  rewritten.
- `minictrl` and `make` run in a plain venv; conda is optional.
- CI runs mypy, matching `make check`.
- The gate has 52 check classes. `fact-docs-current`, which made archived audit documents
  restate live counts, is retired; `screen-direction-neutral` is new.
- The offline screen reads each strategy against what its days alone would have scored,
  rejecting only when it is worse beyond noise.
- `make benchmark` compares each strategy with SPY on the same capital over its own window.

### Fixed
- Orders the Guardian refused were graded as trades; a SELL was credited the round-trip cost
  instead of charged it.
- The daemon read screens back from the journal without `good_trades`, and a maintenance
  pass screened on the ledger as it stood before that pass recorded anything.
- The daemon's heartbeat reported the fingerprint of the code on disk rather than the code it
  had loaded, so the "daemon runs superseded code" check could never fail.
- The RSI kernel review's headline - every SELL scored 0.000 - came from reading the oldest
  counterfactual row per cycle; corrected to 26.5% SELL vs 66.2% BUY.

### Added (2026-10-08, evening)
- `risk_judgment`: an EWMA volatility forecast per symbol in the decision context, with its own
  measured quality (rank correlation with the volatility that followed, over up to ten years,
  with its standard error) and an evidence-earned status: ACTIVE only when the pessimistic end
  of that correlation clears 0.10, SUSPENDED only when the optimistic end falls below it,
  UNVERIFIED otherwise. A doctor check (`risk forecast`) reports it.
- `research/signal_test`: the gate every hypothesis about what predicts returns has to pass, with
  a ledger that makes the bar rise with the number of hypotheses tried.
- `docs/evidence/signal-research-2026-10-08/`: why no brain showed judgment - 47 hypotheses on
  real bars (intraday, daily, cross-sectional), the news event study, the volatility result.

### Added (2026-10-08)
- `MIN_AGENT_MANAGE_ACCOUNT` (default `false`): the agent may manage every position in the
  account, a SELL then bounded by the account's position instead of the agent's own fills.
- During the session the daemon sleeps only the rest of the interval after a round, so trading
  several symbols does not stretch the interval.
- The market context reads about three days of history per traded symbol.

### Added (2026-10-07, later)
- The strategy's own rule decides first; the model may override it only with a reason, and
  every decision records both (`rule_action`, `override_reason`). doctor reports `llm vs rule`.
- A `market` block in the decision context: regime, trend, volatility, 1h and 1d returns.
- Each lesson is shown on half of cycles; a lesson with no measured effect after 10 trading
  days is retired.
- PAUSED strategies are re-examined on evidence, one per pass, never into immediate re-pause.
- SHORT and COVER, behind `MIN_AGENT_SHORTS` (default off) and a Guardian rule with its own
  limits; short lots in the ledger; shorts in the counterfactual and the backtest.
- A `RULE` strategy kind: a constrained, parameter-only DSL the curriculum may propose.
- `minictrl owner-history`: prices an agent sale beyond its own lots against the account
  owner's recorded fills, reported as the owner's exit.
- `docs/evidence/research_trials-2026-10-07.jsonl`: every research trial, 0 of 59 passed.

### Fixed (2026-10-07, later)
- `PositionSnapshot` rejected a negative market value, so the first real short would have
  failed every later snapshot.
- An owner-history ingest was read as the latest evidence batch.

- `[project.urls]`, authors, keywords and classifiers in `pyproject.toml`; the README's clone
  command names the repository and shows the CI badge.

## 0.1.0

The paper-trading agent as recovered and refactored: one execution path
(`cli → daemon → loop → guardian → executor → journal`), an append-only journal as the only
source of truth, broker-verified PnL, and an evolution loop that can retire its own
strategies.
