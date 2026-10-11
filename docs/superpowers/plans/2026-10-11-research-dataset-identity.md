# Give each alpha trial an exact dataset identity

## Problem and measured baseline

The research driver deduplicates trials using `strategy_id`, kind, parameters, symbol, and
`bars` count in `src/autopoiesis/research/driver.py::_already_evaluated`. The existing test
`test_new_data_re_evaluates_a_candidate_already_on_record` verifies only that 800 bars differ
from 1,200 bars. Two price series with the same symbol and length but different timestamps or
prices satisfy every current equality check and are therefore treated as repeats. In addition,
`search()` labels every input `alpaca historical bars; not synthetic`, including the synthetic
series used by its own bookkeeping tests; the API has no source/provenance argument.

Measured code baseline: trial identity compares five fields and does not inspect bar contents;
the source string is a constant. Current recorded trials have no `dataset_fingerprint`, so they
must be treated as legacy and re-evaluated once after this change rather than trusted as an exact
match.

## Success criteria

1. A deterministic SHA-256 fingerprint covers the ordered timestamp/price series consumed by the
   walk-forward evaluator. Same-length data with one changed timestamp or price receives a new
   trial identity; byte-equivalent logical bar sequences retain identity.
2. Every new trial stores the fingerprint. Legacy rows without one never suppress a new trial.
3. Callers provide a non-empty source label. The real cache path passes its recorded provenance;
   tests explicitly label synthetic fixtures as synthetic. No research result claims real data
   solely because it called `search()`.
4. Candidate count and multiple-testing denominator rise only for genuinely new rule/data pairs;
   a repeat over the exact same bars remains idempotent.

## Verification

Add tests first for same-length changed prices, changed timestamps, repeat stability, legacy rows,
required source labels, and source propagation. Run
`make test TEST_ARGS=tests/autopoiesis/test_research_driver.py`, then `make lint`, `make type`,
`make check`, `make verify`, and `git diff --check`. No market fetch or real-data claim is part of
this code verification; the existing recorded real-data evidence remains unchanged.

## Scope

Only trial identity, source labelling, tests, this plan, and the main RSI execution plan are in
scope. Do not alter candidate rules, verdict thresholds, holdout policy, costs, or Guardian
controls in this change.

## Execution record (2026-10-11)

The new baseline test reproduced the collision: after recording one 800-bar tape, changing one
price while keeping the bar count fixed produced `repeats=2`. After the change, the same case
produces two new trials. The test fixtures now explicitly label their source as `synthetic test
fixture`; the real cache entry point passes the cache's recorded provenance. Every result reports
the SHA-256 fingerprint.

Verification: `make test TEST_ARGS=tests/autopoiesis/test_research_driver.py` — 21 passed;
`make lint` passed; `make type` passed with 47 source files; `make verify` passed all 52/52
classes, 1,971 executions across 83 test files, 0 failures. No new real-data experiment was run,
so this change establishes experiment identity and honest provenance labels, not alpha.
