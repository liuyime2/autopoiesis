# tools/

Operator utilities. **None of these are part of the trading path and none are imported by
`src/min_agent/`.**

A previous version of this file documented fifteen one-off diagnostic scripts that no
longer exist — the directory held nothing but `__pycache__` residue. Naming files that
are gone is worse than not naming them: a reader concludes the tool is missing rather than
that it was consolidated, and a `tools/README.md` that cannot be trusted is not worth
maintaining. The scripts that remain are listed here with what each is for; a script that
is deleted rather than replaced has no entry, and `docs/MIGRATION.md` records the ones
this refactor removed.

## The gate — `verify.py`

**`python tools/verify.py`** is the project gate: 51 check classes, non-zero exit on any
failure. `make verify` runs it. Three modes:

- `--self-test` breaks the gate on purpose and asserts it reports failure, because a
  verification command that cannot fail is believed rather than run.
- `--list` names every class and the test files behind it.
- `--only <class> [...]` runs a subset, for re-running one failing class while working
  on it (`make verify CLASS=pnl-accounting` is the same thing through the Makefile).

## The replay environment — real bars, simulated account

**`fetch_replay_bars.py`** takes `[SYMBOL] [START] [END] [INTERVAL]` and writes what the
broker's historical endpoint actually returns into `runtime/min_agent/replay/`, with the
provenance recorded in the file. It never falls back to a generator: a replay over invented
prices can only show that code runs, and this is the one step that needs network and
credentials. Everything below it runs offline against that cache. On a fresh clone run it
once.

The same API is what `broker_evidence` reads for PnL, and it caps a page at 100. Measured on
this account: the daemon's 24-hour window returns 0-20 fills, `minictrl evidence ingest`'s
30 days returns 43, four months 66 — and a nine-month window holds 279, of which one request
returns 100. A full page is therefore reported in `missing_reasons` so a caller asking for
more than the limit gets a stated partial rather than a fragment presented as the window.

**`replay_self_evolution.py`** drives the whole `AgentDaemon` over those bars — not
`TradingLoop` alone — so reflect, counterfactual scoring against what the market did next,
offline screening and the lifecycle ruling each have to fire on their own. Driving the loop
and calling `StrategyLifecycleManager.review()` by hand proves the rules work; it does not
prove the stages are wired to each other. It found two defects that way: the Guardian
refused all 847 cycles as `data snapshot is stale`, and `ReplayDataGateway.current` raised
IndexError once the cursor passed the last bar. Fills stay `REPLAYED`, so it shows selection
pressure reacting to scored evidence and says nothing about whether any strategy has an edge.

**`self_evolution_probe.py`** is the assertion over that harness, and the one the gate runs:
847 cycles with no daemon errors, every evolution stage present in the journal, at least one
ruling carrying its ratio in the reason, the strategy no longer on `PROBATION`, and no
replayed ruling claiming broker-verified PnL. Run standalone it prints a JSON list of what
failed. `make verify` calls it through `check_self_evolution_closes`.

## The independent audit

**`replay_audit.py`** re-derives the load-bearing PnL figures straight from the raw
journal text, with no production code involved, and compares them to what the system
reported. It is deliberately naive arithmetic over the record: if it agreed with the
system by construction it would prove nothing.

**`selection_pressure_probe.py`** is the machine-checked form of the claim that the
lifecycle rules react to the system's own scored evidence: a `REJECT_POOR_DECISIONS` verdict
must retire from `StrategyLifecycleManager` carrying its ratio in the reason, while
`INCONCLUSIVE` and absent evidence must not — silence is not failure. This caught a defect
that was live for months, where the veto existed only in `AgentDaemon._apply_offline_rejection`
so any caller of the rule set got promotion gating without rejection. `make verify` calls it
through `check-selection-pressure-reaches-the-rules`; like the replay probe it verifies the
mechanism, not that any strategy has actually been retired.

**`replay_safety_probe.py`** is the machine-checked form of the four properties that keep
the replay harness out of the PnL ledger: it holds no broker client, reports `REPLAYED`
with no `order_id`, its snapshot source says real-bars-simulated-account, and a journal of
replayed fills evaluates to zero realized PnL. Run standalone it prints a JSON list of
whatever failed, so a failure is legible rather than a traceback. `make verify` calls it
through `check_replay_cannot-reach-the-account`; the docstring there records the same
distinction `check_shadow_live_consistency` insists on — this proves the mechanism, not
that any replay has been run.

