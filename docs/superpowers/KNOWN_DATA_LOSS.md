# Known data loss

A record of data that was lost and could not be reconstructed. Written because a gate that
reports what is missing is better than one that quietly passes over the gap.

## 2026-10-01: one research trial record deleted

**What was lost.** The last line of `runtime/min_agent/research_trials.jsonl`, a
walk-forward trial recorded at `2026-09-29T13:25:57` between
`trend-follow-buy-005` (`.805622`) and `trend-follow-sell-001` (`.836556`). The ledger went
from 27 records to 26.

**How.** I truncated the file with `splitlines()[:-1]` to test whether the new
`research-trial-ledger` assertion would fail on missing data. The test did not fail - the
assertion I had written checks for traceability and verdict validity, not for the record
count being unchanged, so removing a record entirely passed it. The file was not backed up
first, and the shell variable holding the original content was scoped to a different
invocation, so the restore raised `NameError` and the truncated state is what remains.

**Why it cannot be reconstructed.** The strategy library contains no strategy matching the
missing trial, and the journal records no research event at that timestamp - the batch that
produced these 27 trials was run by a script that wrote only to the ledger. There is no
backup, no rotated generation (the ledger has no rotation configured), and the file is
gitignored, so it was never committed.

**What it means.** One of 27 research trials has no record. The trials were all
`INSUFFICIENT` - none passed - so no admission decision rests on the missing row, and no
strategy was promoted or retired because of it. The loss is a completeness gap in the audit
trail, not a change in any conclusion.

**What changed afterwards.** `research-trial-ledger` now asserts what it can actually verify
about each record - that it names a strategy, carries a recognised verdict, is not
duplicated, and that the summary's count matches the record count. It does not and cannot
assert that a record which once existed still exists; nothing in this repository can, because
the ledger has no integrity mechanism of its own. That gap is now written down here instead
of being invisible.

**The lesson.** A destructive test on live state needs a backup taken in the same
invocation, and a test that mutates the thing it is testing should be verified to actually
fail. Mine passed while data was being lost underneath it, which is worse than no test: it
reported the ledger as fully accounted for 30 seconds after a record had gone missing.
