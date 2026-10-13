# Fresh-clone isolation repair

Problem: run-fresh-clone.sh unsets credential variables and XDG_CONFIG_HOME,
but Make can immediately reload the real default or explicitly inherited env
file. Inherited AUTOPOIESIS_JOURNAL can also point a clone at live state.
The old log's unset-variable probe does not prove child-process isolation.

Baseline: zero overrides of ENVFILE/AUTOPOIESIS_ENV_FILE in CLEAN_ENV; the
committed script contains nine verification steps, including Make entry points
that include the external env file. Reproduce a child Make loading a synthetic
external sentinel file using a sandboxed clone/interpreter fixture before edits.

Acceptance: no operator env file or inherited project runtime configuration
reaches verification children. Preserve HOME; give children an empty disposable
XDG config directory and explicit /dev/null env-file overrides. Logs name the
actual tested commit and isolation controls. Failures must propagate.

Minimum design: remove inherited project/broker/model configuration, override
both env-file variables, and use the existing Make entry points consistently.
Add a Make fresh-clone target after concurrent backup Make edits are frozen.
No changes to credentials or actual runtime/service state.

Verify: a labelled synthetic shell/Make subprocess regression, scoped lint,
then a real fresh committed clone with a new isolated venv and all documented
checks. The fixture proves environment plumbing only; the real run proves
installation and execution.

Red regression: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/
test_fresh_clone.py'` -> **1 failed in 0.41s**. The script declared credentials
absent while both child Make checks loaded the synthetic external env file.

Integration coverage baseline: `make verify ENVFILE=/dev/null
CLASS=test-coverage-map` fails with four unclassified new files (research contract,
learning evidence, local installer, fresh clone). Map each to at least one class
that actually runs it, plus relevant invariants. Success is zero unmapped tests;
do not weaken the coverage gate. Current tree contains 94 test files; reconcile
the README count and reproduction commands before the full gate.

Independent review found ignored setup failure: a failed venv creation can leave
an activation script and proceed to PASS through another interpreter. Add an
explicit synthetic partial-setup exit-9 case; fail early and record FAIL using
strict shell error handling and an ERR trap. Clarify actual CI checks rather than
claiming exact fresh-clone parity. Editable clone verification and installed
package release checks establish different properties.

Partial-venv setup reproduction: **1 failed, 1 passed in 0.78s** before strict
shell handling; after repair **2 passed in 0.50s**, scoped lint clean.

Probe failure variant: **1 failed, 2 passed in 1.06s**. Command substitutions
inside echo mask a failed interpreter/version probe. Capture both in standalone
checked assignments before writing the normal verification header.

Final independent review approved: **3 passed in 0.64s**, scoped lint and
whitespace checks clean. Real committed-clone execution is still pending.
