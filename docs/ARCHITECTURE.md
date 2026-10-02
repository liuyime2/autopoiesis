# Architecture & Runtime Map

## Current architecture (after the refactor)

What this repository is now. The pre-refactor tree it replaced is recorded at the end of
this file, behind a dated marker - a refactor with no record of its starting point cannot
be audited, but that record must not read as a description of the present.


> Sections below describe the repository as it is now. This is the current architecture;
> sections 1-9 above are the historical record it replaced.

### What the refactor cost and saved

The objective is explicit that line count is not the acceptance criterion, so the honest
account is given rather than a flattering one.

```
76 files changed, 1560 insertions(+), 651 deletions(-)

deleted    437 lines   5 shell wrappers, each a thin duplicate of CLI flags
added      545 lines   README, ARCHITECTURE, MIGRATION
added      264 lines   pyproject.toml, ruff.toml, Makefile targets
added      368 lines   tests, of which 40 are the syntax-import class's own
production +131 / -98   net +33 lines in src/min_agent
```

The production-code number is the one that matters, and +33 is almost entirely comments
recording why a thing is the way it is: why the closed-market sleep stays inside the
heartbeat budget, why `is_system_rejection` matches prefixes, why `research/` is
quarantined, why the import surface was deliberately left alone. That is the kind of line
that costs nothing to carry and saves the next reader a wrong change.

What was genuinely removed, in the objective's own order:

- **Delete** - 437 lines of five entry points that did the same things as the CLI; a dead
  package; every unused import and dead local ruff could identify (12 and 2, before the
  rules were scoped down to what is worth enforcing).
- **Merge** - five ways to start the daemon became one `min-agent` console script.
- **Simplify** - `CHECK_CLASSES` was a list that had stopped matching reality; it now does.
- **Reuse** - the D8 safety property outlived the script it was written next to.
- **Rewrite** - `smoke-offline` and the `--help` string, both of which had never worked.
- **Add** - `pyproject.toml`, `ruff.toml`, `README.md`, `ARCHITECTURE.md`, `MIGRATION.md`,
  and one test file.

Six real defects were found and fixed that had nothing to do with the refactor's shape and
everything to do with looking: `smoke` calling an SDK method that does not exist, `--help`
crashing on argparse's `%` interpolation, a coverage gate passing while checking 13 of 29
classes, `syntax-import`'s tests never executing inside a green gate, doctor reporting a
live daemon dead every night, and a test protecting the curriculum's context window
depending on gitignored state so it could not run on a fresh clone.

---

> **Dated section starts here.** Everything below records the repository as it stood
> before the refactor: its defect table, its test baseline, and its execution path.
> None of it is a claim about the system as it is now.

## Part I — Pre-refactor (recorded before any change)
## 1. What this project fundamentally is

A paper-trading agent that proposes and executes trades, decides for itself whether its own
strategies are any good, and retires the ones that are not. The distinguishing property is
not that it trades - that is trivial - but that **it grades its own work using broker
evidence, and is allowed to remove its own strategies as a result.**

Everything below is subordinate to that. A component earns its place only if it moves data
toward one of four outcomes: a decision reached, an order reconciled against the broker, a
strategy's worth measured, or a strategy's lifecycle changed.

## 2. Core execution path

There is exactly one, and it is short. `src/min_agent/daemon.py` runs it:

```
MarketScheduler.should_trade_now()           market open? (broker clock)
  └─ AlpacaDataGateway.snapshot(symbol)      DataSnapshot: price, positions, account
      └─ OllamaDecisionEngine / PolicyEngine TradeDecision (model, or deterministic fallback)
          └─ Guardian.review(decision, snapshot)   approved? -> hard risk limits
              └─ Executor.submit(...)               order to the broker
                  └─ JsonlJournal.append(cycle)     the one append-only record

  maintenance (every 900s):
      ├─ FillReconciler.resolve()          SUBMITTED -> broker-confirmed fill
      ├─ BrokerEvidenceProvider            30-day broker batch
      ├─ DeterministicEvaluator            StrategyResult incl. realized PnL
      ├─ Counterfactual / OfflineValidation score the decisions made
      ├─ ReflectionMemory                  reflection.json snapshot
      ├─ CurriculumAgent                   propose a new strategy (LLM)
      ├─ StrategyAdmission                 accept or refuse it
      └─ StrategyLifecycleManager          PROBATION -> ACTIVE / PAUSED / RETIRED
```

`JsonlJournal` is the single source of truth. Every other state file is derived from it and
is regenerable: `reflection.json`, `heartbeat.json`, `risk_baseline.json`,
`market-state.json` (written by nothing since the deleted `check-market-open.sh`), `curriculum_state.json`, `strategies/`.

## 3. Module dependency reality

