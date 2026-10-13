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

- [ ] Synthetic red baseline before edits.
- [ ] Surgical correction and focused Make verification.
- [ ] Independent specification and quality review.
- [ ] Full check and committed-tree clean-clone proof.

The failed command is evidence of a check's host dependence, not evidence that the
three actual research settings are ignored. Do not delete the operational settings
or alter risk limits to make this test green.
