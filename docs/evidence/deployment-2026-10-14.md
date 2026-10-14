# Deployment record, 2026-10-14

P0's acceptance criterion: a deployment record naming the commit, the CI run and the
fingerprint. Written after the deploy, from the commands that ran.

## What was deployed

| | |
|---|---|
| commit | `752b80c` — "Make the pause readable by the doctor, and stop it binding to $HOME" |
| branch | `codex/rsi-audit-repairs` |
| source fingerprint | `f52fe76e49da051f` |
| deployed at | 2026-10-14 16:02 EDT, after the 16:00 close |
| worktree | `/localscratch/liuyime2/QuantGroup/.worktrees/rsi-audit-repairs`, clean at deploy time |
| daemon PID after restart | 4010199, `NRestarts=0`, `ActiveState=active` |

## CI

All four jobs green on `752b80c` (runs 175 and 176):

    gate (3.10)   success
    gate (3.11)   success
    gate (3.12)   success
    release       success

## Verification after the restart

    daemon heartbeat fingerprint : f52fe76e49da051f
    on-disk source fingerprint   : f52fe76e49da051f
    market-preflight.service     : f52fe76e49da051f
    market-open-verify.service   : f52fe76e49da051f
    6 timers armed

All four agree. The readiness timers had to be reinstalled in the same operation,
because they carry the fingerprint they expect and would have failed tomorrow's
08:45 and 09:45 checks with `SOURCE_MISMATCH` against the superseded value.

    make install-readiness-timers \
      EXPECTED_FINGERPRINT=f52fe76e49da051f \
      DEPLOYMENT_AFTER=2026-10-14T16:00:00-04:00 \
      READINESS_PYTHON=/localscratch/liuyime2/conda_env/llm/bin/python3.10 \
      ENVFILE=/localscratch/liuyime2/ohome/.config/autopoiesis/env

`READINESS_PYTHON` has no default and `DEPLOYMENT_AFTER` must be a full aware ISO
datetime, not a time. Both fail as `TIMER_INSTALL_FAILED` with a bare exception type
and no field name, which cost two attempts here and is worth fixing on its own.

## Gate state at deploy

`make verify`: 52/55, unchanged from before this work. The three failures are the ones
already known and already recorded:

- `daemon-source-matches-worktree` — cleared by this deploy; it failed because the
  daemon was running superseded code, which is what a deploy fixes.
- `replay-audit` — the naive-FIFO versus ledger scope difference. Both figures are
  correct over different universes; the plan is `docs/superpowers/plans/2026-10-13-replay-audit-scope.md`.
  Deliberately not relaxed.
- `self-evolution-closes` — needs scored decisions, which need the 24h horizon to
  elapse on the provenance-carrying decisions.

`doctor`: `RESULT: OK with 10 warning(s)`. The blocking `lifecycle provenance` failure
is gone; it was caused by this session's own pause events missing the key the reader
uses, and is fixed in this commit for new events and read tolerantly for the three
already in the append-only journal.

## Not deployed, and why

The session's earlier commits are in this tree and therefore are deployed by it: the
audited pause entry point (`1bc5d1b`), the unconditional-sell refusal (`ce55855`), and
the curriculum diagnostics and window (`2767704`, `c136c6a`).

`docs/PLAN_2026-10-14.md` on local `master` (commit `cca5b2b`) is **not** deployed and
**not** pushed. Local `master` also carries an uncommitted `src/autopoiesis/research/prereg.py`
change made by another agent, which this session did not touch. Merging this branch into
that tree is a write to shared history that would sit next to unreviewed work, so it
waits for the single-deployer decision the plan itself calls for.

## The deployment rule this followed

One deployer at a time; only committed code whose CI is green on all three versions;
no deploys during an open session; the deploying worktree clean. Each held: the deploy
ran at 16:02 with the market closed, from a clean tree, at a commit whose four CI jobs
had passed.
## Second deployment, same day

| | |
|---|---|
| commit | `0d05fc4` — the P2 work: fallback exposure limit, decision-failure counting, coverage-map entry |
| source fingerprint | `6d38e72bc6753626` |
| deployed at | 2026-10-14 17:09 EDT, market closed |
| daemon PID after restart | 4052852, `NRestarts=0`, `ActiveState=active` |
| CI | runs 183 and 184, `success` on all four jobs |

Verified after the restart: daemon fingerprint, on-disk fingerprint and both readiness
units all read `6d38e72bc6753626`. The timers were reinstalled in the same operation for
the same reason as before - they carry the value they expect.

Two CI failures were diagnosed and fixed by reproducing the runner environment locally
rather than by guessing, because the CI logs need a token this session does not have:

- `dc53a90` failed lint. Two ruff findings were committed with it and only fixed
  afterwards, and CI runs `ruff check` without `--fix`.
- `0d05fc4`'s predecessor failed the gate's `test-coverage-map` class, which requires
  every test file to declare the check classes it covers. `make check` does not run the
  gate, so neither was visible locally. The gate passes 55/55 in a reproduction of the
  runner environment.