`src/min_agent/` - 40 files, ~11,950 lines, 3 external dependencies (`alpaca_trade_api`,
`pydantic`, `requests`). Heaviest nodes: `models.py` (imported 21x, the shared schema),
`atomicio.py` (8x), `journal.py` (6x), `strategy_engine.py` (6x).

`cli.py` imports **25 modules**. That is the real measure of coupling, not module count:
almost the entire package is reachable from the entry point, so almost nothing can be
changed without risking the entry point.

### Dead package found

`src/voyager_quant/` contains 7 files, all `__pycache__/*.pyc`, zero `.py` sources, and zero
references anywhere in the repository. It is the compiled residue of a renamed package, and
being the only thing in `src/` besides `min_agent`, it makes the layout look ambiguous to a
newcomer for no benefit.

### Correction: "11 modules are unimported" was wrong

A static import scan flagged `attribution`, `calibration`, `counterfactual`,
`experiment_registry`, `lineage`, `model_registry`, `offline_validation`, `regime` as having
no importers. All are imported - by `daemon.py:15`,
`from min_agent import counterfactual, offline_validation`, a form the scan's `ImportFrom`
handling missed. **Nothing in `min_agent/` is dead code.**

### `research/` is production-excluded on purpose

`src/min_agent/research/` - `backtest.py`, `walk_forward.py`, `trials.py`, 845 lines - is
imported by tests only. `tools/verify.py:317 check_production_research_separation` fails
the build if production ever imports them. This is a correct separation of diagnosis from
execution and must be kept, not "fixed" by wiring research into production.

## 4. State and schema

| Path | Role | Regenerable |
| --- | --- | --- |
| `runtime/min_agent/journal.jsonl` | **source of truth**, append-only | no |
| `runtime/min_agent/strategies/*.json` | strategy specs + lifecycle | from journal + admission |
| `runtime/min_agent/reflection.json` | 50-cycle evaluation snapshot | yes |
| `runtime/min_agent/heartbeat.json` | liveness + source fingerprint | yes |
| `runtime/min_agent/curriculum_state.json` | curriculum dedup state | yes |
| `runtime/min_agent/risk_baseline.json` | daily loss baseline | yes |
| `runtime/min_agent/doctor-history.jsonl` | watchdog history | append-only |
| `runtime/min_agent/research_trials.jsonl` | research-only trial ledger | append-only |

`runtime/` is correctly gitignored.

**Journal size: 58 MB across 9,901 lines - ~5.9 KB per line.** Cycles embed a full snapshot
as a Python `repr`, so each line carries price, positions and account state inline.

### Correction: "the journal has no rotation" was wrong

I grepped `rotate` in `journal.py`, saw no match in the first pass, and concluded there was
none. `_rotate_if_needed` exists and is called from `append` at line 156, with a `backups`
count. This is bounded, not a leak.

### Runtime directory pollution

`observable-smoke-journal.jsonl`, `observable-smoke2-journal.jsonl`,
`observable-smoke*-heartbeat.json`, `observable-smoke*-strategies/`,
`daemon.out.crashed-1324`, `auto-fix.log`, `market-check.log`, `ollama.log` - **zero
references from any code, script, or test.** Debris from manual debugging sessions.

## 5. Infrastructure defects found

| Defect | Evidence | Consequence |
| --- | --- | --- |
| **No dependency declaration at all** | no `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements*.txt`, `environment.yml` | a fresh clone cannot be installed; the 3 real dependencies are discoverable only by reading imports |
| **Package is not installable** | no entry point declared | `pip install -e .` impossible; `minictrl` hardcodes an absolute interpreter path |
| **Four redundant shell wrappers** | `monitor.sh`, `observe.sh`, `run_forever.sh`, `check-market-open.sh` | each is a thin wrapper re-implementing CLI flags (`--status`, `--doctor`, `--daemon`, `--reconcile`, `--evidence`). Two run modes for the same operations |
| **`minictrl` hardcodes paths** | 204 lines of absolute interpreter and unit paths | environment not reproducible across hosts |
| **Docs dominated by one narrative file** | `SYSTEM_AUDIT.md` = 3,756 of 6,529 doc lines | a newcomer reads a chronological debugging log before learning what the system is. The actual design docs (`plans/`, `specs/`) are a fifth the size and are not the entry point |
| **No README at repository root** | absent | the first thing a newcomer looks for does not exist |
| **No CI** | no `.github/` | nothing verifies a change on push |
| **No lint or type config** | `pytest.ini` only | the `lint`/`type` stages of the requested iteration loop have no tooling |

## 6. Test and verification baseline

Recorded **before** any change, so "still green" is a claim about a known starting point:

```
make test              PASS  (62s)   759 passed
make verify            PASS  (51s)   29 classes, 0 failed, 1191 executions / 60 files
make doctor            PASS  (11s)
make audit             PASS  ( 4s)
make integrity         PASS  ( 4s)
make verify-self-test  PASS  (14s)
```

