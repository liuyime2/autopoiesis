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

`min-agent.service.in`, `watchdog.service.in`, `ollama.service.in` (the timer unit ships as a file, `tools/watchdog.timer`, which `minictrl` copies into place) —
substituted by `minictrl install-service`, which uses `systemd_unit_dir.sh` to ask
the systemd user manager where it actually searches rather than trusting the shell's
`XDG_CONFIG_HOME`. Installing to the latter produces files systemd never opens, while
`systemctl is-enabled` still answers "enabled".

The timer is `watchdog.timer`. It ships as a file and `minictrl` copies it into place,
rather than being templated inline, because a timer has no `ExecStart` to substitute into.
That makes it the one unit here that is not a `.in` template.
