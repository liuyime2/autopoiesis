# Close remaining whole-account profit-target verdicts

Problem: benchmark/public pages refuse agent-return claims without allocation and
aligned equity, but CLI and daemon still mark a whole-account return as an agent
profit-target success. Broker verification establishes account data provenance,
not attribution of owner flows/positions to the agent.

Measured baseline: existing synthetic CLI test supplies account-only history at
0.11 percentage points, zero cycle records or agent allocation, and expects exit
0/status satisfied. Daemon contains one identical account threshold success path.
CLI help describes 10 percent while code compares 0.10 percentage points and
ignores configured target. Reproduce below before production edits.

Success: account-only positive/negative/missing data cannot mark agent profit goal
satisfied. CLI returns UNKNOWN/unproven with explicit missing-allocation/equity
reason and exit 2; daemon emits unproven/SKIPPED with satisfied=false and same
reason. Preserve raw account return only as explicitly labelled diagnostic. Use
configured target as metadata without changing any safety/risk/market checks.
Existing maintenance target-transition deduplication remains. Help/evaluate docs
must distinguish unknown target from proven failure or execution error.

Minimum design: remove these two account-threshold verdict branches; directly
state unavailable attribution/equity in both existing producers, no new abstraction
or return calculation. Update existing CLI/daemon tests with clearly synthetic
account-only positive and negative cases. Adapt Make evaluate only if needed so
UNKNOWN is preserved as a measurement and unexpected runtime failures propagate.
Run focused tests immediately, independent spec then quality review, full checks
and a newly committed fresh clone after this source change. No broker orders or
production daemon restart. All previous passing evidence retains its actual SHA.

- [x] Red reproduction and surgical producer/consumer repair.
- [x] Focused verification and independent spec/quality reviews complete below.
- [x] Integration/fresh-clone/remote-CI proof on final source `cd2874e` completed below.

## Consumer repair evidence (2026-10-13)

Baseline command before changing the tests:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py -k verify_profit_target'`
returned **3 passed, 8 deselected**. The positive synthetic account-only case
asserted exit 0 and status satisfied with no cycle records.

Before production edits, the replacement synthetic cases and real Make recipe
test were run with:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_daemon.py -k "synthetic_account or make_evaluate or help_renders"'`.
Result: **13 failed, 1 passed, 63 deselected**. Failures included missing UNKNOWN
metadata, account-based verdicts, rejected valid UNKNOWN collection, accepted
exception/malformed reports, and the incorrect CLI help.

The minimum repair removes both account-threshold verdict branches. CLI returns
exit 2/unproven/UNKNOWN and daemon records unproven/SKIPPED, with configured
target metadata and explicit missing agent allocation, cash flows, and aligned
rolling equity history. Raw account return is labelled `account_only_diagnostic`.
Daemon status-transition deduplication remains. The Make collector validates
process exit against structured status/comparison/satisfied fields before
accepting a report as a collected measurement. No dependencies, capital
calculations, order paths, market checks, or risk rules changed.

Immediately after the producer repair:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_daemon.py -k "synthetic_account or help_renders"'`
returned **11 passed, 68 deselected**. After the Make recipe repair:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py -k make_evaluate'`
returned **5 passed, 13 deselected**.

Final focused verification, including additional malformed-exit-2 and mismatched
status/satisfied cases:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_cli.py tests/autopoiesis/test_daemon.py tests/autopoiesis/test_research_fetch_make.py'`
returned **87 passed in 2.86s**. These are synthetic correctness/regression tests,
not evidence of profitability or measured market performance.

`make lint-files ENVFILE=/dev/null FILES='src/autopoiesis/cli.py src/autopoiesis/daemon.py tests/autopoiesis/test_cli.py tests/autopoiesis/test_daemon.py'`
returned **All checks passed**. `make type ENVFILE=/dev/null` returned
**Success: no issues found in 49 source files**.
`git diff --check -- Makefile src/autopoiesis/cli.py src/autopoiesis/daemon.py tests/autopoiesis/test_cli.py tests/autopoiesis/test_daemon.py`
returned no findings. Existing test file count remains **94**:
`rg --files tests/autopoiesis -g 'test_*.py' | wc -l`.

Implementation is frozen for independent review. Existing fresh-clone and remote
CI results from the earlier committed source do not verify this consumer repair;
final-source integration, committed-tree fresh clone, and remote CI remain open.

Independent spec review approved with **87 passed in 2.86s** across CLI, daemon
and existing Make contract tests. Independent quality review approved with **82
CLI/daemon tests passed in 2.69s**, focused lint clean. No hard risk/order/market
path changed. Root full `make check ENVFILE=/dev/null` after this frozen repair:
**1,499 passed / 6 skipped in 38.25s**, lint clean and 49-file mypy clean. Both
reviews closed; committed-source clone, full final gate and exact remote CI remain
integration evidence rather than inferred from these checks.

Final source cd2874e passed real fresh clone (nine steps, 55/55 classes) and remote
CI run 38348912218 (all three Python gate jobs plus release SUCCESS). Root serial
full gate passed 55/55 classes / 2,489 executions / zero failures after clone-log
completion. Documentation-only evidence publication follows; no later source edit.
