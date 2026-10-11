# Diagnose the daemon shutdown budget before changing service behavior

## Problem

The paper daemon has been killed by systemd after receiving SIGTERM because it did not exit within the service's 30-second stop window. Raising the timeout without bounding all synchronous work could only move the failure later; reducing model timeouts could corrupt the measured curriculum workload.

## Measured baseline

- `tools/autopoiesis.service.in` sets `TimeoutStopSec=30`.
- Historical `journalctl --user -u autopoiesis.service` evidence records two stop jobs that reached the 30-second deadline and ended in SIGKILL.
- The curriculum HTTP transport is synchronous and has `CURRICULUM_TIMEOUT_S=600`.
- The same real curriculum prompt was measured at 141.9 seconds with `num_ctx=8192`; therefore normal measured work already exceeds the stop budget by 4.73×. The 600-second request ceiling is 20× the stop budget.
- `AgentDaemon._request_stop()` only sets `_stop_requested` and writes a `STOPPING` heartbeat. `run()` invokes `_maintenance()` synchronously before checking the loop condition again; no cancellation handle reaches the in-flight request.
- The decision transport has an explicit 240-second timeout by default; curriculum has a 600-second timeout. Both are synchronous `requests.post` calls.
- Broker call sites do not pass a timeout. The installed `alpaca-trade-api` is 3.2.0; its `REST._one_request()` calls `self._session.request(method, url, **opts)` without a `timeout`, and default retry settings permit 3 retries with 3 seconds between retries. Thus the HTTP operation itself currently has no finite upper bound; retries do not fix that.
- **Unknown:** the historical journal identifies the timeout and SIGKILL, but does not identify which maintenance stage was executing. Do not claim that curriculum caused those two specific incidents.

## Goal and success criterion

Establish a reproducible, paper-safe shutdown contract before modifying the unit or model transport. The next implementation must satisfy both:

1. A SIGTERM never permits a new broker-clock check or trading cycle after the in-flight synchronous stage returns.
2. The service's stop budget is derived from the explicit maximum duration of every synchronous operation that can be active at SIGTERM, with a stated buffer. Normal successful curriculum generation must not be made to fail merely to fit the old 30-second budget.

The current task is diagnostic: map the stop path and inventory relevant request timeouts. Do not stop/restart the deployed daemon, issue orders, tune model context/timeout, or alter systemd service behavior while the longest blocking operation remains unbounded or unmeasured.

## Diagnostic checklist

- [x] Trace `SIGTERM → _request_stop → current call return → _maintenance return → run loop exit` from source. The handler records STOPPING and sets a flag; `_maintenance()` remains synchronous; the loop has no way to interrupt the current request.
- [x] Inventory explicit request limits. Decision HTTP: 240s config default. Curriculum HTTP: 600s. Broker: 0 explicit timeout arguments across 29 call sites; installed SDK 3.2.0 calls `Session.request` without `timeout`, with three retries and three-second retry waits. Doctor/local Ollama probes use five seconds; watchdog's `systemctl` subprocess uses 60 seconds.
- [x] Reuse the real journal evidence and measured curriculum latency above; explicitly preserve the unknown historical active stage.
- [x] Define the next implementation gate: broker operations must gain a finite deadline and order submission timeout ambiguity must remain reconcilable by `client_order_id`; only then calculate the service stop budget.
- [x] Record limitations: no service was stopped, no runtime request was injected, and the source/SDK inspection does not prove the exact stage active during prior SIGKILL events.

## Next implementation verification

- Before code changes, add Makefile-driven tests using a clearly labelled fake to prove transport timeout behavior and that post-timeout order outcomes remain reconcilable. Synthetic timing is control-flow evidence only, never a claim about live latency.
- Verify paper-order idempotency and fill/order reconciliation under an ambiguous submit result. No repeated submit is allowed while the first client order ID remains unresolved.
- After bounded request behavior is proven, add a graceful-stop integration test for stop-state persistence and no post-stop broker/trading work, then derive `TimeoutStopSec` from the largest measured request budget plus explicit overhead.
- Do not infer that this diagnostic document alone fixes the observed SIGKILL.

## Diagnostic result

The source audit completed. The shutdown budget is not currently measurable as a finite number because broker HTTP calls have no timeout. Therefore changing `TimeoutStopSec` now would still be guesswork. The next task must first bound the broker request layer and test its retry/error behavior; only after every blocking path has an explicit bound should the service stop window be recalculated. The 600-second curriculum path remains the current largest *declared* timeout, not the proven whole-daemon maximum.

## Scope boundary

No production code or deployed service changes in this diagnostic unit. Keep later implementation changes limited to one validated behavior at a time. Guardian, market-open checks, paper-only mode, broker credentials, and order execution remain unchanged.
