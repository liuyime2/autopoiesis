# Local journal recovery repair plan

Final independent quality approval: **90 passed in 2.87s**, whitespace checks
clean. Real `make journal-local-backup ENVFILE=/dev/null` completed on the main
runtime into `runtime/backups/20261013T042445Z-fc7750573671`: five files,
219,034,423 bytes including the archive manifest, four journal generations,
2,326 cycles, 21,671 events, zero unparsed lines, archive CERTIFIED, 47 closed
lots, realized 757.95, unrealized 89.59, assumed cost 35.818876.
`make journal-restore` into a disposable directory completed exit 0 with all
semantic figures equal. Main journal was never replaced. A separate full drill
with restored-byte checks is running as final integration evidence. These are
journal backups on this host, not full-state or offsite recovery.

Problem: backup copies a journal that may rotate or append during copying, hashes
the live source after copying, and evaluates the live source rather than the sealed
copy. Recovery currently accepts equal ledger failures as success. Reader discovery
happens before locking and both backup and reader apply arbitrary generation limits.
The installer also expands `$$` into a process ID in two PYTHONPATH overrides.

Measured inspection baseline: backup discovers numeric generations only 1..19;
the default reader only 1..3. Two readers discover files before their shared lock.
Two installer PYTHONPATH assignments contain `$$`. No local backup timer exists.
The existing nine backup tests use invalid pseudo-cycles and prove no cycle recovery.
Record executable regression measurements below before each implementation unit.

Success: all actual numeric generations recover in order; discovery and copying
hold one shared journal lock, while hashing and evaluation use only sealed bytes.
Backup verification detects unparsed lines and ledger errors. A completed backup
is fsynced, verified and atomically published under a unique name. A credential-free
hourly Persistent timer invokes the Make entry and installer checks running state.
Valid, explicitly synthetic cycle fixtures prove append/rotation isolation, semantic
failure refusal and environment propagation without starting actual services.

Minimum design: reuse journal shared lock, atomic JSON writer and snapshot manifest;
enumerate actual generations independent of the writer retention setting. Add one
scheduled backup CLI command with staging and atomic publication, no automatic
deletion policy. This covers journal recovery only, not full host state or offsite
durability. User has explicitly chosen reliable local backups first.

- [x] Replace invalid backup fixtures; reproduce deep-generation loss and lock race.
- [x] Fix journal discovery under lock and snapshot sealed-byte consistency.
- [x] Refuse unsupported semantic recovery and atomically publish durable backups.
- [x] Add credential-free Make/systemd entry, installer rendering and PYTHONPATH fix.
- [ ] Independent review and actual read-only drill by parent. Focused checks below pass.
- [ ] Daemon stop boundary repair is a separate follow-up after learning review.

Verification: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_journal.py
tests/autopoiesis/test_journal_rotation_recovery.py tests/autopoiesis/test_journal_snapshot.py'`,
scoped `make lint-files`, `make type`; installer regressions stub child processes.
No live runtime writes, service installation, restarts or trading are authorized here.

## Executed evidence

- After correcting a fixture that omitted required execution fields, backup regressions
  returned **4 failed, 9 passed in 0.41s**: `.21` omission, source append changing sealed
  figures, torn-line recovery PASS and equal ledger exceptions accepted as PASS.
- Reader regressions reproduced old scan discovery returning only `after` and retention
  returning only `newest`. The daily-counter fixture initially used a frozen-model
  assignment and then a HOLD action; both fixture mistakes were corrected to an
  explicitly synthetic BUY/SUBMITTED row before the successful focused run.
- Deep imported history was incorrectly attested as an empty fresh install:
  `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_journal.py
  -k deep_generation --tb=short'` -> **1 failed, 21 deselected in 0.35s**.
- Installer regression Make run -> **3 failed in 0.68s**: literal PID corruption
  in PYTHONPATH, absent backup timer, and failed unit verification still exiting 0.
  Stub subprocesses never contact the actual service manager.