**`audit_defects.py`** checks the 61 assertions across 15 recorded regressions are still fixed, and asserts the
D8 safety properties over the ops scripts that remain — that no script assigns a risk
limit, writes a `lifecycle` field, force-enables a strategy, or touches runtime
strategy state.

## The benchmark — `benchmark.py`

The falsifiable target, runnable by anyone without credentials:

```
make benchmark
```

It answers one question — *does the system beat SPY buy-and-hold, after costs, inside the stated
risk budget, over a rolling 60-trading-day window?* — and prints the LLM increment next to its
own attribution coverage, because a share printed without the share of decisions it covers is the
kind of figure that gets retracted later.

It needs **no credentials and no network**: `doctor` is run with `skip_broker=True` and every
number is read out of the journal and the broker evidence already on disk. It does need the
*record*, which lives in gitignored `runtime/` — so a fresh clone reports that there is nothing
to measure and exits 2, rather than printing a number for a run that never happened.

Every number is computed once, by `src/min_agent/doctor.py`, and parsed back out of its check
detail rather than recomputed; a second implementation could disagree with the one the project
argues from.

Exit codes are the contract, so a green run is never mistaken for a passed target:

| code | meaning |
| --- | --- |
| 0 | the target is met over the record |
| 1 | the target is failed: at least one strategy is behind buy-and-hold |
| 2 | nothing to measure — no journal, or no closed lots on record |

Each strategy is compared with SPY on the same capital (its peak exposure) over the same window
(its first entry to its last exit, or to the last valuation while a lot is open). Run it for the
current verdict; this file does not repeat a number that moves. It also prints the record's own window and reports the target window as not met, because
the journal holds 16 trading days and a 60-day window over a 16-day record is a division that
hides the denominator.

## Runtime inspection

**`check_runtime_integrity.py`** reconciles the journal against the strategy library and
the lifecycle decisions: every strategy's state must be explained by a journalled
decision, and every file in the library must have a recorded admission verdict.

**`alpaca_smoke.py`** is a read-only connectivity probe against the paper API — clock,
account, positions, orders. `make smoke` runs it. It was originally `test_alpaca.py` at
the repository root, where pytest imported a network-calling module on every run.

**`provenance.py`** prints what `make reproduce` records: commit, dirty state, the
resolved configuration, package versions, the seed and dataset version (both reported as
absent, with the reason, rather than invented), the output paths, and the metrics at that
moment. A separate file rather than a Makefile recipe because a backslash-continued
Python program inside `$(...)` is consumed by make instead of continuing the shell line,
and fails silently.

## Repairs

**`prune_knowledge.py`** deduplicates the knowledge library by statement, preserving every
`source_ref`.

## Service templates

`min-agent.service.in`, `watchdog.service.in`, `ollama.service.in`, `research.service.in` (the
timer units ship as files, `tools/watchdog.timer` and `tools/research.timer`, which `minictrl`
copies into place) —
substituted by `minictrl install-service`, which uses `systemd_unit_dir.sh` to ask
the systemd user manager where it actually searches rather than trusting the shell's
`XDG_CONFIG_HOME`. Installing to the latter produces files systemd never opens, while
`systemctl is-enabled` still answers "enabled".

The timers are `watchdog.timer` and `research.timer`. They ship as files and `minictrl` copies
them into place, rather than being templated inline, because a timer has no `ExecStart` to
substitute into. That makes them the units here that are not `.in` templates.

`research.service.in` installs as the unit quant-research.service: it fetches real bars from
the broker and then runs `min_agent.research.driver`. It is a separate unit rather than something
the daemon invokes because `check_production_research_separation` fails the gate when any
production module imports the research package — letting the daemon run the search would break
that separation for the sake of one scheduler, and a component that both searches and trades can
grade itself. Its fetch is followed by `|| echo ...` so a broker outage yields "no new bars" and
still runs the search, rather than silently stopping research; the search itself is not tolerant,
because a missing cache is a real failure.
