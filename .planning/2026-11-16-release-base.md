# Preserve the current main branch in the release candidate

## Problem and measured baseline

The readiness candidate branched from the live release commit `ba4a082fa383`. The remote `origin/main` is `21143cd399bc` and is not an ancestor of the candidate. Its two commits, `6c02f92` and `21143cd`, record the live build's gate result and the 2026-11-10 handoff. A release from the candidate as-is would omit both. The source fingerprint is the same, but the audit and handoff history would be incomplete.

## Success criteria

- The release candidate contains the remote `main` commits and all readiness/rollback changes.
- Both the live deployment gate row and every candidate-generated gate row remain present and valid in `docs/evidence/deployments.jsonl`.
- The merged tree has no unintended production-source changes; its rollback dry-run still resolves to the active `ba4a082fa383...` release.
- Run the full repository gate and record the merged candidate commit before deployment.

## Verification

1. Merge `origin/main` into the candidate and resolve any append-only ledger conflict by retaining every independently generated row in its original order.
2. Inspect `git status`, merge diff, resulting graph, and source fingerprint.
3. Run `make record-deployment`, then `make verify-fast`, and dry-run deployment. Do not deploy before the after-close window.

## Safety

No deployment, service restart, trading action, ledger fabrication, or branch deletion occurs in this integration step.
