# Market-open readiness, 2026-10-12 08:48 EDT (42 minutes before the 09:30 open)

Every item below was measured on this host at the time stated, with the command that measured it.
This is a readiness record, not a claim about trading results.

## Passed

| Check | Measured | Command |
|---|---|---|
| daemon | `active`, heartbeat RUNNING, written 08:29 | `systemctl --user is-active` + heartbeat mtime |
| daemon runs the code on disk | disk fingerprint `f980fa3192d5d0a1` == heartbeat's | `source_fingerprint()` |
| no unresolved submissions | `unresolved: []`, 97 known order ids | `project_submission_state` |
| archive manifest | `valid: True, trusted: True` | `archive_manifest_status()` |
| broker clock | `market CLOSED; next_open=2026-10-12 09:30:00-04:00` | `make doctor` |
| risk limits unchanged | position<=25000, exposure<=90000, daily_loss<=500, trades/day<=100; "unchanged since the recorded baseline" | `make doctor` |
| paper only | `mode=paper`, base URL the paper host | `make doctor` |
| decision path | real engine, real context: **55.2s, `decision_source=llm`**, coherent HOLD with a rationale | `OllamaDecisionEngine.decide()` |
| model resident | `qwen3.8:27b` loaded, 17.3GB in VRAM | `ollama /api/ps` |
| GPU headroom | 18GB of 46GB used, 0% utilisation on both | `nvidia-smi` |
| sleep policy | closed: ≤900s and trimmed inside the staleness budget; open: 300s — it cannot sleep past the open | `scheduler.sleep_seconds` |
| long rounds keep heartbeats fresh | a heartbeat is written per symbol inside `_run_symbol` | source |
| timers and units | all five `enabled`/`active` | `systemctl --user` |
| gate | 55/55 classes, 2078 executions | `make verify` |
| working tree | clean, 0 ahead of origin/main | `git` |
| disk | 21T free | `df` |

## Two things a reader should know

**The decision path takes 55-81 seconds per symbol, and six symbols are traded.** A round is
therefore five to eight minutes against a 300-second interval. The daemon sleeps only the remainder
after a round, so it does not fall behind, and a heartbeat is written after each symbol so the
heartbeat never goes stale mid-round. The 240-second decision timeout is not at risk on an idle box
(0% GPU utilisation, no competing model loaded), but it is the number to watch: a timeout fails
closed to a HOLD, which is safe and means the model contributed nothing for that cycle.

**The model returned an empty response to a bare probe** (`num_predict=16`, HTTP 200, 38.5s,
`response: ''`). That is the probe being wrong, not the model: with the engine's real payload -
schema-constrained, `num_predict=256` - the same model returned valid JSON in 81s and a decision
with `decision_source=llm` in 55.2s. It is recorded here because a reader who probes the model the
cheap way will conclude it is broken, and the cheap way is what a person under time pressure does.

## Not checked, and why

Whether the daemon actually trades at the open. It has run no cycle since its last restart (the
weekend), so `cycle_count: 0` and `last_model_ok: null` describe a daemon that has not been asked
yet rather than one that failed. `--once` was not run to force it: the pid lock correctly refuses a
second daemon while one is live, and forcing a cycle outside the schedule would place an order on
the strength of a pre-open snapshot. The first cycle after 09:30 is the test, and its outcome will
be in the journal.
