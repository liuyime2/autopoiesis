# Give curriculum the latest whole-account book

Written planning phase before code edits; Superpowers implementation and sequential
independent specification/quality review apply after the account boundary freezes.

**Problem / baseline:** Read-only scope audit located one `_open_book` function
which scans all historical cycles backwards, drops quantities <= 0, and returns
the first nonempty positive book. There are zero direct `_open_book` regression
tests in the existing daemon/curriculum files. A latest flat or short-only snapshot
therefore cannot supersede older long positions. Curriculum is supposed to identify
real current capability gaps; an old position cannot be substituted for a later
observed empty book. The relevant account-boundary baseline of 68 passes did not
exercise this case. The actual runtime has 2,326 cycles, making a full scan
unnecessary for a current-book query.

**Success:** The newest recorded real snapshot alone supplies all nonzero signed
positions (including short and fractional quantities), and an empty latest book
returns empty without looking for an older nonempty book. No journal or missing
record returns empty. Use the existing `last_n(1)` bounded read, keeping only the
existing symbol/quantity/market_value fields. No new model, API request, mutation,
trade authorization or relaxation of shorts=shadow. Latest recorded is not a
claim of a newly fetched broker book; keep that limitation explicit.

**Verification:** Before implementation, clearly labelled synthetic real-journal
fixtures in existing test_daemon.py must reproduce old-long followed by flat,
old-long followed by short-only and latest mixed short/fractional-long behavior.
Then focused Make tests, lint/type and relevant daemon/curriculum regressions,
specification followed by quality review, and final source full/fresh-clone/CI.
Do not broaden this change into whole-account COVER or fractional order execution.

- [x] Read-only diagnosis and pre-edit target recorded.
- [x] Reproduce synthetic failures before editing the function: focused Make run
  below produced 5 failures, 2 passes before the function changed.
- [x] Implement the smallest correction and immediately verify regressions:
  7 direct cases and 145 focused daemon/curriculum/account/journal cases passed;
  scoped lint and type passed. Commands and limits recorded below.
- [x] Independent specification then quality approval: specification reran
  **7 passed / 61 deselected in 0.42s** and approved; subsequent quality reran
  **7 passed / 61 deselected in 0.46s** and **145 passed in 1.94s**, diff clean,
  and approved. No broker/performance claim follows from these tests.
- [ ] Bind final checks/deployment and evidence to the accepted source.

## Pre-implementation reproduction (2026-10-13)

`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_daemon.py -k open_book'`
ran against the original function after adding explicitly labelled synthetic
fixtures in a temporary `JsonlJournal`: **5 failed, 2 passed, 61 deselected in
1.18s**. Flat, zero-only and short-only latest snapshots incorrectly returned
the older SPY long. Mixed short-before-fractional-long returned only AAPL and
dropped the short. The API assertion failed when `_open_book` called `read_all`.
No-journal and no-history cases already passed.

The bounded claim is limited to requesting `last_n(1)` and retaining one cycle,
with no historical fallback in this caller. The existing journal `_scan` still
reads and validates all lines while retaining a `deque(maxlen=1)`; this unit does
not change journal scanning or claim a measured I/O, CPU or latency improvement.
Any scan optimization requires a separately measured baseline and separate unit.
The book is the latest recorded snapshot, not a newly fetched broker snapshot.

## Implementation evidence (2026-10-13)

The function now requests `journal.last_n(1)`, returns empty for absent history,
and projects only the latest record's nonzero signed holdings to the three
existing fields. Existing market-value rounding remains. The correction does
not authorize SHORT/COVER or fractional orders and does not alter Guardian,
market-open, model, service or runtime behavior.

Executed after the single function correction:

- `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_daemon.py -k open_book'`
  — **7 passed, 61 deselected in 0.43s**. The synthetic API test observes exactly
  one `last_n(1)` request and rejects any caller use of `read_all`.
- `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_daemon.py tests/autopoiesis/test_daemon_heartbeat.py tests/autopoiesis/test_curriculum.py tests/autopoiesis/test_curriculum_feedback.py tests/autopoiesis/test_manage_account.py tests/autopoiesis/test_journal.py'`
  — **145 passed in 1.89s**.
- `make lint-files ENVFILE=/dev/null FILES='src/autopoiesis/daemon.py tests/autopoiesis/test_daemon.py'`
  — **All checks passed**.
- `make type-files ENVFILE=/dev/null FILES='src/autopoiesis/daemon.py'`
  — **Success: no issues found in 1 source file**.

These test timings are command output, not a runtime benchmark. Fixtures are
synthetic and isolated; no fresh broker read or real account behavior was tested.
Source is ready for specification review followed by quality review. Final
full/fresh-clone/CI checks and deployment remain pending in the parent workflow,
bound to the source accepted by those reviews; none ran in this small unit.
