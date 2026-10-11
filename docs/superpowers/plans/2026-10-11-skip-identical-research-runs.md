# Reuse prior results for an identical research rerun

## Problem and measured baseline

The first real-data run on this worktree evaluated 11 SPY candidates over 24,237 five-minute
bars and took approximately 2 minutes 49 seconds. `research.driver.search()` reports exact repeats
but still calls `run_walk_forward()` for every candidate before deciding not to append them. The
daily research service can therefore spend minutes recomputing a dataset the fetcher correctly
identified as current. The previous same-dataset unit test checked only ledger length and did not
prove repeated backtests were skipped.

## Success criteria

1. When every candidate already has an exact ledger match (rule, parameters, symbol, bar count,
   and dataset fingerprint), return those stored results without invoking `run_walk_forward()`.
2. Preserve cumulative trial counts/verdicts from the original evaluation; an identical rerun
   must not create trials or move the evidence bar.
3. Report `backtests_run` so the operator can distinguish a cached no-op from a completed new
   search. New/changed data still runs the full existing evaluation path.
4. A test replaces `run_walk_forward()` with a failure callback and proves the exact rerun succeeds
   from the ledger. Record the real-data first-run time as the baseline only; do not rerun the
   expensive search before implementing the cache path.

## Verification

Run the research-driver tests, then `make lint`, `make type`, `make check`, `make verify`, and
`git diff --check`. Re-run `make research-search` against the exact cached fingerprint; success is
zero backtests, 11 repeats, unchanged ledger row count, and a substantially shorter wall time.

## Scope

Only exact-repeat detection/result reuse and its tests/reporting are in scope. Do not cache across
different fingerprints, change candidate generation, or weaken trial-count and verdict rules.

## Execution record (2026-10-11)

The baseline real search took about 2m53s for 11 candidates over 24,237 bars. The exact-repeat
path now returns ledger rows without invoking the walk-forward function. On the same real cached
fingerprint, `make research-search` reported `backtests_run=0`, `repeats=11`, unchanged 11-row
ledger, and wall time 5.461s (about 31× faster). A test patches `run_walk_forward` to fail if
called, so cache success cannot hide a recomputation. Focused research tests passed 24/24. Final
verification: `make check` 1,253 passed / 3 skipped; `make verify` 52/52 classes, 1,979
executions, 0 failed; lint, mypy (47 source files), and `git diff --check` passed.