- Directory-fsync failure injection -> **1 failed, 16 deselected in 0.37s**:
  permissive common atomic IO hid durability failure. Backup publication now uses
  strict directory fsync and propagates its failure without claiming completion.
- `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_local_backup_installer.py
  tests/autopoiesis/test_journal_snapshot.py tests/autopoiesis/test_journal.py
  tests/autopoiesis/test_journal_rotation_recovery.py --tb=short'` ->
  **48 passed in 1.19s**. Actual nonblocking flock proves the copy excludes a writer;
  actual append/rotation after lock release leaves the sealed four-cycle fixture intact.
  Make child environment check proves six synthetic broker/model credentials removed.
- Final scoped `make lint-files ENVFILE=/dev/null` for journal/snapshot and their three
  test files -> **All checks passed**. `make type ENVFILE=/dev/null` -> **49 source files clean**.

Independent spec review found two additional failures and requested regressions:

- Transient installer reported success for an invisible, failed-to-start or inactive
  backup timer. Focused synthetic stub reproduction returned **3 failed, 4 deselected
  in 0.84s**. The start loop now accumulates all unit failures and exits nonzero.
- First-use backup directory creation did not fsync newly created parent entries.
  Reproduction returned **2 failed, 17 deselected in 0.40s**. Before publication,
  every newly created directory entry is now strictly fsynced back to the first
  existing ancestor; failure propagates with no completed backup published.

Re-freeze: the same four-file focused Make test command above returned **53 passed
in 1.70s**, followed by **All checks passed** for scoped lint; `git diff --check`
returned zero. Backup/snapshot/installer source is frozen for re-review, not committed.

Spec re-review found that a failed first directory fsync leaves the new directory
visible; a retry previously skipped the existing parent's fsync. Exact failed-create
then retry regression returned **1 failed, 19 deselected in 0.37s**. Simpler final
design resolves the destination and strictly syncs it and every ancestor on *every*
attempt, so incomplete prior creation is not implicit machine state. Final expanded
four-file Make focused run -> **54 passed in 1.58s**, scoped lint -> **All checks passed**.
Re-frozen for review; no source is committed yet.

## Quality review follow-up: usable archive and rotation order

Problem/baseline: semantic recovery validates zero archive manifests. The four-cycle
fixture incorrectly claimed `next_generation=1` with zero listed entries while an
archive file existed; the current runtime integrity gate would reject that record.
Deep generation discovery also exposes writer ordering: with `.21` older than `.3`,
normal rotation archives `.3` before `.21`, so the reader returns newer history first.

Success: a completed local backup and drill certify only a verified, attested
archive baseline and complete parsed history. Invalid/missing/unattested manifests
remain byte-preservable through snapshot, but cannot receive semantic PASS or be
published as a completed backup. Never automatically attest legacy history.
Rotation preserves original chronology across all actual deep generations or fails
before destructive mutation if that imported layout cannot be verified safely.

Design: reuse `archive_manifest_status` on the sealed copy (only call it if manifest
exists, avoiding empty-install mutation), surface archive status/reason in figures
and compare recovery eligibility. For rotation, either archive out-of-budget numeric
generations oldest first under the existing writer lock or refuse unsupported
imported layouts before moving files. Add actual `.21`/`.3`/live synthetic regression
and invalid/missing manifest recovery regressions before modifying source.

- [x] Reproduce invalid archive semantic certification and deep rotation reordering.
- [x] Repair each root cause and rerun focused recovery tests; lint/types pending below.
- [ ] Quality/spec re-review and actual read-only recovery proof by parent.

Red `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_journal_snapshot.py
-k archive_integrity tests/autopoiesis/test_journal.py -k "archive_integrity or
deep_imported" --tb=short'` returned **5 failed, 42 deselected in 0.70s**. Missing,
invalid, unattested and wrong-checksum archive manifests all got semantic PASS;
rotation returned `third, oldest` instead of `oldest, third`.

