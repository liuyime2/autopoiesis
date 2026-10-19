# Changelog

Notable changes, newest first. The reasoning and the measurements behind each one are in
the commit message. The dated argument trail is not part of the repository - it is kept outside
it, on the operator's machine, because it describes the system's history rather than the system;
`git log` carries the same commits.

## Unreleased

- The gate has a class that compares the installed `ollama.service` with `tools/ollama.service.in`
  value by value. Its absence cost a live session's model participation on 2026-10-19: the installed
  unit pinned GPU 4 while the template declares 2,3, GPU 4 was 95% full, the model half-loaded onto
  the CPU, and every decision timed out at 120s into the rule-engine fallback - with nothing
  anywhere saying the unit disagreed with the template. `gpu-devices-reserved` could not see it,
  because GPU 4 is not a reserved device.
- The gate has two tiers. `make verify-fast` runs 52 of the 58 classes in about four minutes and
  is what a deploy runs at install time; `make verify` runs all 57 and is what CI runs on every
  push and pull request. AGENTS.md 19 states what each is evidence for, because the full gate had
  grown to about 23 minutes on the deployment host - longer than a working session, which had made
  "run the gate before deploying" a rule nobody could keep.
- A deployment now fails the gate unless the installed build has a recorded full-gate pass.
  `make record-deployment` runs the gate and writes what it observed to
  `docs/evidence/deployments.jsonl`; the `deploy-gate-record` class requires it, and the tier is
  written by that tool rather than typed, so a `--fast` record cannot claim it. The tool writes
  nothing when the gate is red, so a bad install keeps its red as an unrecorded build.
  The one exception is named: `deploy-gate-record` cannot be green
  while the record it demands is being written, and the entry stores the failing
  classes so the exception is countable. The order is
  install, then verify, then record: `daemon-source-matches-worktree` and `doctor` are red before
  every install by construction, which makes "green before installing" unsatisfiable.

## 0.1.0a1 - 2026-10-10

The first published build of the paper-trading agent. `pyproject.toml` version `0.1.0a1`,
tag `v0.1.0a1`, classifier `Development Status :: 3 - Alpha`.

**What this release is.** A research instrument: one execution path
(`cli → daemon → loop → guardian → executor → journal`), an append-only journal as the only
source of truth, broker-verified paper accounting, a deterministic Guardian with hard limits,
and an evolution loop that can retire its own strategies on recorded evidence. Everything below
was produced by running a command on this host, and the command is named so it can be run again.

### Verified on this build

- The gate: `make verify` reports **58 check classes, 0 failed**, over 1859 test executions in 81
  files. `pytest` reports **1183 passed**; `ruff check src/ tools/ tests/ examples/` and
  `mypy src/autopoiesis` are clean.
- The credential-free path claimed by the README quick start. In a fresh clone at this tag, with
  no broker credentials, no `runtime/` and no env file: `make install`, `make check`,
  `make smoke-offline`, `examples/minimal_cycle.py`, `make release-check`, the site generation and
  `make verify` all exit 0.
- The public site is deployed. `docs/site/` is generated from committed figures and published at
  <https://liuyime2.github.io/autopoiesis/> by the `pages` workflow: five pages, all HTTP 200,
  carrying the same numbers the CLI prints and no account data.
- The package installs and runs. `python -m build --sdist --wheel` succeeds; the sdist carries
  the package, `examples/` and `configs/`; installing that sdist in a clean virtualenv imports
  `autopoiesis`, runs `examples/minimal_cycle.py` and exposes the `autopoiesis` console script.
- The journal no longer deletes its own history when a generation reaches the rotation horizon -
  it is moved into `runtime/autopoiesis/journal-archive/` and read back first, and a rotation
  that cannot archive raises instead of unlinking.
- The scheduled research chain runs end to end: `quant-research.service` fetched 24,287 real
  five-minute SPY bars, searched 134,425 bars of that one instrument over 11 candidates and
  recorded every trial with its symbol. The bar fetch had been failing on a mixed cache for a
  full day before this.

### Not verified, and not claimed

- **Profitability.** `make benchmark` reports the agent **+1.81%** on deployed capital after cost
  against SPY **+2.69%** over the same window, over **19 of a targeted 60** trading days. The
  agent is behind, with its denominator printed. Exit code 1, which is the contract: 0 met,
  1 behind, 2 nothing to measure.
- **Any directional edge.** 61 hypotheses about what predicts returns have been through a
  family-wise gate; none carried a direction. The one judgment that holds is volatility, which
  is a statement about risk, not direction.
- **The VIX floor is not a Sharpe improvement.** 4 of 4 instruments significant, and an A/B
  test against itself shows it buys back drawdown with return. It is fused and labelled as that.
