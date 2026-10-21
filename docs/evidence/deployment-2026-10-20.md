# R0: one verifiable release, 2026-10-20

The 2026-10-20 review's R0 asked for a release whose source, process, CI, readiness and record all
agree, and for the runtime to stop being readable by other local users. This is what was done, with
the measurements that made each step necessary.

## What runs now

| | |
| --- | --- |
| Release directory | `/localscratch/liuyime2/QuantGroup/releases/6095193` |
| Source fingerprint | `3b872efd65e02155` |
| Daemon | RUNNING, `error_count=0`, fingerprint equals disk |
| Readiness units | `EXPECTED_FINGERPRINT=3b872efd65e02155`, `WorkingDirectory` the release |
| Shared runtime | `/localscratch/liuyime2/QuantGroup/runtime`, symlinked into the release |
| Journal | the same file throughout, 0600 |
| Gate | 59 classes, 59 passed, 0 failed |
| CI | `ci` #354 across 3.10 / 3.11 / 3.12 and the release job; `pages` #38 |
| Record | `docs/evidence/deployments.jsonl`, `59/59`, `failed_classes: []` |

The development worktree is no longer the run release. The `/tmp` unit backups moved to
`runtime/autopoiesis/rollback-checkpoints/units-before-release-20261020`, owner-only, because a
rollback checkpoint in `/tmp` is not a checkpoint.

## The three defects R0 uncovered, and one it created

**The runtime was readable by every local user.** 0755 directories and a 0644 journal expose account
snapshots, positions and the model's rationales. The class added to guard it found **20,871 exposed
entries of 23,411** on its first run; it now passes at 23,423 owner-only.

**Private has to be established where a file is created.** CI failed at `008bae9` on all three Python
versions, and the cause was the gate's own `doctor` run creating `runtime/autopoiesis` with the process
umask on a checkout that had never traded. The service `UMask=0077` covers only files a *service*
creates. The journal, the lock file and the doctor history now narrow themselves.

**And the first fix was worse than the bug.** It narrowed `parent.parent` unconditionally, which is
`/tmp` for a probe writing a temporary journal - it raised `PermissionError` and killed the replay
safety probe - and would have narrowed the home directory for a journal a few levels down. Narrowing
now applies only to a grandparent named `runtime`, and a directory that is not ours never fails a
write.

**A recorded figure drifted.** README said 115 test files against a tree of 116, and the gate refused
to write a deployment record because of it. The class was right; the figure was wrong.

## How the CI failure was found without CI logs

The job logs need admin rights this session does not have. The failure was reproduced by cloning the
pushed commit and running the gate with `HOME` pointed at an empty directory - which is the environment
CI has: no operator credentials, no systemd units, and a runtime the gate creates for itself. The same
reproduction then confirmed the fix: 59 of 59 in that clone.

## What R0 does not close

R1 to R6 are untouched. They need their own implementation packages - a decision-fact projection, an
equity contract and a measurable benchmark, a falsifiable alpha round, a controlled RSI comparison, a
promotion and observation path, and per-asset capability acceptance. None of that is a consequence of
this release, and none of it is claimed here.

The bookkeeping that R0 asked for and this note does not provide: nothing yet compares the *release
directory name* against HEAD automatically. The identity that is checked is the fingerprint, which is
what the daemon, the readiness units and the record all key on; a release whose `src` is identical
carries the same fingerprint and is covered by the same record.
