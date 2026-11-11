# Deployment runbook

Step 0.2/0.3 of `docs/EXECUTION_PLAN_2026-11-05.md`. **The single entry point for a
deployment is `tools/deploy_release.py`.** Nothing else may install or switch a build.

## Who deploys

One session only: the session that owns the running daemon. Every other session works
on a branch and never deploys. The deployer may publish and roll back; it may not
change strategy or research conclusions.

## Pre-flight

1. `git status` clean on the branch being deployed; commit is pushed.
2. Full gate green on that commit: `make record-deployment` (runs the gate and
   writes the verdict). `deploy-gate-record` must be the only allowed red.
3. Confirm the target is outside market hours (ET 09:30-16:00, weekdays), or
   obtain an explicit emergency reason.

## Deploy

```
python3 tools/deploy_release.py --approved-by <name>
```

What it does:
- takes an exclusive lock on `runtime/deploy.lock` (a second deploy is refused);
- refuses if the market is open (override only with `--emergency REASON`);
- creates `releases/<short-sha>/` beside the **main checkout** (also when run from a
  linked worktree) as a detached git worktree for the commit, and links its `runtime`
  to the shared `runtime/`;
- appends an entry to `docs/evidence/release-actions.jsonl` (commit, approved_by,
  rollback_to, emergency, source fingerprint). The full-gate ledger
  `docs/evidence/deployments.jsonl` is written only by `make record-deployment`,
  run inside the new release directory; append its new line to the branch's ledger.

Corrected 2026-11-07, before the tool's first real use: it put releases inside the
invoking worktree, did not link `runtime/`, wrote rows without `fingerprint` into the
gate ledger (which `equity_curve.cohort_start` reads by that key), and chmod-ed the tree
0444, which removed `./autopoiesis`'s executable bit and blocked the gate's own record.

Optional: `--install-service` installs the systemd units from the release tree: the daemon's units
(`./autopoiesis install-service`) and then the readiness and VIX timers (`make install-readiness-timers`,
with this release's fingerprint and the deploy time as the cutoff). The second step was missing until
2026-11-11, and `market-preflight`, `market-open-verify` and `vix-refresh` ran from an October release,
the readiness pair failing every day with `SOURCE_MISMATCH`. The gate's `units-where-systemd-looks` class
now fails if any installed unit runs from a release other than the daemon's.

Run the gate as `PATH=<env>/bin:$PATH make record-deployment NOTE=...`. Do not put `PY=...` or any other
variable on the `make` command line: make passes it to the tests' own child `make` calls (`tests/conftest.py`
now removes `MAKEFLAGS` for that reason).
Optional: `--dry-run` prints the plan without changing anything.

## Verify after deploy

```
make verify          # 59/59
python3 tools/verify_runtime_identity.py   # commit, fingerprint, pid, cwd agree
```

The installed build, the release directory, and the ledger entry must agree on
commit and fingerprint.

## Roll back

Re-deploy the previous commit recorded as `rollback_to` in the ledger entry:

```
cd releases/<rollback-short-sha> && git checkout <rollback-sha>
python3 tools/deploy_release.py --approved-by <name>   # from that tree, or
```

In practice: check out the rollback commit in the main tree and re-run
`deploy_release.py`. The ledger keeps the chain.

## Market hours

Deploys are refused during ET 09:30-16:00 on weekdays. The check uses the host
clock; `AUTOPOIESIS_MARKET_OPEN=1|0` overrides it for tests. An emergency bypass
requires a non-empty reason that is recorded in the ledger.

## Backup and restore

Local only (`docs/AMPLIFICATION_STANDARD.md` 2026-10-14 decision). Drill:

```
make journal-drill
```

Snapshots live under `runtime/backups/` (see `make journal-local-backup`). Restore
only into a scratch directory; `journal-restore` refuses the live runtime dir.

## What is never done

- No in-place switch of a release directory.
- No deploy during market hours without an emergency reason.
- No second deployer.
- No weakening of the gate to make a deploy pass.

## Branch policy (2026-11-11)

`main` is the trunk and GitHub's default branch. `goal/edge-first` is the working branch and is kept identical
to `main` after every deploy (`git push origin goal/edge-first:main` is a fast-forward). Other work is a
short-lived branch off `main`. Releases are checkouts of commits on `main` under `releases/`; only the live
one and its rollback target are kept. The tips of branches that were folded in are kept as `archive/*` tags.
