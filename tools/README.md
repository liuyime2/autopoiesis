# tools/

Operator utilities. **None of these are part of the trading path and none are imported by
`src/autopoiesis/`.**

A previous version of this file documented fifteen one-off diagnostic scripts that no
longer exist — the directory held nothing but `__pycache__` residue. Naming files that
are gone is worse than not naming them: a reader concludes the tool is missing rather than
that it was consolidated, and a `tools/README.md` that cannot be trusted is not worth
maintaining. The scripts that remain are listed here with what each is for; a script that
is deleted rather than replaced has no entry, and `docs/history/MIGRATION.md` records the ones
this refactor removed.

## The gate — `verify.py`

**`python tools/verify.py`** is the project gate: 57 check classes, non-zero exit on any
failure. `make verify` runs it. Three modes:

- `--self-test` breaks the gate on purpose and asserts it reports failure, because a
  verification command that cannot fail is believed rather than run.
- `--list` names every class and the test files behind it.
- `--only <class> [...]` runs a subset, for re-running one failing class while working
  on it (`make verify CLASS=pnl-accounting` is the same thing through the Makefile).

## The replay environment — real bars, simulated account

**`fetch_replay_bars.py`** is invoked through `make fetch-replay-bars`, with explicit
`REPLAY_SYMBOL`, UTC `REPLAY_START`, UTC `REPLAY_END`, `REPLAY_INTERVAL`, and optional
`REPLAY_ROLLING=1`. It writes what the paper broker's historical endpoint actually returns into
`runtime/autopoiesis/replay/`, with the provenance recorded in the file. It never falls back to a
generator: a replay over invented prices can only show that code runs. This read-only fetch is
the one step that needs network and credentials. Everything below it runs offline against that
cache. `make research-run` installs the research extra, fetches the requested window, then runs
the offline driver; a failed fetch stops this explicit run rather than claiming new data.

The same API is what `broker_evidence` reads for PnL, and it caps a page at 100. Measured on
this account: the daemon's 24-hour window returns 0-20 fills, `autopoiesis evidence ingest`'s
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

## Keeping the VIX floor engaged

**`refresh_vix.py`** merges `^VIX` and `^VIX3M` daily closes from Yahoo's public chart
endpoint into the cache the volatility floor reads. It **merges** rather than replaces: the
cache carries 9,263 points back to 1990 and the endpoint returns a bounded window, so
writing what the endpoint returned would discard the long history the six-year quality
window reads. `make vix-refresh` refreshes; `make vix-check` exits non-zero when the reading
is past `VIX_STALE_AFTER_DAYS`, which is the same ceiling `vix_floor` applies.

**`vix-refresh.service.in`** and **`vix-refresh.timer.in`** are the pre-open units that run
it, installed by `install_market_readiness.py` alongside the readiness pair. They are their
own units rather than a step in `market-preflight`, which is read-only by contract; this one
writes. Measured 2026-10-15: without them the cache had been stale since 2026-10-09 and the
floor had been silently disengaged since 2026-10-13.

## Turning "the gate was green" into something a deploy can fail on

**`record_deployment.py`** runs the full gate and appends what it observed to
`docs/evidence/deployments.jsonl`, keyed by the source fingerprint. `make record-deployment` runs
it.

Why a tool and not a line typed into the deployment record: a record an operator types is a record
an operator can type wrongly, and the failure is invisible afterwards - the record says the gate
passed, and it did, on a different tree, at a different time. Only running it can tell the
difference, so this runs it, and writes nothing at all when the gate is red. A ledger that can hold
a red run gets read as "the gate was run", and that is the one thing a red run must never become.

`make verify` then has a class for it: `deploy-gate-record` fails when the installed fingerprint has
no passing record from a *full* run. The tier is written by the tool rather than typed, so a
`--fast` record cannot claim it. What the check deliberately does not do is compare the recorded
class count against today's: the first version did, and it deadlocked the moment a check class was
added - the record said 56, the full run said 57, the gate went red, and this tool refuses to write
while the gate is red, so the record could never be brought up to date.

The order is install, then verify, then record - not verify, install, record. `daemon-source-matches-worktree`
and `doctor` are red before every install by construction, because the daemon is still running the
old build, so a rule requiring a green gate *before* installing is unsatisfiable. A deploy is
instead complete only when the installed build has a record, and a red gate cannot produce one - so
a bad install keeps its red, visibly, as an unrecorded build.