60 test files under `tests/min_agent/`. `tools/verify.py` is a custom gate layer of 29 check
classes. Several are genuine invariants worth keeping and extending: production may not
import `research/`; every deployed unit's `EnvironmentFile` must resolve to a real file; the
running daemon's source fingerprint must match the worktree; replayed FIFO accounting must
independently reproduce the reported PnL figures; production must not import research-only
symbols.

## 7. What this map implies

Ordered by the objective's own rule (delete -> merge -> simplify -> reuse -> rewrite -> add):

1. **Delete.** `voyager_quant/`; runtime debris; four shell wrappers that duplicate CLI flags.
2. **Add (infrastructure, not logic).** A `pyproject.toml` declaring the 3 real dependencies
   and a console entry point. Without this nothing else is reproducible.
3. **Simplify.** Make the CLI import lazily where it exists only for one subcommand, so the
   core `--daemon` path does not drag 25 modules.
4. **Rewrite docs.** One README that answers what/how/where, an architecture doc, and the
   existing audit demoted to history rather than being the entry point.
5. **Keep.** `research/` separation, the 29 verify classes, the journal as sole source of
   truth. These are the parts that make the system trustworthy; they are not bloat.

## 8. Import cost: measured, not assumed

`cli.py` imports 24 `min_agent` modules at module level, which looks like the coupling
problem this refactor was supposed to reduce. It was measured instead of restructured.

```
--help                          0.62s   (of which bare interpreter start is 0.10s)
import min_agent.cli             0.76s
import alpaca_trade_api          1.83s
```

`--help` is *faster* than importing the Alpaca SDK alone, because `cli.py` already defers
`import alpaca_trade_api` into the four functions that need it (`cli.py:172, 219, 398,
536`). The heavy dependency is already off the help path.

`requests` shows 175ms of the profile via `min_agent.broker_evidence`. Deferring it was
tried by measurement rather than by taste: removing the top-level import and adding a
function-local one made `--help` **14ms slower**, not faster, because `alpaca_trade_api`
imports `requests` itself and the cost is paid either way once any subcommand runs.

So the import surface was left alone. Restructuring it would have added an indirection and
a comment explaining why, in exchange for nothing - which is the opposite of the stated
goal of reducing long-term complexity rather than line count. The real reduction already
happened elsewhere: five duplicated shell entry points became one CLI, and one canonical
`min-agent` console script replaced an absolute interpreter path baked into a script.

## 9. Acceptance audit

Each criterion the objective names, and the evidence for it. Not a claim of completion -
a list of what was checked and what the check returned.

| Criterion | Evidence |
| --- | --- |
| One authoritative implementation per concept | Risk limits are defined in `config.py` alone (`Guardian` takes them as parameters, so it cannot disagree); `submit_order` appears in exactly one module, `executor.py:38`; the journal is written in exactly one place, `journal.py:153`. Two other append sites exist - `doctor.py:144` and `research/trials.py:59` - and both write separate ledgers with their own schema (`doctor-history.jsonl`, `research_trials.jsonl`), not a second copy of the journal |
| One primary execution path per core task | `loop.py` runs one cycle and only one: snapshot → decision → `Guardian.review` → `Executor.submit` → journal. `--daemon`, `--once` and `make run` all enter through it |
| One source of truth for config and data semantics | `runtime/min_agent/journal.jsonl`; the other six state files are derived and the table in section 4 says which. Environment variables are read only by `config.py`; the other occurrences are documentation, assertions that a variable exists, or installer plumbing |
| Tests for key behaviour | 61 test files, all mapped, no unclassified file and no ghost mapping. `class-coverage` now *requires* a test behind every behaviour class, so a substantive check added untested fails the gate |
| Fresh clone runs from the documentation | Cloned to an empty directory, new `conda create`, `pip install -e ".[dev]"`, 800 tests pass, lint clean, `min-agent --help` works - with zero Alpaca credentials set |
| Fast verification loop | `make check` ≈ 40s (lint + tests), `make smoke` ≈ 9s against the live broker |
| Full experiment traceability | `make reproduce` writes commit, dirty state, interpreter, pinned dependency versions and broker clock to `runtime/min_agent/reproduce.txt` |
| Understandable without the history | `README.md` (what/how/where/debug), `docs/ARCHITECTURE.md` (this file), `docs/MIGRATION.md` (what changed and what replaced it). `SYSTEM_AUDIT.md` opens by declaring itself historical |

### One thing deliberately not done

The import surface in `cli.py` was left alone. Section 8 has the measurements: deferring
`requests` made `--help` slower, and the Alpaca SDK is already deferred into the four
functions that use it. Restructuring it would have added an indirection in exchange for
nothing measurable.