Repair: fixture now holds the actual three retained generations and correct archive
bytes/hash/sequence plus explicit synthetic attestation. Sealed figures report
archive recovery as CERTIFIED only after the existing integrity check verifies a
trusted baseline; absent manifests are UNKNOWN without calling the mutating empty
baseline initializer. Snapshot still preserves bytes, but failed archive validation
blocks completed backup and semantic drill/restore success.

Before ordinary rotation, all numeric generations beyond the configured horizon
are archived oldest first under the existing append lock. The existing manifest is
validated before moving deep files, so invalid/gapped imported layouts cannot turn
into a silently reordered record. Baseline attestation remains unknown for imported
history unless the operator has explicitly attested it.

Final quality repair verification: `make test ENVFILE=/dev/null
TEST_ARGS='tests/autopoiesis/test_local_backup_installer.py
tests/autopoiesis/test_journal_snapshot.py tests/autopoiesis/test_journal.py
tests/autopoiesis/test_journal_rotation_recovery.py tests/autopoiesis/test_durability.py
--tb=short'` -> **90 passed in 3.50s**, followed by **All checks passed** for scoped lint.
Expanded chronology checks cover writer
budgets 0, 1 and 3, plus a gapped import refusing before moving any data generation.
Scoped lint passed and `make type ENVFILE=/dev/null` reported **49 source files clean**.

Limits: numeric generation discovery follows actual disk history; writer retention
still determines future rotation. Backup service requires explicit installation; no
actual timer was installed here. Transient backup timer is rendered and started but
is lost with `/run` at reboot, as the existing transient installer states. Existing
transient Ollama/research/report deployment choices are outside this focused unit.
No local directory provides resilience to host loss. No retention deletion is added.
Semantic PnL availability remains whatever the recorded journal can establish.

## Real local recovery and hourly scheduling (2026-10-13)

Independent spec/quality reviews approved commit `0ee9170`. Real read-only commands
through Make on the main journal produced a completed 5-file backup (219,034,423
bytes) at `runtime/backups/20261013T042445Z-fc7750573671`; restoring into an isolated
scratch directory matched all bytes and semantic figures. Full `make journal-drill
ENVFILE=/dev/null JOURNAL_RUNTIME=<main runtime>` returned PASS: 2,326 cycles,
21,671 events, zero unparsed lines, four journal generations, CERTIFIED archive,
47 closed lots, realized 757.95, unrealized 89.59, assumed cost 35.818876. Commands
and raw outputs are retained privately in `/tmp/autopoiesis-audit-real-{backup,restore,drill}.log`.
These figures describe recorded paper-account accounting, not portfolio return.

Scheduling baseline measured with `systemctl --user show quant-backup.timer`:
LoadState=not-found, ActiveState=inactive. Success: install only the credential-free
backup service/timer, validate units, run one actual scheduled service, verify its
exit status and new completed snapshot, then enable hourly persistent timer and
inspect next-run time. No trading service restart is needed. Minimum configuration
renders the reviewed service against the retained repair worktree/interpreter but
explicitly points JOURNAL_RUNTIME and BACKUP_ROOT at the main runtime. Preserve the
worktree until normal source deployment updates this service path. This completes
local journal scheduling only; host loss, offsite storage, full-state recovery and
retention policy remain recorded follow-ups. No existing backup is deleted.

Scheduling execution: reviewed service/timer rendered into the manager's durable
`$HOME/.config/systemd/user`, `systemd-analyze --user verify` passed, and an actual
`systemctl --user start quant-backup.service` exited 0 / Result=success, publishing
`runtime/backups/20261013T043537Z-04070b58fa63`. System journal records start
00:35:37 EDT and completion 00:36:03 EDT. `enable --now quant-backup.timer` reports
enabled/active, next run 2026-10-13 01:01:30 EDT. The trading daemon remained active
with the same PID 2800047. These measurements establish this first local scheduled
service execution, not future uninterrupted operation or reboot/host-loss recovery.
The service path uses the retained repair worktree and explicit main journal paths;
update it during eventual normal deployment before deleting that worktree.