## Keeping the price series honest about distributions

**`refresh_dividends.py`** merges cash distributions from Alpaca's corporate-actions
endpoint into a cache the counterfactual reads, so a decision scored across an ex-date is
scored against the total return rather than a price that fell by the distribution.
`make dividends-refresh` refreshes; `make dividends-check` reports coverage.

The provider was tested rather than assumed, and the first attempt read a 400 as a missing
service: `types=dividend` is invalid, `types=cash_dividend` is the one. Counts: TLT 13,
BIL 13, XLE 5, XLF 5, XLB 5, SPY 4.

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

Every number is computed once, by `src/autopoiesis/doctor.py`, and parsed back out of its check
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

## The daily validation report — `daily_report.py`

```
make daily-report            # today, New York time
make daily-report DATE=2026-10-07
```

Run after every weekday close, at 16:30 New York time, by the timer installed from
`report.timer` and `report.service.in`. It reads the journal and runs doctor without the broker - it places and
reads no order - and writes `runtime/autopoiesis/reports/<date>.txt`:

- the day's first and last cycle, which is how a missed open shows up;
- decisions by source, action and execution result;
- each departure from the strategy's rule, with the model's stated reason;
- SHORT/COVER decisions and the Guardian's verdict on each (shorts run in shadow);
- the validation lines from doctor (`llm vs rule`, `pnl vs holding`, `pnl attribution`,
  `decision quality`) and the benchmark verdict.

## Runtime inspection

**`check_runtime_integrity.py`** reconciles the journal against the strategy library and
the lifecycle decisions: every strategy's state must be explained by a journalled
decision, and every file in the library must have a recorded admission verdict.

**`alpaca_smoke.py`** is the read-only whole-account paper inventory. `make smoke`
prints the clock, cash/equity/buying powers, account blocking flags, crypto status and options levels,
every held position's broker class, and metadata for held/configured symbols.
Missing fields and unreadable asset metadata stay UNKNOWN. It requests 500 open orders
and refuses a full page or invalid contents rather than claiming completeness.
No stock quotes, model or order/account writes are needed. US equities have an existing
adapter subject to Guardian, market and coverage checks; options, crypto and unknown
classes remain unsupported. Keep financial output in private runtime evidence.
It was originally `test_alpaca.py` at the repository root, where pytest imported a
network-calling module on every run.

**`broker_latency_sample.py`** samples clock/account SDK operation latency against the
configured paper endpoint only. Run `make broker-latency-sample COUNT=20`; each iteration
makes one clock and one account read, and COUNT is capped at 50. It prints summary timings
and SDK version, not response data or error text. Only source-audited
`alpaca-trade-api 3.2.0` is accepted; SDK retries are disabled so one operation makes
one HTTP attempt. There is no request deadline yet; do not run it unattended or treat
observed latency as a stop-time guarantee.

**`provenance.py`** prints what `make reproduce` records: commit, dirty state, the
resolved configuration, package versions, the seed and dataset version (both reported as
absent, with the reason, rather than invented), the output paths, and the metrics at that
moment. A separate file rather than a Makefile recipe because a backslash-continued
Python program inside `$(...)` is consumed by make instead of continuing the shell line,
and fails silently.

**`status_page.py`** renders a read-only static snapshot of the same numbers the CLI computes, as
one HTML file. It has no order path and reads no credentials: a page that could trade would be a
second execution path, and the Guardian's single-path property is worth more than a button. A
number that cannot be computed reads UNKNOWN rather than 0, because "not measurable" and "measured
at zero" are different claims. `make status-page` writes six pages to
`runtime/autopoiesis/report/site/`: Overview, Portfolio & Risk (the limits in force beside the
exposure they bound, and the capital the agent-level return divides by), Decisions (the rule's
decision, the model's override and its reason, the Guardian's refusal), Strategies (lifecycle
transitions with their reasons — what the self-evolution actually changed), System (whether the
daemon is running judged by age and pid, and how much of the record these pages can see), and
Evidence (the agent-level claim, the attribution coverage, and the signal ledger's verdict
counts). All six are rendered from one snapshot, so a page cannot be from a different instant than
the page next to it, and the journal is read through `JsonlJournal.history_paths()` so the archive
is part of the record rather than something one directory over.

