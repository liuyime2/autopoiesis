# min-agent

A paper-trading agent that decides for itself whether its own strategies are any good,
using the broker's records as evidence, and retires the ones that are not.

It runs against Alpaca **paper** only. Live trading is refused in code, not by
convention: `cli.py` rejects any base URL that is not a paper endpoint before a client is
constructed.

For how the system is put together - the canonical execution path, the evolution loop,
and which module owns which fact - start at [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
This file is the operator's guide: what to run, what to look at when it misbehaves, and
what each hard limit is.

## What it actually does

Every five minutes while the market is open:

1. Read a snapshot from the broker - price, positions, account, clock.
2. Ask the model for a decision, or use the deterministic fallback if it cannot be reached.
3. Put the decision past the **Guardian**, which refuses anything that breaches a hard risk
   limit. The Guardian is not advisory and has no bypass.
4. Submit the order and journal the cycle.
5. Every fifteen minutes, reconcile pending orders against the broker, re-score the
   strategies from broker-confirmed fills, propose a new strategy if the library has a gap
   in it, and move each strategy's lifecycle on the evidence.

The unusual part is step 5. The system is permitted to remove its own strategies. A
strategy that cannot demonstrate worth does not stay in the library just because it is
there.

## Install

Requires Python 3.10+ and an Alpaca **paper** account.

```bash
conda create -n llm python=3.10 -y
conda activate llm
make install                       # pip install -e ".[dev]"

export XDG_CONFIG_HOME="$HOME/.config"
mkdir -p "$XDG_CONFIG_HOME/min-agent"
# write ALPACA_API_KEY, ALPACA_SECRET_KEY and ALPACA_BASE_URL (paper) into
# "$XDG_CONFIG_HOME/min-agent/env", then:
make check
```

`make setup` prints the same steps. Credentials live outside the repository and are never
read from a checked-in file.

### Verifying the install before you have credentials

Everything below runs on a fresh clone with no broker credentials and no `runtime/`
directory, which is what `runtime/` being gitignored means in practice:

```bash
git clone <repo> && cd min-agent
conda create -n llm python=3.10 -y && conda activate llm
make install
make smoke-offline     # import, config, CLI - no broker contacted
make check             # lint + mypy + tests
```

That path is verified rather than asserted: cloning into a clean environment and running it
found three things that only fail off the live machine - `ruff` and `mypy` missing from
`[dev]`, a test that read the live strategy library from `runtime/`, and a `smoke-offline`
target that passed `--skip-broker` to a flag which only applies to `--doctor`. All three
are fixed; see `docs/MIGRATION.md`.

## Commands

Every task is a `make` target. There are no other scripts to memorise - the deleted shell
wrappers each duplicated a flag below.

| Command | What it does | Cost |
| --- | --- | --- |
| `make check` | lint + mypy + test, no broker | ~15s |
| `make smoke` | one real cycle end to end against the broker | ~8s |
| `make smoke-offline` | config and CLI end to end with `--skip-broker`; no broker, no model | ~20s |
| `make fast` | check + smoke + the full gate | ~90s |
| `make verify` | 51 check classes, non-zero on any failure | ~100s |
| `make test` | the whole suite | ~5min |
| `make doctor` | health of the running system, non-zero on any fault | ~20s |
| `make status` | is the daemon alive and what is it doing | ~1s |
| `make run` / `make stop` / `make restart` | control the daemon via systemd | ~2s |
| `make reproduce` | record commit, config, versions, broker clock, metrics for this run | ~5s |

`make type` runs mypy and blocks. It is part of `make check`, so a type
regression fails the fast loop rather than waiting for review.

### The iteration loop

```
edit  ->  make check  ->  make smoke  ->  make fast
```

`make check` is deliberately cheap and catches the things a real edit breaks. `make smoke`
is the first stage that touches the network and the model, so it catches a changed API
contract, a broken credential path or an unparseable decision. `make fast` is the gate.

One failing check class can be re-run alone: `make verify CLASS=pnl-accounting`.

## Where things are

```
src/min_agent/          the package. 40 modules, 3 external dependencies.
  cli.py                the single entry point
  daemon.py             the cycle loop and the maintenance loop
  guardian.py           hard risk limits. not bypassable
  loop.py               one cycle: snapshot -> decision -> guardian -> execute
  journal.py            append-only record. the source of truth
  models.py             shared schema, imported by 21 modules
  strategy_engine.py    strategy library, selection, lifecycle
  evaluator.py          scoring from broker-confirmed evidence
  research/             diagnosis only. production may not import it, and a gate enforces that
tools/verify.py         the gate: 51 check classes
tools/provenance.py     what `make reproduce` records
tests/min_agent/        62 test files
examples/minimal_cycle.py   the smallest runnable example, no broker needed
configs/paper.env.example   every environment variable, with its default
.github/workflows/      CI: lint, tests, offline smoke, and the gate's self-test
runtime/min_agent/      all state. gitignored, regenerable except journal.jsonl
docs/ARCHITECTURE.md    the current architecture, then the pre-refactor map behind a dated marker
docs/MIGRATION.md       what changed and what replaced it
```

## State

`runtime/min_agent/journal.jsonl` is the only source of truth. Every other file is derived
from it and can be deleted and regenerated: `reflection.json`, `heartbeat.json`,
`risk_baseline.json`, `market-state.json` (written by nothing since the deleted `check-market-open.sh`), `curriculum_state.json`, `strategies/`.

`docs/ARCHITECTURE.md` has the full table and the data flow; its final section records
what the repository looked like before this refactor.

## Debugging

```bash
make status                      # daemon alive? which strategy? last decision?
./minictrl logs        # the systemd journal for the daemon
python -m min_agent.cli --doctor # why the doctor says what it says
```

When something looks wrong, the journal is the record. One line is one cycle, and it
carries the snapshot, the decision, the Guardian's verdict and the execution result:

```bash
tail -1 runtime/min_agent/journal.jsonl | python3 -m json.tool | head -40
```

## Adding a strategy

Strategies are proposed by the curriculum agent, admitted by `strategy_admission.py`, and
moved through `PROBATION -> ACTIVE / PAUSED / RETIRED`. A new one is not added by writing a
file into `strategies/` - `doctor` reports any file without a recorded admission verdict as
a blocking failure, on purpose.

A `TREND_FOLLOW` is refused if its `reference_price` cannot trade in either direction: too
far from the market to be a real price, or so close that its own trigger band already
contains the current price, which would leave it permanently `HOLD`. A trigger the market
has not reached yet is fine - that is a resting order, and the rule follows the price
rather than purging once. See
[`docs/history/plans/2026-10-03-refuse-untradeable-strategy.md`](docs/history/plans/2026-10-03-refuse-untradeable-strategy.md)
for the measurement behind it.

## Verifying a change did not break something

```bash
make fast
```

`make verify` is the gate the project's own objective asks for. `make verify-self-test`
breaks it on purpose to prove it can fail - a verification command that cannot fail is
believed rather than run.

## Hard limits

Read once from the environment by `config.py` and enforced by `guardian.py`. The Guardian
has no bypass - it is called on the single path that can submit an order.

| Limit | Env var | Default |
| --- | --- | --- |
| max position value | `MIN_AGENT_MAX_POSITION_VALUE` | $5,000 |
| max total exposure | `MIN_AGENT_MAX_TOTAL_EXPOSURE` | 4x position value |
| max daily loss | `MIN_AGENT_MAX_DAILY_LOSS` | $500 |
| max trades per day | `MIN_AGENT_MAX_TRADES_PER_DAY` | 10 |
| min confidence | `MIN_AGENT_MIN_CONFIDENCE` | 0.5 |

They are environment-overridable, so a *lower* limit is a legitimate tightening and a
*higher* one is a risk decision that belongs to whoever operates the account, not to the
agent. Nothing in the codebase raises them.

A SELL is additionally bounded by what the agent itself bought, computed by replaying
`ORDER_FILL_CONFIRMED` events from the journal - not by what the account holds. The account
holder's shares are not the agent's to sell, and an agent that cannot establish what it
owns is refused rather than allowed to guess.

## Known debt

`make type` is a gate and reports 0 findings. It used to report 95 and block
nothing: the recipe was `-@$(CONDA_RUN) mypy ...`, and the leading `-` makes make
ignore the exit code, so the findings were printed on every run while the command
still passed. Paying that debt is what found four real defects, none of them
annotation noise:

- `cli.py` passed `cost_basis` as a one-argument callable to a parameter typed as
  taking none. The engine's zero-argument call raised `TypeError`, a surrounding
  `except Exception` turned it into an empty mapping, and the model was silently
  given no cost basis - so it could not tell profit from loss, which is the one
  thing that context exists to provide.
- `llm_decision.py` defined `Transport` twice at module level with incompatible
  signatures. Python bound the second, so the type readers saw was not the type
  that ran, and the first was unreachable.
- `research/backtest.py` carried its own `Bar` and `bars_from_records`,
  byte-identical to `min_agent.regime`'s. A bar built by the research copy was a
  *different class* from the one production returns, so a rule could be handed
  something structurally right and nominally wrong. It now imports both.
- `strategy_engine.py` printed "kept X which has N cycles against this one's N",
  reading the duplicate's result for both numbers, so the justification for a
  retirement compared a strategy against itself.

The remaining 91 were annotation precision at `Callable` and `dict` boundaries.
They were fixed at the boundaries rather than silenced: one canonical
`min_agent.coerce` now does every payload narrowing, and reports the field name
and the offending value when a record is unreadable, instead of raising a bare
`invalid literal for int()`.

[`docs/history/`](docs/history/README.md) is the historical record - the running log
(`STATUS.md`), the defect audit, and one plan per change - kept because it records why
specific decisions were made, including the claims that were later retracted. It is not documentation of how the system works - this
file and `docs/ARCHITECTURE.md` are.

## The pipeline

```bash
make install        # once: install the package and its dev dependencies
make pipeline       # validate-data -> test -> evaluate -> reproduce
```

`make pipeline` is the whole loop as one command. Each stage is independently runnable and
each is a real check rather than a step that prints "ok": `validate-data` parses the runtime
state before anything consumes it, `test` runs the suite, `evaluate` scores the paper record
from broker evidence, and `reproduce` records the commit, environment, config and metrics that
produced the result.

`install` is deliberately not a pipeline stage - re-resolving dependencies partway through
would mutate the environment the other stages are running in.

`evaluate` exits zero even when the 10% daily target is not met. That is a measurement, not a
build failure; if it failed the pipeline, the one number this project exists to move would
become something you learn to ignore.

### Two gates, and why

`make check` (lint + mypy + tests) answers "is this repository correct?" and is the gate that runs in
CI. `make verify` additionally runs `minictrl doctor` against the live deployment, so on a
host where the agent is trading it also answers "is the agent doing well?".

That means `make verify` can legitimately go **red for a trading result rather than a code
defect**, and on this host it does. `pnl attribution` is FAIL right now: the model opened four
lots and contributed -0.98 to a total of +563.41. That is a health failure and not a build
failure, and `tests/min_agent/test_attribution.py` guards the severity deliberately - an
earlier version used `== 0.0` instead of `<= 0.0` and reported OK while the model lost money.

So when you read a red run, read which class failed before concluding anything is wrong with
the code. `make check` (lint + mypy + tests) is the one that speaks only about the repository.

Findings that are limits on the *record* rather than outcomes - currently the 29 shares sold
that no BUY accounts for, written up in `docs/history/STATUS.md` - are WARN. They are stated prominently
but do not redden the gate, because a four-month-old accounting gap that is permanent red is
how a gate gets ignored.

## Verifying a clone

The figures quoted elsewhere in this repository are reproduced by one script, and its
output is committed:

```bash
docs/evidence/run-fresh-clone.sh
```

It clones the committed tree into a scratch directory, installs it, and runs seven steps:
lint, the full test suite, the example cycle, the entry point, provenance, the gate's own
self-test, and the gate itself. No credentials are needed. That last one is the one that
matters - it is the check a newcomer runs before they have a paper account or a single
recorded cycle, and it was red for several commits while every other step was green. The result is written to `docs/evidence/fresh-clone.log` and the
gate checks that log against the script and the commit it names, so a green claim here is
traceable to a run rather than to prose. `make verify` runs the same checks in place; the
clone exists to prove they pass from nothing but a checkout.

## License

Apache License 2.0 - see [`LICENSE`](LICENSE).

Two things a reader should know before running this against a real account, because the
license does not cover them. It **trades in paper mode only**: `config.is_paper_endpoint`
is the single definition of paper-only and both `cli` and the executor go through it, so
there is one line to audit rather than a setting to trust. And it makes no claim of profit.
The figures this repository quotes are broker-verified for a paper account and are evidence
that the accounting and the loop are real, not a track record - and they are deliberately
not written into these documents, because they move while the agent trades. Run
`minictrl doctor` for the current ones.

