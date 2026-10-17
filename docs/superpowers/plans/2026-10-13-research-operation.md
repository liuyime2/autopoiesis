# Configure the real scheduled research path

This is an operational plan, not a new strategy or an alpha claim.

**Measured problem/baseline:** External AUTOPOIESIS_RESEARCH_SPEC is absent. Installed old timer uses the older fetch/search path. Existing global `runtime/autopoiesis/research_trials.jsonl` has 143 valid JSON object rows; none has a new registered evaluation identity. Existing combined replay cache has conflicting timestamp values. The clean earlier diagnostic has zero passing candidates and a separate ledger that must not replace the global history.

**Success:** A supported immutable external historical-integrity spec is registered in the real runtime registry. `make research-run` fetches real bars into a new named canonical cache, verifies the same tape, evaluates and persists against the existing global ledger. Actual counts and verdicts are reported, including no PASS. Exact repeat performs zero backtests and appends zero rows. Deployed research service resolves these same paths; missing/failed contract/fetch stops the unit. Original cache, ledger history and credentials are preserved.

**Minimum steps:**

- [x] Copy unchanged configs/research.spec.example to an external nonsecret `.spec` next to the external operator config. Register and verify through Make with explicit actual runtime registry. Registration after historical data is labelled integrity diagnostic.
- [x] Run Make research-run with explicit SPEC, canonical REPLAY_CACHE, real PREREGISTRY and original RESEARCH_TRIALS. Verify expected 143 + actual fresh attempts, not a new lower-count ledger. No schema rewrite or historical metadata backfill.
- [x] Re-run search through Make to prove exact reuse and zero duplicate rows. Retain real command output and run timing.
- [x] Append only absent research path settings to the external operator env file; never copy/print credentials. Retain previous absent-setting state for rollback. Install reviewed service through Make later; inspect its actual rendered entry and timer (installation remains pending).
- [ ] Record scheduled historical diagnostics separately from independent future holdout, alpha discovery, admission and profitability, which remain unverified.

The current timer actually uses local 06:30 despite its comment saying UTC. This task records the real timezone/schedule instead of relying on that comment; changing schedule is not needed to run the fixed historical contract. No risk setting, trading credential, journal history or strategy status is modified by these operational steps. If a command fails, preserve its output and isolate the fault before retrying.

## Actual command evidence

External spec `/localscratch/liuyime2/ohome/.config/autopoiesis/research-integrity-20261013.spec` retains example bytes; SHA-256 `795261baf57d12de814dfc45ac18700473a047a3ea0ac125c5788349baea4187`, first registration `2026-10-13T06:06:48.031420+00:00`. `make research-register` and `make research-verify` with actual registry both exited 0. Credentials stayed in the original external env file.

`make research-run SPEC=<external spec> PREREGISTRY=/localscratch/liuyime2/QuantGroup/runtime/autopoiesis/preregistered RESEARCH_TRIALS=/localscratch/liuyime2/QuantGroup/runtime/autopoiesis/research_trials.jsonl REPLAY_CACHE=/localscratch/liuyime2/QuantGroup/runtime/autopoiesis/replay-threshold-grid-integrity` exited 0. It installed declared research dependencies, fetched **24,237 real SPY 5Min bars**, and evaluated **11 fresh candidates**. Global attempts **143 → 154**, required trades **125**, **1 OVERFIT / 10 INSUFFICIENT / 0 PASS**, zero unrecorded results. No strategy was admitted. Historical registration is `registered_after_window_start`, an integrity diagnostic.

Dataset fingerprint `9137cff68e231f520d1b8ceaa7f3cb8592398162d22088ea0ee508afcd70296b`; evaluator `4501e3a03f4895726b38a5e699475666342b7f70bd09ad3e0b7332199e44e6ea`; evaluation identity `2e74b57e318ae5742d97bfa4034fc3650aeb45f72b2f26c8a18646a8f8fd4c7e`.

`make research-search` with those same explicit paths then exited 0: **154 already / 0 backtests / 11 repeats / 0 unrecorded**. Private raw command logs: `/tmp/autopoiesis-open-research-{register,verify,run,repeat}.log`. The four previously absent research-only path settings were appended to the external env file; rollback removes only those added settings, never copies/restores credentials or resets the ledger. Actual installed-source scheduling still requires the deployment unit.