## The public site — `project_site.py`

**`figures.py`** runs the five collectors and writes `docs/site/figures.json`. **`gate_summary.py`**,
**`test_summary.py`**, **`record_summary.py`**, **`signal_summary.py`** and **`benchmark_json.py`**
each run one real command and print one JSON object; `benchmark_json.py` wraps
`benchmark.py --json`, whose prose goes to a sink so a parser sees one thing. A collector that
fails is recorded as an error rather than omitted, so a page with a missing figure is
distinguishable from a project with nothing to report.

**`project_site.py`** renders that file into `docs/site/` — Overview, Evidence, Architecture,
Safety, Build. It reads nothing else, which is why the site builds on a fresh clone where
`runtime/` does not exist, and why it publishes an aggregate rather than one deployment's
numbers. There are no screenshots: a reader can re-run a command and cannot re-derive a PNG.

`make refresh-figures` runs the collectors and regenerates the pages; `make site` renders only.
Both are committed together, and the date field in `figures.json` is what tells a reader how old a
published measurement is.

**`check_research_tape.py`** runs `research/prereg.py`'s data-quality gate over the tape a
search is about to consume: it verifies the spec is the one registered before the run, loads the
merged bars for the spec's instrument exactly as the driver merges them, and refuses the tape if it
carries a duplicate timestamp, a non-chronological series, an unusable price, or a bar outside the
declared window. `make research-check-tape SPEC=... REPLAY_CACHE=...` runs it between fetching and
searching.

First run against the real cache, and it found something: the SPY tape the driver consumes spans
2016-01-04 to 2026-10-09 - 3931 days - while the spec declared six months of 2026. 110,014 bars
precede the declared window and 50 follow it. So the search that recorded those trials ran over a
decade of SPY it never said it would use, and its own evidence record describes one file rather than
the merged series actually read. The gate returns 1 and the search does not start. The finding is
written up in `docs/evidence/preregistration-2026-10-11.md`; the fix - making the consumed tape match
the spec, or the spec match the cache - is not done.

**`shutdown_budget.py`** derives the service's stop window instead of letting it be picked. It
enumerates the stages that can be in flight when SIGTERM arrives - broker HTTP, the curriculum
generation, the model decision, the doctor probes, the watchdog's systemctl subprocess - reads the
timeout each one *declares* from the source, takes the largest, adds a stated buffer, and compares
the result with the unit template. `tools/autopoiesis.service.in` carries
`TimeoutStopSec=@STOP_BUDGET_SECONDS@` rather than a number, and `autopoiesis install-service`
substitutes the derived one, so a stage timeout that grows leaves the template untouched and only
the budget moves - which `shutdown-budget-covers-every-stage` detects.

The number on this host is 630s: the curriculum generation's 600s ceiling plus a 30s buffer, and
the deployed unit had 30s. That gap is the historical evidence's two SIGKILLs, and it is now a
derived value checked by the gate rather than a number nobody could defend. The stages with no
declared ceiling - the journal write, the atomic file writes - are named in the output, because the
budget is a lower bound and calling it the daemon's maximum would be a guess formatted as a
measurement.

**`journal_snapshot.py`** snapshots the whole journal record - live file, rotated generations,
the archived ones and the archive manifest, each under its runtime-relative path - and restores it
into a scratch directory. `make journal-drill` does the whole path and compares the restored copy
against the sealed snapshot twice: byte for byte against its manifest, and then on the
figures the ledger produces - closed lots, realized and unrealized PnL, the assumed cost. A PASS
means those figures are identical sealed and restored on that execution. A shared journal lock
covers discovery and copying, including all
actual numeric generations; hashing and evaluation use the copy after releasing the writer.
Unparsed lines, ledger exceptions or a missing/invalid/unattested archive manifest prevent a
semantic recovery PASS even when bytes agree. A manual snapshot can preserve such bytes; the
completed backup target requires the same verified archive baseline the runtime integrity gate
uses. It never invents an attestation for legacy history. Numeric generations outside the writer's
retention budget are archived oldest first before ordinary rotation to preserve chronology.

