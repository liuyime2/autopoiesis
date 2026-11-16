# Deployment rollback target correction

## Problem and measured baseline

The deployment dry-run derives `rollback_to` from the full-gate ledger. After recording the candidate full gate, `python tools/deploy_release.py --dry-run --approved-by liuyime2 --install-service` reported rollback target `fa65125`, even though that commit has not been deployed. The active user service reports `WorkingDirectory=/localscratch/liuyime2/QuantGroup/releases/ba4a082fa383`, `MainPID=2553651`, and `NRestarts=0`; `git rev-parse` in that release is `ba4a082fa383c3cc3cfc4b4a2c41ec7a0d6b47eb`. `release-actions.jsonl` ends at `557fc620`, so its historical ledger is also behind the active release. The gate ledger and actual deployment state have different meanings.

## Success criteria

- Dry-run identifies the active daemon's release commit (`ba4a082fa383...`) as its rollback target, never a commit that only has a gate record.
- If no active service identity is available, dry-run returns a null rollback target and real deploy refuses; it never guesses from a gate ledger or stale action history.
- Regression tests cover the active-release path and prove appending a gate record cannot change rollback selection.
- The release dry-run and relevant deployment tests pass; no deploy, service restart, or ledger backfill occurs as part of this repair.

## Verification

1. Add tests using a controlled systemd identity/process working directory and synthetic ledgers clearly labelled as test data.
2. Run relevant tests through the Make target (`make verify CLASS=unit-integration`) and re-run `tools/deploy_release.py --dry-run` against the real user service.
3. If this tool change is included in the readiness release, rerun the complete deployment gate and record it on the final commit before any after-close deploy.

## Scope and safety

This change only corrects rollback identity selection. It does not deploy, stop services, edit production ledgers, change trading behavior, or weaken any risk gate.
