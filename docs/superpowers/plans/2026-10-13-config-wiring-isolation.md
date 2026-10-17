# Verify declared configuration consumers without relying on host secrets

> Use Superpowers subagent-driven-development; independent specification review
> followed by quality review. Implement after the readiness unit freezes.

**Problem / measured baseline:** Frozen readiness full `make check ENVFILE=/dev/null`
at 8719fcc plus its unit3 diff produced **1 failed / 1,563 passed / 6 skipped in
81.03s**. `test_every_operator_knob_is_actually_read_by_the_loader` reads the real
external env despite ENVFILE=/dev/null, then scans only config.py and the executable.
It rejects three working Make readers: AUTOPOIESIS_RESEARCH_SPEC, PREREGISTRY and
RESEARCH_TRIALS. Each is an actual Make variable reference; the real explicit
research loop already completed and retained its failed candidates. The sibling
config-example gate also treats arbitrary host env contents as evidence of a
consumer, allowing a dead example knob to pass just because someone set it.

**Success:** Ordinary configuration checks are identical with/without a host env,
require actual declared code readers (including Make variable references), reject
comment-only/assignment-only/dead names, and still fail for an unknown declared
configuration knob. No credentials are opened by these checks. Existing actual
research settings remain unchanged and are verified operationally elsewhere.

**Minimum design:** Update the existing test_config_wiring.py and existing
config-example gate in tools/verify.py; no production loader or new abstraction.
Use shipped configs/paper.env.example for the default declared-knob test. Include
actual `$(AUTOPOIESIS_...)` Make references in recognized readers; strip comment
lines, do not count merely documenting or assigning a variable as its consumption.
Remove the gate's external env-file fallback. Clearly labelled synthetic fixtures
test recognized research references, dead/comment-only names, and a fake external
env that must not make an unknown example name valid. Keep tests in existing
registered files and reuse existing gate fixtures. Do not remove the inverse
wiring assertion or hide an ignored setting with a blanket allowlist.

**Verification:** Red synthetic tests first, then focused Make tests/lint, independent
specification and quality review, then full make check and actual fresh clone from
the committed source. Record measured counts and reconcile this plan.

- [x] Synthetic red baseline before consumer-detection edits: final isolated fixture
  run `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_config_wiring.py --tb=short'`
  produced **9 failed / 53 passed / 1 skipped in 0.71s**. The absent-host case
  skipped under the old implementation; both missing Make readers failed, fake
  host declarations incorrectly validated a dead name, and the guards caught
  attempts to open the synthetic host env. Shell assignment/prose names were
  also incorrectly treated as reads.
- [x] Surgical correction and focused Make verification: the same focused command
  produced **63 passed in 0.57s**. The existing operator-reader helper now selects
  actual shell expansions and non-comment Make references; the inverse test
  reuses it instead of keeping a duplicate shell scan. Default declarations come
  only from active assignments in the shipped example. The gate compares exact
  names and never uses the external env as consumer evidence.
  `make classes ENVFILE=/dev/null` confirmed the relevant class names;
  `make verify ENVFILE=/dev/null CLASS='config-example-names config-example-complete'`
  produced **2 passed / 0 failed**, checking **42 active example names** and
  **44 documented loader variables**, across the unchanged **94-file registry**.
  `make lint-files ENVFILE=/dev/null FILES='tools/verify.py tests/autopoiesis/test_config_wiring.py'`
  reported **All checks passed**; `git diff --check` exited 0.
- [x] Independent specification and quality review: initial specification approved;
  quality found false shell consumers, corrected and re-reviewed below.
- [ ] Full check and committed-tree clean-clone proof: coordinator follow-up after
  both reviews; deliberately outside this bounded implementation unit.

The failed command is evidence of a check's host dependence, not evidence that the
three actual research settings are ignored. Do not delete the operational settings
or alter risk limits to make this test green.

## Quality correction target (before edits)

Specification approved 63 tests and both focused gates. Independent quality review
then demonstrated two false consumer proofs in the tightened shell scanner:
`echo ok # ${AUTOPOIESIS_DEAD}` and `echo '$AUTOPOIESIS_DEAD'` are accepted as reads.
Neither shell form expands that variable. The old whole-line comment filter is
insufficient for the new actual-expansion claim.

Minimum correction within the same existing helper: discard inline shell comments,
single-quoted literals and escaped dollar signs while preserving real unquoted and
double-quoted parameter expansions. Use a small lexical selection rather than a
general shell parser or new abstraction. Keep non-comment Make references and
loader behavior unchanged. Reproduce those negative cases plus quoted-positive
and escaped-dollar cases in labelled synthetic fixtures first; then run focused
Make tests/gates/lint and repeat specification followed by quality review. The full
check run on the initial frozen correction cannot certify the final changed tree.

- [x] Quality regression red: before changing the helper,
  `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_config_wiring.py --tb=short'`
  produced **10 failed / 74 passed in 0.71s**. Both checks accepted all five
  negative shell forms: inline comment, single-quoted simple/braced dollar,
  unquoted escaped dollar, and double-quoted escaped dollar.
- [x] Minimum lexical correction in the existing helper: track shell quote/escape
  state and comment boundaries before selecting dollar references. Preserve
  actual unquoted/double-quoted expansions, quoted `#`, and mid-word literal `#`.
  Make selection and loader behavior remain unchanged. The same focused command
  produced **84 passed in 0.66s**.
- [x] Focused gates/lint: `make verify ENVFILE=/dev/null CLASS='config-example-names config-example-complete'`
  produced **2 passed / 0 failed**, with **42 active example names**, **44 documented
  loader variables**, and the unchanged **94-file registry**. `make lint-files
  ENVFILE=/dev/null FILES='tools/verify.py tests/autopoiesis/test_config_wiring.py'`
  reported **All checks passed**; `git diff --check` exited 0.
- [x] Repeat specification then quality review: specification independently reran
  **84 passed in 0.59s** and both gates PASS; subsequent quality independently
  reran **84 passed in 0.58s**, lint and diff checks passed, and approved the
  correction to its original findings. No runtime action by the implementer.
- [ ] Full check and committed-tree clean-clone proof must run on the final reviewed
  tree; the previous full check is historical evidence only.
