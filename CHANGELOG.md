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

### To do before the first public release
- Settle the repository name and add `[project.urls]` to `pyproject.toml`.

## 0.1.0

The paper-trading agent as recovered and refactored: one execution path
(`cli → daemon → loop → guardian → executor → journal`), an append-only journal as the only
source of truth, broker-verified PnL, and an evolution loop that can retire its own
strategies.
