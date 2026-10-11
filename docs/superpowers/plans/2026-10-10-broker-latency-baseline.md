# Measure paper-broker read latency before setting HTTP deadlines

## Problem

The installed `alpaca-trade-api` 3.2.0 calls `requests.Session.request()` without a timeout. There is no finite broker-call duration from which to derive a reliable systemd stop budget. A timeout is necessary, but choosing one by intuition could turn healthy paper requests into failures or leave order outcomes ambiguous.

## Baseline

- Source inspection found 29 broker API call sites and no explicit per-call `timeout` argument.
- SDK `_one_request()` passes no timeout to its session; retry defaults are 3 retries and 3 seconds between retries.
- Broker timings are not recorded in the journal, so current p50/p95/max latency are **unknown**. Do not invent or infer them from LLM latency.
- Order submission uses a deterministic `client_order_id`, but a raised exception is journaled as `ERROR`; current fill reconciliation only scans `SUBMITTED` records. A transport timeout after broker acceptance could therefore become an untracked outcome.

## Goal and success criteria

Add a reproducible Makefile-only, paper-endpoint, read-only latency sample that calls broker clock and account reads, reports SDK operation count, failures, p50, p95, and max wall time, and never prints credentials or submits orders. Run it against the real configured paper endpoint. Use the measured output to choose explicit connection/read/total deadlines in a separate implementation plan. Pin the SDK dependency to the exact audited version so a clean install satisfies the sampler's retry-control contract.

The baseline exposes a dependency mismatch: `pyproject.toml` currently allows `alpaca-trade-api>=3.2,<4`, but the safe retry override in the sampler is audited only against the installed 3.2.0 implementation. The measurable success criterion is that the declared dependency resolves to 3.2.0 on a clean install and that an unsupported version fails closed before any broker call.

The timeout implementation itself is out of scope until this baseline exists and the ambiguous-submit reconciliation contract is specified. No `submit_order`, live endpoint, sampler-level retry, or service-stop command is allowed in this benchmark. The sampler accepts only source-audited Alpaca SDK 3.2.0 and disables its SDK retry loop; one SDK operation therefore makes one HTTP attempt.

## Verification

- Tests use a fake client and clock to prove the command is paper-only, read-only, count-bounded (at most 50 iterations / 100 HTTP attempts), and redacts credentials.
- `make broker-latency-sample COUNT=20` must report all 20 iterations (unless the paper endpoint fails, in which case actual successes/failures are both shown), plus nearest-rank p50/p95/max; errors must fail closed without attempting another endpoint.
- Run `make test` and `make lint` after implementation. The command output is the baseline, not a synthetic fixture.
- Record timestamp, endpoint, SDK version, operation counts, outcome counts, and aggregate p50/p95/max; do not retain raw stdout, response bodies, account identifiers, or credentials. The command prints per-operation detail; only aggregate real-run measurements were retained here, so per-operation percentiles are unknown and must not be reconstructed.

## Measured baseline (real paper endpoint, 2026-10-11 UTC)

Actual monitored execution: `make broker-latency-sample COUNT=20`. The endpoint label was `https://paper-api.alpaca.markets`; SDK was `alpaca-trade-api 3.2.0`; 40 HTTP attempts (20 clock + 20 account) succeeded, 0 failed. Aggregate latency: p50 25.036 ms, p95 27.248 ms, max 90.247 ms. Timestamp: `2026-10-11T02:16:48Z`. The captured run used the same SDK version and retry-disabled behavior; source code was the uncommitted sampler atop commit `505449d`.

The current sampler has a strict operation-count bound but no HTTP connect/read/deadline bound. Do not run unattended or claim stop-time safety from this result: a stuck network call can still hang the sample. This confirms fast observed reads only. Derive per-request deadlines and an ambiguous order-submit reconciliation contract as a separate implementation gate before changing daemon stop policy.

## Outcome and verification

- The first full test run exposed the README's stale test-file count (80 vs 81); updated the measured count. The first full verify then exposed the missing `tools/README.md` entry; documented the new tool and reran the gate.
- Initial full suite before the final retry guard and SDK pin: `make test` — 1208 passed, 3 skipped. A separate full suite after retry disabling and before the extra dependency assertion also passed at that same count.
- Focused sampler suite after the pin: `make test TEST_ARGS=tests/autopoiesis/test_broker_latency_sample.py` — 14 passed. Latest `make lint`, `make type`, and `git diff --check` passed; mypy reports 45 source files.
- `make install` resolved `alpaca-trade-api==3.2.0`; the focused suite also asserts that `pyproject.toml` pins the same audited version.
- Final `make check` after the dependency pin: 1209 passed, 3 skipped; lint passed and mypy reported no issues in 45 source files.
- Final `make verify` after the dependency pin: 52 classes passed, 0 failed; 1906 test executions across 81 files. Eight runtime/state-dependent checks skipped because this isolated worktree has no heartbeat/journal or published-tree plan archive. These are not runtime paper-trading or profitability evidence.
