# Keep the recorded broker-defect audit bound to the real shared reader

Written planning phase before edits; Superpowers implementation, independent
specification then quality review, and final combined re-review apply.

**Problem / measured baseline:** The final frozen e28a2f1 `make check ENVFILE=/dev/null`
failed: **1 failed / 1,693 passed / 6 skipped / 1 warning in 210.38s**.
The failure is `test_every_recorded_defect_is_still_fixed`: existing defect audit
**61/62 checks passed across 15 defects**. D1 insists that data_gateway.py contain
the literal `list_orders(`, although the approved correction deliberately moves
that API call to broker_client.list_open_orders and every production/audit consumer
now shares it. The functional 211-test unit and independent reviews already prove
the live path; the recorded audit must follow the actual delegation rather than
force a duplicate API call into the gateway. A separate durability thread-warning
is being investigated independently and is not dismissed by this correction.

**Success / minimum design:** Keep all 62 recorded defect properties and their
failure exits. D1 proves supported list_positions plus gateway delegation to the
actual broker_client.list_open_orders that calls pinned SDK list_orders. Do not
accept a method name merely in a comment/docstring, or a dangling delegation with
the helper removed/changed to the old get_orders. Use a small structural check
within the existing audit; do not rewrite the other 61 checks or production code.
Add narrowly meaningful negative fixtures in existing test_governance.py to prove
the D1 guard still fails for a missing or incompatible shared SDK consumer.

**Verification:** The actual full-run failure above is the RED baseline. Run focused
Make governance tests and `make audit ENVFILE=/dev/null` after the correction, then
lint and diff checks, independent specification and quality reviews, final combined
review and a new full check. Commit before the serial actual fresh-clone proof;
publish exact CI only for the accepted final source. No broker/model/order/service
or runtime mutation in this bounded audit implementation.

- [x] Measured full check failure and pre-edit target recorded.
- [x] Correct the existing audit without deleting its safety property.
- [x] Focused Make verification and meaningful negative regressions.
- [x] Independent specification then quality review.
- [ ] Final full/fresh-clone/CI and actual deployment proof.

**Focused implementation evidence (uncommitted review freeze):**

- RED: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_governance.py -k "every_recorded_defect or broken_shared_broker_reader"'`
  produced **14 failed / 11 deselected in 0.76s**: the real-source audit failed
  at **61/62**, while all 13 synthetic source regressions falsely returned audit
  exit 0 under the old substring guard.
- GREEN: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_governance.py'`
  produced **25 passed in 1.17s**. The negative fixtures cover comment/docstring
  mentions at each link, absent/wrong helper imports, wrong client arguments,
  missing helper module/function, and the legacy SDK method. Each fails D1,
  retains **62** audit properties, and returns exit 1.
- `make audit ENVFILE=/dev/null` returned exit 0 and **62/62 checks pass across
  15 defects** against actual repository source.
- `make lint-files ENVFILE=/dev/null FILES='tools/audit_defects.py tests/autopoiesis/test_governance.py'`
  reported **All checks passed!**; `git diff --check` returned exit 0.

The implementation replaces only the stale D1 property with an AST check scoped
to the gateway's broker-fetch methods, its shared-reader import, and the actual
SDK call in that helper. The other 61 properties remain unchanged. Synthetic
sources live only under pytest temporary directories. Independent reviews and
the final combined/full/fresh-clone/CI/deployment proof remain pending; these
focused commands do not claim that those stages ran.

Independent specification review approved with **25 passed in 1.07s**, audit
**62/62**, and lint/diff checks. Subsequent quality review approved with
**25 passed in 1.04s**, audit **62/62**, and selected lint. The AST check proves
scoped structural call presence, not exhaustive reachability or runtime binding;
actual broker behavior remains a separate operational measurement.
