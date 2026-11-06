# INVALID — smoke output, not evidence

The two files in this directory (`ab_smoke.txt`, `abcd_smoke.txt`) are retained for
traceability only. **Do not cite them as results.**

Measured reasons they are invalid:

- The simulator had no cash constraint. On the committed B/C rules, gross exposure reached
  4.66x starting equity and cash reached **-236,489.83** (see
  `docs/REPLAY_INTEGRITY_PLAN_2026-11-06.md` §2).
- It never called `Guardian.review`; the production risk layer was absent.
- It filled at the same bar close used by the decision, not the next bar's open.
- The "D arm LLM" response was printed and then ignored; the replayed rule used hard-coded
  thresholds. The driver script was not committed, so the run is not reproducible.
- No manifest, dataset fingerprint, config digest, or cost declaration was written.

The corrected path is specified in `docs/REPLAY_INTEGRITY_PLAN_2026-11-06.md`. These files
may be deleted only after the corrected four-arm report exists.

## Corrected run

`reports/20261106/corrected/` is the first run on the production Guardian path (commit
`b5dca29`). It is reproducible from `run_manifest.json`. It is **still not a G4 verdict**:
D is `NOT_RUN` (no LLM weekly engine) and A is a cash baseline, not the core sleeve.