`make journal-local-backup ENVFILE=/dev/null` stages, fsyncs and verifies a backup, then atomically
publishes it under `runtime/backups/<UTC timestamp>-<unique id>/`. No credentials are read by the
scheduled service. `JOURNAL_RUNTIME=/absolute/runtime` and `BACKUP_ROOT=/absolute/backups` select
explicit locations. Existing copies are retained; operators must provision storage and arrange
retention rather than expecting automatic deletion. The installed backup timer runs hourly
and catches up a missed run with `Persistent=true`; persistent installation verifies the unit and
checks `enable --now` and its active state. Installation itself does not run a backup immediately.

Run `make journal-drill ENVFILE=/dev/null JOURNAL_RUNTIME=/absolute/runtime` to prove recovery
in disposable directories. Restore with `make journal-restore ENVFILE=/dev/null
JOURNAL_RUNTIME=/absolute/live-runtime SRC=/absolute/backup DEST=/absolute/scratch` and compare
the sealed ledger figures. This protects journal history on one host. Strategy files, knowledge,
operating policies, host loss and offsite durability require separate recovery arrangements.

The relative paths are the point. `history_paths()` finds the archive under
`journal-archive/`, so a flattened copy is a record whose own reader cannot see part of it - the
first version of this tool flattened them, and only the generation count in the drill caught it.
`restore` refuses to write to the configured runtime directory: restoring a stale snapshot over the
live record destroys the only copy that was right. Restore verifies bytes before copying and its
CLI checks semantic equality afterwards. A snapshot that does not verify is not restored at all.

**`check_workflow_jobs.py`** asserts every workflow file's `jobs:` contains the jobs it defines.
It exists because a `release` job appended to `ci.yml` at column 0 instead of under `jobs:` is
valid YAML with `release` as a sibling of `jobs` - the workflow ran only `gate`, reported failure
with zero jobs, and nothing on this host could see the difference, because the only thing that
reads those files is GitHub. `make verify` runs it as `workflow-jobs-reachable`.

## Service templates

`autopoiesis.service.in`, `watchdog.service.in`, `ollama.service.in`, `research.service.in`,
`report.service.in`, `backup.service.in` (the timer units ship as files,
`tools/watchdog.timer`, `tools/research.timer`, `tools/report.timer` and
`tools/backup.timer`, which `autopoiesis` copies into place) —
substituted by `autopoiesis install-service`, which uses `systemd_unit_dir.sh` to ask
the systemd user manager where it actually searches rather than trusting the shell's
`XDG_CONFIG_HOME`. Installing to the latter produces files systemd never opens, while
`systemctl is-enabled` still answers "enabled".

The timers are `watchdog.timer`, `research.timer`, `report.timer` and `backup.timer`.
They ship as files and `autopoiesis` copies
them into place, rather than being templated inline, because a timer has no `ExecStart` to
substitute into. That makes them the units here that are not `.in` templates.

`research.service.in` installs as the unit quant-research.service: it fetches real bars from
the broker and then runs `autopoiesis.research.driver`. It is a separate unit rather than something
the daemon invokes because `check_production_research_separation` fails the gate when any
production module imports the research package — letting the daemon run the search would break
that separation for the sake of one scheduler, and a component that both searches and trades can
grade itself. Set `AUTOPOIESIS_RESEARCH_SPEC` in the external configuration to an
explicitly registered supported contract. The Make entry point verifies it before
fetching its declared symbol, interval and window. Fetch failures stop the unit
with a nonzero exit; it does not substitute an older cache for a failed fetch.

## Read-only market preparation and session evidence

`market_readiness.py` runs through `make market-preflight` and `make market-open-verify`.
Both require an explicitly frozen `EXPECTED_FINGERPRINT` (16 lower-case hex digits);
`make source-fingerprint` prints the imported source identity for the deployment checkpoint.
`DATE=YYYY-MM-DD` selects the broker session; the default is today in America/New_York.
Open verification additionally requires an aware `DEPLOYMENT_AFTER` timestamp.
For example, after recording the actual deployment identity and cutoff:

```bash
make market-preflight EXPECTED_FINGERPRINT=<recorded-16-hex> DATE=2026-10-14
make market-open-verify EXPECTED_FINGERPRINT=<recorded-16-hex> DEPLOYMENT_AFTER=<recorded-aware-timestamp> DATE=2026-10-14
```

