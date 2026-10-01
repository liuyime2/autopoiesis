# min-agent

A paper-trading agent that decides for itself whether its own strategies are any good,
using the broker's records as evidence, and retires the ones that are not.

It runs against Alpaca **paper** only. Live trading is refused in code, not by
convention: `cli.py` rejects any base URL that is not a paper endpoint before a client is
constructed.

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
make check             # lint + tests
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
| `make check` | lint + unit tests. The loop you run on every edit | ~15s |
| `make smoke` | one real cycle end to end against the broker | ~8s |
| `make smoke-offline` | the same path with broker and model stubbed | <1s |
| `make fast` | check + smoke + the full gate | ~90s |
| `make verify` | 41 check classes, non-zero on any failure | ~100s |
| `make test` | the whole suite | ~5min |
| `make doctor` | health of the running system, non-zero on any fault | ~20s |
| `make status` | is the daemon alive and what is it doing | ~1s |
| `make run` / `make stop` / `make restart` | control the daemon via systemd | ~2s |
| `make reproduce` | record commit, config, versions, broker clock, metrics for this run | ~5s |

`make type` runs mypy. It reports rather than blocking: see
[Known debt](#known-debt).

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
tools/verify.py         the gate: 41 check classes
tools/provenance.py     what `make reproduce` records
tests/min_agent/        61 test files
examples/minimal_cycle.py   the smallest runnable example, no broker needed
configs/paper.env.example   every environment variable, with its default
.github/workflows/      CI: lint, tests, offline smoke, and the gate's self-test
runtime/min_agent/      all state. gitignored, regenerable except journal.jsonl
docs/ARCHITECTURE.md    current architecture (Part II) and the pre-refactor map (Part I)
docs/MIGRATION.md       what changed and what replaced it
```

## State

`runtime/min_agent/journal.jsonl` is the only source of truth. Every other file is derived
from it and can be deleted and regenerated: `reflection.json`, `heartbeat.json`,
`risk_baseline.json`, `market-state.json`, `curriculum_state.json`, `strategies/`.

`docs/ARCHITECTURE.md` Part II has the full table and the data flow; Part I records
what the repository looked like before this refactor.

## Debugging

```bash
make status                      # daemon alive? which strategy? last decision?
tail -f runtime/min_agent/daemon.log
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

`make type` reports 94 mypy findings and is not a gate. All of them are annotation
precision at `Callable` and `dict` boundaries; none is a known runtime defect. It is
wired and reproducible so the debt is visible rather than forgotten.

`docs/superpowers/SYSTEM_AUDIT.md` is a historical defect log, kept because it records
why specific decisions were made. It is not documentation of how the system works - this
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

## Verifying a clone

The figures quoted elsewhere in this repository are reproduced by one script, and its
output is committed:

```bash
docs/evidence/run-fresh-clone.sh
```

It clones the committed tree into a scratch directory, installs it, and runs lint, the full
test suite, the example cycle, the entry point, provenance, and the gate's own self-test. No
credentials are needed. The result is written to `docs/evidence/fresh-clone.log` and the
gate checks that log against the script and the commit it names, so a green claim here is
traceable to a run rather than to prose. `make verify` runs the same checks in place; the
clone exists to prove they pass from nothing but a checkout.
