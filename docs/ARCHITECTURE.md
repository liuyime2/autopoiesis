# Architecture & Runtime Map (pre-refactor)

Produced by reconnaissance against the working tree, not by reading the existing plan
documents, which this refactor is instructed to ignore. Every claim was checked; where a
first reading was wrong the correction is recorded rather than dropped, because three of
the four largest findings were initially misread.

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
`market-state.json`, `curriculum_state.json`, `strategies/`.

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