The collector reads the actual running PID's environment privately and resolves paths
against its cwd. It requires a fresh live heartbeat, exact expected/imported/loaded source,
an existing journal lock and trusted archive manifest, complete full history with no
unresolved submissions, a current real broker clock/calendar and an active unblocked paper
account. READY/VERIFIED require RUNNING at both the initial and final heartbeat
observation. A fresh live STARTING or BACKING_OFF daemon produces fixed-code WAIT,
including a daily-cycle-cap backoff even when earlier session coverage is complete.
The report retains the latest bounded daemon status, cumulative error count and
broker/model health booleans or null; it omits raw heartbeat messages. Historical errors
alone do not permanently reject a recovered RUNNING process, and unknown heartbeat
model health is separate from the actually observed preopen model response. Initial
STOPPING/STOPPED/ERROR, dead/stale, source or paper-configuration faults still fail.
It constructs the shared production Hybrid/Guardian pipeline with a read-only
journal and never runs the daemon, reconciliation, maintenance, admission or an order.
Preopen obtains configured-symbol data and observes one covered-symbol model response;
other symbols have data/selection observations only. Guardian reviews the actual final
frozen decision at model completion time. Closed-market and stale-quote refusals remain
refusals; this does not certify an open-market approval. Additional held/attention-symbol
coverage remains explicitly unobserved.

Open verification reads cycles from the actual calendar open and deployment cutoff, checks
configured first-round coverage, fresh recorded quote times, eligible frozen provenance,
and durable intent/acknowledgement identity for approved submitted orders, including unique
broker order IDs. Cumulative broker fill snapshots use the existing fill projection and
match the selected submission's client/order/symbol/side/strategy identity. Each order
contributes its latest observed cumulative quantity once; quantities must be finite,
nonnegative and within the requested order quantity. Observations after the collection
time cannot supply current fill evidence. Partial orders remain in
`pending_fill_observations`, while terminal filled/canceled/expired/rejected observations
close that broker observation. `learning_feedback` remains UNOBSERVED independently. A safe uncovered
HOLD with no attribution is counted separately. Approved actionable decisions with no
submission remain explicitly unexecuted. No fills are required, and profitability remains
UNPROVEN. Missing configured coverage is WAIT for the first 60 minutes after the later of
open/cutoff and FAIL afterwards; holidays and closed sessions are WAIT. Historical cycles
carry no inferred producer fingerprint from today's heartbeat.

Exit 0 means READY (preopen) or VERIFIED (open), 1 means FAIL, and 2 means WAIT. Make propagates
the process failure; its own failure status is not the collector's verdict. Reports contain
bounded allowlisted scalars and fixed diagnostics, without model rationale, SDK dumps,
account identifiers or credentials. By default each report is a new dated mode-0600 JSON
under the actual journal parent's `readiness/`; `READINESS_OUTPUT` chooses a new `.json` path.
When the running process configuration cannot be established, the failure is stdout-only.

`install_market_readiness.py` is a separate explicit installer, invoked by
`make install-readiness-timers`. Its templates are `market-preflight.service.in`,
`market-preflight.timer.in`, `market-open-verify.service.in`, and `market-open-verify.timer.in`.
Provide `READINESS_ROOT`, the absolute `READINESS_PYTHON` interpreter, an external
`READINESS_ENVFILE`, `EXPECTED_FINGERPRINT`, `DEPLOYMENT_AFTER`, and the actual manager's
`READINESS_UNIT_DIR`. The Make default for the latter follows XDG config, but an already
running user manager may use a different directory; supply the directory it actually reads.
The installer validates safe absolute paths, renders all four definitions in a private
staging directory, runs `systemd-analyze --user verify` before replacing installed bytes,
then reloads/enables only the two reporting timers and lists their next triggers. It does
not start or restart the trading service.

Services invoke Make with the frozen values and a 1,200-second whole-process budget (the
configured single model preview normally has a 240-second request budget). Timers run on
weekdays at 08:45 for preopen and 09:45/10:30 for open, explicitly America/New_York. They
pass `SCHEDULED=1` through Make to the collector's explicit `--scheduled` option. Only a
consistent WAIT verdict returns scheduled process success; its private report and stdout
retain status WAIT and exit_code 2. A FAIL returns nonzero through Make and fails the
service. The services use the ordinary success status 0, because Make maps multiple
collector failures to its own status 2. Ordinary operator commands preserve the collector
process exit contract.
Reinstall with a newly frozen fingerprint/cutoff after a source deployment. Installation
and real broker/model observations are separate proofs from the labelled synthetic tests.