- **Live trading.** Not present. `AUTOPOIESIS_MODE=live` is refused by the loader, and the
  Guardian's limits are not weakenable by any configuration value - a non-finite limit is now
  rejected where it is read and where the Guardian is constructed.

### Fixed in this release

- A NaN risk limit silently disabled the Guardian: every comparison against it is False, so a
  10,000-unit buy that a 5,000 limit correctly refused was **approved**. Rejected at both the
  loader and the Guardian's constructor.
- Cost was defined three times, two of them wrong. The round-trip charge was halved a second
  time (0.18 owed against 35.82 charged basis on this record) and the benchmark computed its own
  figure. One definition now, from the evaluator; the reported agent figure fell 0.04 points
  as a result.
- `make daily-report` called `_headline(report)` after the verdict moved to the agent level, so
  the entry point a timer invokes raised `TypeError` while its unit tests stayed green.
- The research merge keyed deduplication on the timestamp alone. Correct with five SPY files,
  wrong with seventeen instruments: 852,937 of 988,704 bars were dropped and the survivors were
  fragments of different assets interleaved. The search is now scoped to one instrument, and the
  instrument is part of a trial's identity.
- The read-only panel read only the live journal generation and reported 881 cycles where the
  record holds 2,060, described `PAUSED` as terminal when the daemon reopens it, and judged the
  daemon by the heartbeat's own status word. It is now one snapshot per run, read across every
  generation, judged by age and pid, with atomic writes and tests.
- An empty strategy library failed the gate on a fresh clone, contradicting the README's own
  claim that no credentials are needed for the quick start.
- `FillReconciler` confirmed a broker order's quantity and price against a pending order without
  checking that the order was the one asked for. A broker answering a `client_order_id` lookup
  with a different order would have that order's fill recorded against this one, and nothing
  downstream could tell: the confirmation is well-formed and only the numbers are somebody else's.
  A reported, different id is now refused; an absent one is not, because refusing on absence would
  turn one wrong fill into none.
- A workflow's `release` job appended at column 0 instead of under `jobs:` is valid YAML, ran
  nowhere, and failed nothing locally. `workflow-jobs-reachable` checks it, and it caught the
  mistake it was written for.
- The operator script was still named `minictrl` after the project renamed to `autopoiesis`.

### Known gaps

- 29 shares an agent order sold beyond the agent's own lots are priced and reported separately;
  their gain is **not** in any total above, and the gap's direction is unknown.
- 1077 of 2060 decisions cannot be attributed to a model or a rule, because they predate
  provenance capture.
- No off-machine copy of the journal exists. The archive keeps the record from being deleted on
  this host; nothing has shipped it anywhere else, and no test claims otherwise.
- The public site asserts the properties an accessible page needs - landmarks, one `h1`,
  `aria-current`, a skip link, a visible focus ring, `prefers-reduced-motion`, both colour
  schemes - but has not been opened in a browser, checked with a screen reader, or measured for
  contrast by a tool. It is published and unaccepted.
- `runtime/` is not published. The site's figures are aggregates over the record; the record
  itself, and any position, is not on the site and not in the repository.

### Added
- `make benchmark`: the falsifiable target - beat SPY buy-and-hold within the risk budget,
  after costs, over a rolling 60-trading-day window. Exit code 0 met / 1 behind / 2 nothing
  to measure. Currently 1 (behind).
- Community files: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and PR
  templates.

### Changed
- `STATUS.md` and `docs/superpowers/` moved to `docs/history/`. Git history is kept, not
  rewritten.
- `autopoiesis` and `make` run in a plain venv; conda is optional.
- CI runs mypy, matching `make check`.
- The gate has 58 check classes. `fact-docs-current`, which made archived audit documents
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

### Added (2026-10-08, night)
- `AUTOPOIESIS_UNIVERSE` (`allowlist` | `account` | `tradable`), `AUTOPOIESIS_MIN_PRICE`,
  `AUTOPOIESIS_ATTENTION_SLOTS`: the account holder can widen what the Guardian lets through to
  every held symbol, or to any active US stock on a major exchange priced at least $5, with the
  broker's most-active liquid names looked at each round. Default unchanged. The risk baseline now
  records the universe and the price floor, so a widening is flagged until a human re-approves it.

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
- `AUTOPOIESIS_MANAGE_ACCOUNT` (default `false`): the agent may manage every position in the
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
- SHORT and COVER, behind `AUTOPOIESIS_SHORTS` (default off) and a Guardian rule with its own
  limits; short lots in the ledger; shorts in the counterfactual and the backtest.
- A `RULE` strategy kind: a constrained, parameter-only DSL the curriculum may propose.
- `autopoiesis owner-history`: prices an agent sale beyond its own lots against the account
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
