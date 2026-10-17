# Verify installed research storage without source imports

Problem: unit tests establish registry resolution from source, but packaging checks
prove only imports/example execution. A fresh editable install does not establish
that a noneditable installed research CLI writes to the operator workspace.
Measured baseline: `make release-check ENVFILE=/dev/null` exited 0 and ran zero
installed research registration/verification or workspace-registry assertions.

Success: the same Make target registers and verifies the explicitly example spec
using the built sdist's interpreter in an isolated working directory, without
PYTHONPATH, inherited registry overrides or credentials. Assert registration files
exist under that directory's runtime registry; ensure the imported package comes
from that interpreter's site-packages. No research run or scientific result is
claimed; this is a disposable installation check, not pre-registration evidence.

Minimum design: extend the existing release-check recipe, reusing built sdist and
throwaway virtualenv. No dependencies or new production API. Verify by executing
make release-check, then independent spec/quality review. Record exact output.

- [x] Add the installed CLI check through Make and execute it.
- [x] Independent spec/quality reviews and actual release-check reconciliation below.

Implementation: the existing sdist virtualenv now creates a fresh
`runtime/release-check/workspace` directory and changes into it. One failure-propagating
recipe invokes the installed Python with `env -i`, an explicitly nonexistent env
file and an isolated config path. It asserts absent PYTHONPATH/registry overrides,
checks both imported package modules against that interpreter's site-packages,
executes installed register/verify CLI subprocesses, and checks both hash-named
registration files under the working-directory runtime registry. No production
code, dependency, real preregistration or research execution changed.

Execution on 2026-10-13:

`make release-check ENVFILE=/dev/null` exited 0, built
`autopoiesis-0.1.0a1.tar.gz` and `autopoiesis-0.1.0a1-py3-none-any.whl`, and reported:

```text
import from the installed sdist: ok
examples/minimal_cycle.py from the sdist: ok
configs/paper.env.example from the sdist: ok
examples/ from the sdist: ok
registered 795261baf57d12de... from /localscratch/liuyime2/QuantGroup/.worktrees/rsi-audit-repairs/runtime/release-src/autopoiesis-0.1.0a1/configs/research.spec.example
  hypothesis: the existing threshold grid yields positive mean fold return after costs
  instrument: SPY over 2026-04-10T13:30:00Z .. 2026-10-09T19:40:00Z
  family:     threshold-grid-integrity-diagnostic
verify a run against it with: research/prereg.py verify <spec>
verify: the spec is byte-identical to the one registered before the run
installed example register/verify in the working-directory registry: ok
release-check: the sdist installs and runs what the README claims it runs
```

The printed example hypothesis is a declaration in the distributed example, not
an evaluated result. Packaging emitted existing setuptools license deprecation
warnings; they did not prevent installation or execution.

`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_research_contract.py tests/autopoiesis/test_research_prereg.py'`
exited 0: `63 passed in 0.99s`. `git diff --check` exited 0.

Independent specification and quality review remain pending; implementation and
measured execution are complete, without a commit by the implementation agent.

Independent spec review approved imported-module provenance and existing generated
artifacts. Independent quality review actually reran `make release-check
ENVFILE=/dev/null` on Python 3.10.18: exit 0; raw log retained privately at
`/tmp/autopoiesis-audit-installed-quality.log`, with installed example register,
verify, artifact assertions and final success. Failure propagation, shell quoting,
package provenance, empty environment and isolation were approved. Both reviews
complete; no operator registration or scientific experiment occurred.
