# Verify each unit template through its actual renderer

Written planning phase before edits. Superpowers bounded implementation, independent
specification then quality review and final combined re-review are required.

**Problem / measured baseline:** Actual committed-tree c2fa05d fresh clone into a
new Python 3.10 venv, credentials removed, completed **8 PASS / 1 FAIL**. Its final
integration gate reported **54/55 classes, 2,816 test executions across 94 files**.
`unit-templates-portable` reported five problems: PYTHONDIR/FINGERPRINT in both
market readiness services and AFTER in open verification. It incorrectly requires
all markers to appear in the shell autopoiesis renderer, although the reviewed
dedicated install_market_readiness.py owns and renders four units. Its glob also
checks only **12/14** templates, excluding both new `.timer.in` files. Independent
read-only diagnosis confirmed the exact ownership and discovery gaps. The prior
clone is a failed baseline, not final acceptance; unit 10 is also pending.

**Success / minimum design:** Keep the same gate and 55 classes. Discover service
templates, timers and timer templates. Identify each renderer from actual legacy
shell render/copy commands or readiness installer's explicit UNIT_NAMES; fail
unclaimed templates. Keep host-path checks on every template. Reuse the existing
readiness install/render operation with private temporary paths and an injected
capturing runner that executes no service command. Compare all staged units to
exact expected substitutions derived independently from synthetic declared inputs;
prove unknown/raw markers, missing/wrong/empty values or disabled replacement fail.
Do not exempt readiness templates, join renderer source strings to make names pass,
or infer substitution from comments or the presence of dictionary keys alone.
Preserve legacy template checks; do not add a new production renderer framework.

**Verification:** Synthetic fixtures in existing classified `test_fresh_clone.py`
exercise actual gate results and renderer failure modes: unknown marker, missing
key, wrong/empty value, disabled re.sub, malformed/raw @, missing UNIT_NAMES claim,
and a hardcoded host path in timer.in. Require valid templates to pass, all fourteen
covered, and negative fixtures to fail. Run focused Make tests and lint/diff checks,
then independent two-stage reviews. Root owns serial full/fresh-clone/gate proof,
exact CI and actual installation. No broker/model/runtime/service/network calls
or whole-gate runs by implementation/review agents. Isolate synthetic paths from
the actual unit manager and external credentials.

- [x] Record actual failed fresh clone and independently confirmed 5/14 baseline.
- [x] Correct existing discovery/ownership/rendering check with regression fixtures.
- [x] Focused verification: 126 tests pass; selected lint and owned diff checks pass.
- [x] Independent specification and quality reviews approved.
- [ ] Final combined/full/new fresh-clone/exact CI and paper deployment.

Unit 11 implementation evidence: first focused regression run failed all 13 cases
against the old ownership gate, including the valid 14-template fixture. An added
echo-only shell fixture then failed against the first repair; requiring actual sed
expressions inside render functions made it pass. Final command:
`make test TEST_ARGS='tests/autopoiesis/test_fresh_clone.py tests/autopoiesis/test_governance.py tests/autopoiesis/test_config_wiring.py' ENVFILE=/dev/null AUTOPOIESIS_ENV_FILE=/dev/null`
reported **126 passed in 2.72s**. Synthetic renderer inputs and capturing runner
exercise the existing installer; no service command is executed. No class map,
gate class count, production installer, or templates changed. Root's existing
failed fresh-clone log remains untouched; its pre-existing trailing whitespace is
outside this unit's owned diff.

Root acceptance: SPEC independently passed 126 tests in 2.56s and QUALITY 126 in 2.73s, with selected lint/scoped diff clean. Both approved this bounded unit. Separate pre-existing follow-up: shell markers are pooled across both render functions, masking missing Ollama substitutions in install-transient. Actual deployment uses the persistent installer; no transient installation success is claimed. A separately measured plan is required before modifying that path.
