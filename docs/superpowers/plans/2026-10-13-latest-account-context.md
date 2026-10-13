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
- [ ] Reproduce synthetic failures before editing the function.
- [ ] Implement the smallest correction and immediately verify regressions.
- [ ] Independent specification then quality approval.
- [ ] Bind final checks/deployment and evidence to the accepted source.
