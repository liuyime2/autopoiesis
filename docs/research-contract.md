# Offline research execution contract

Every CLI, Make and scheduled threshold search requires an explicit, already
registered supported spec. The driver and `research-check-tape` use the same
preparation gate. Missing, edited, unsupported or corrupt registrations stop before
loading data, backtesting or appending trials. Registration records immutable bytes,
SHA-256, parsed fields, source path and its first timezone-aware timestamp; identical
registration preserves that timestamp. A corrupt existing registration is refused,
never repaired by overwriting history.

Use `configs/research.spec.example` as a schema example. It declares a historical
integrity diagnostic. Copy it to an external location, review its fixed window and
settings, then use the Make entry points:

```sh
make research-register SPEC=/absolute/path/frozen.spec
make research-verify SPEC=/absolute/path/frozen.spec
make research-check-tape SPEC=/absolute/path/frozen.spec REPLAY_SYMBOL=SPY
make research-search SPEC=/absolute/path/frozen.spec REPLAY_SYMBOL=SPY
```

`AUTOPOIESIS_PREREGISTRY` defaults to `runtime/autopoiesis/preregistered` and
`AUTOPOIESIS_RESEARCH_TRIALS` defaults to `runtime/autopoiesis/research_trials.jsonl`.
Relative paths resolve against the working directory, including installed-package
use; neither depends on source directories or site-packages. Make also accepts
`PREREGISTRY=`, `RESEARCH_TRIALS=` and `REPLAY_CACHE=` for isolated diagnostics.
Keep the cumulative trial ledger across attempts; selecting a fresh ledger to obtain
a lower sample-size bar invalidates any scientific comparison.

`threshold_grid_v1` means exactly the existing candidate generator: one
TREND_FOLLOW per declared positive threshold plus one FIXED_SIZE buy-hold baseline,
quantity 1 and confidence 0.6. Thresholds are fractions, not percentage points;
`0.002` means 0.2%. The anchor comes from the first selected bar, and each backtest
re-anchors on its own first bar. Thresholds must be unique and yield distinct
four-decimal candidate IDs. `candidate_budget` must equal the threshold count plus
one. There is no optimizer, earliest-60% selection, alternative rule or freeform
execution promise. Generic historical specs can retain metadata registrations but
cannot run this CLI. Unsupported fields and rule/metric/evaluator names fail closed.

`walk_forward_v1` uses the existing evaluator, fixed declared `n_folds`,
`min_train_bars`, `commission_pct` and `slippage_pct` (costs per side in percent).
Folds must have at least five selected bars each, with at least two preceding
training bars. The training segment immediately precedes each OOS segment and is
length-matched where history permits. Fixed stress scenarios and seeds remain part
of the evaluator implementation. `oos_return_pct_after_cost_v1` is the arithmetic
mean of each fold's net-PnL / spent-notional return percentage; it is not a portfolio
return, compounded return or Sharpe ratio. `expected_trades` is positive intent
metadata, never an override of the sample-size gate. Required OOS trades still rise
as `ceil(10 * sqrt(cumulative_attempts))`. This is a heuristic, not formal
Bonferroni or family-wise false-positive control; counted trades and neighboring
folds are not established as independent. Independent holdout and formal family
validation remain necessary before claiming an edge.

The tape selects the declared instrument and interval, then the inclusive UTC
window, before checking selected row quality and merging files. Cache provenance
must match the broker-fetch format. Existing fetch filenames supply interval when
older payloads omit it. Corrupt matching cache files, naive/invalid timestamps,
nonpositive/nonfinite or inconsistent OHLC, invalid volume and nonchronological
selected rows are refused. Identical timestamp/close overlaps across files are
deduplicated; conflicting closes stop the run. Unrelated cache files and excluded
intervals/windows are named. This validates local cache integrity, not cryptographic
broker authenticity. Never place synthetic data in a paper or live cache.

Trials record contract digest/fields, first registration metadata and factual
`preregistration_scope` (`registered_before_window` or
`registered_after_window_start`). All these runs are labelled integrity diagnostics:
local timestamps cannot prove a hypothesis was conceived without seeing data.
Registering already studied history establishes byte integrity only. Even a
registration before the window does not prove independence from other data or
establish an alpha claim. Ordered tape fingerprint, spec digest, actual evaluator
implementation bytes (including rule implementation), costs/folds and parameters
control repeat reuse. Exact repeats run zero backtests and append zero trials;
changed tapes/contracts/implementations/settings are new attempts. Concurrent CLI searches acquire a nonblocking session lock on the ledger before
reading attempts or backtesting; a second session refuses rather than racing counts.
A corrupt ledger
is refused so cumulative counts cannot silently reset. Incomplete persistence makes
the command fail. Preserve failed-run records and investigate before retrying.

Scheduled research reads `AUTOPOIESIS_RESEARCH_SPEC` from the existing external
environment file and calls `make research-run SPEC=...`. This verifies the supported
contract before installation or fetching, derives the exact symbol/window/interval
from it, fetches into the same `REPLAY_CACHE` the search reads, then runs the shared
gate. Fetch failures fail the unit; there is no fallback to an old cache. Windows
stay fixed; there is no daily moving six-month window or post-data registration.
Broker data lag may require waiting until a prospective window is available. No
service or credential file is installed or changed by these source edits.

The public Python `search()` remains available for explicitly labelled unit tests
and diagnostics. Its output never promotes a strategy and cannot authorize trades.
Synthetic fixtures prove software behavior only; the execution plan records real
read-only cache observations separately.
