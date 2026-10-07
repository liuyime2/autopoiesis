# Historical record

Everything in this directory is point-in-time. A figure here was correct on the date it was
written and is not a claim about the present; for current numbers run `make doctor` and
`make benchmark`.

| File | What it is |
| --- | --- |
| `STATUS.md` | The running log: what was found, measured, fixed and retracted, in order. Its top table holds the fixed configuration; everything below its "Dated section" marker is dated |
| `SYSTEM_AUDIT.md` | The defect log from the recovery audit |
| `PHASES.md`, `CAPABILITY_CLASSIFICATION.md`, `STEP_CONTRACT.md`, `KNOWN_DATA_LOSS.md` | Audit artefacts the gate (`tools/verify.py`) still reads |
| `known_state_findings.json` | Known findings `tools/check_runtime_integrity.py` reconciles against |
| `plans/` | One file per change: baseline, falsifier, what was found. Plans that turned out wrong say so in place rather than being deleted |
| `specs/` | Runbooks and specifications, superseded ones marked |

These were moved here from the repository root and `docs/superpowers/` on 2026-10-06 so the
front of the repository describes the system rather than its argument trail. Nothing was
deleted: retracted claims stay, marked, because the record of why a number was wrong is what
stops the next session from repeating it.

**Git history is kept as provenance and is not rewritten.** Earlier commits contain the
author's account name and host paths that the current tree no longer does. Rewriting history
would break every commit reference in these documents, and the history is the evidence for
the claims they make. Decided 2026-10-06.
