# Put real replay-data collection behind the project task runner

## Problem and measured baseline

This isolated checkout has no `runtime/autopoiesis/replay/` cache, so `_load_real_bars()` cannot
run the alpha driver. The repository has `tools/fetch_replay_bars.py`, but the Makefile exposes no
target for it; contributors and the research service invoke the script directly. The script
accepts optional date bounds, whose defaults are a stale fixed week, and writes only real broker
bars when the call succeeds.

After adding the Make target, its guard rejected empty values but passed any non-empty string
through to `client_from_env()`. It did not verify ISO-8601 UTC timestamps or that the start
precedes the end, so malformed input failed only after broker client initialization.

## Success criteria

1. A Makefile target invokes the existing paper-only fetcher and requires explicit symbol, UTC
   start, UTC end, and interval values; an optional rolling flag preserves the existing
   one-file cache behavior. The target and research search use the declared project interpreter.
2. A combined research target orders dependency install, real-data fetch, and offline driver
   execution. No target substitutes generated prices or treats a failed fetch as new data.
3. Help and operator docs explain the external paper-account credentials and explicitly state
   that historical data retrieval is read-only. Tests assert missing dates fail before the
   fetcher runs and dry-run command ordering is reproducible.
4. The fetcher validates both UTC timestamps and their ordering before loading credentials or
   creating a broker client.

## Verification

Test the Makefile argument/target contract without credentials using a temporary fake executable
through Make's `PY` override; assert required values and argument order. Test bad timestamps with
a client factory that fails if called. Then, if the configured
paper-data credentials permit, execute a read-only historical fetch using the actual target and
run the driver against the resulting cache. Preserve the exact command and output; classify an
insufficient sample honestly. Run focused tests, `make check`, `make verify`, and
`git diff --check`.

## Scope

Add the Makefile entry points, tests, minimal README/operations documentation, and this plan. Do
not change broker execution, market data source, research thresholds, or strategy behavior.

## Execution record (2026-10-11)

The Makefile now exposes `fetch-replay-bars`, `research-search`, and the ordered `research-run`
pipeline. Fetch dates are mandatory, the rolling cache option is explicit, and the scheduled
research service uses the same Makefile entry points. A fake-Python test proves missing dates fail
before invocation and verifies exact UTC arguments. The fetcher also rejects non-UTC and reversed
windows before loading credentials. `make -n research-run` shows install → fetch → search order.

The actual read-only fetch succeeded: 24,237 SPY 5-minute bars, 2026-04-10 13:30Z to
2026-10-09 19:40Z. The search produced 11 recorded candidates, 0 PASS (4 OVERFIT, 7
INSUFFICIENT); exact data fingerprint and full candidate results are preserved in
`docs/evidence/alpha-search-2026-10-11.md`. This is a diagnostic real-data run and not a final
holdout or promotion decision. Focused fetch/driver tests passed 33/33. Final verification:
`make check` 1,253 passed / 3 skipped; `make verify` 52/52 classes, 1,979 executions, 0 failed;
lint, mypy (47 source files), and `git diff --check` passed.
