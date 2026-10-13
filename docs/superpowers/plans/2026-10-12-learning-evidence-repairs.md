# Learning evidence repair plan

Independent specification review found an order-alias refusal gap. A clean BUY
and an unknown retry shared a client ID; the broker ID survived and certified
learning PnL. Root reproduced **1 failed, 1 passed, 19 deselected in 0.36s**.
Require every supplied lot alias and its origin aliases to be untainted and
consistent; conflicting clean origins also invalidate an identity permanently
within the evaluation. Raw FIFO accounting remains unchanged. Focused evaluator,
fill and eligibility regressions: **56 passed, 1 explicit real-audit skip in
1.73s**. Type checking: 49 source files clean. Re-review pending.

Re-review found a later broker-only clean retry could erase an earlier client-ID
relationship. Exact partial-retry regression returned **1 failed, 2 passed,
19 deselected in 0.37s**. Preserve the graph of every observed alias relationship
and propagate exclusions over connected identities before certifying any lot.

Quality review then reproduced permanent suppression of fresh behavior PnL:
old behavior earns 10, a clean new behavior earns 20, raw accounting retains 30
but the new behavior gets no learning PnL. Root regression **1 failed, 22
deselected in 0.42s**. Partition learning lots by their proven current origins,
aggregate only those lots and their fees, preserve every raw lot and the permanent
alias taint graph. Old evidence must neither boost nor forever block new learning.

Quality review also measured withheld old ACCEPTED lessons preventing clean new
independent derivations with identical content. Root red **1 failed, 26 deselected
in 0.40s**. Only usable verified lessons block content duplicates; new citations
are verified before admission, and superseded history is retained as RETIRED,
without backfilling the old source digest.

The final quality finding concerns a clean SUBMISSION_UNKNOWN record with only a
client ID, later confirmed by broker with an assigned broker ID. Root red **1
failed, 1 passed, 27 deselected in 0.36s**. Link confirmed activity aliases only
through an existing journal origin, check strategy/symbol/side consistency and
propagate all unknown/conflicting aliases before certifying learning. The paired
tainted retry remains excluded; raw accounting stays 20 in both fixtures.

The same broker-confirmation regression expanded to order-derived fills returned
**1 failed, 3 passed in 0.39s**. Reuse the canonical `_fill_candidates` input for
alias association too, so activities, filled orders and confirmed journal seeds
follow one contract. Root evaluator/fill/learning checks **66 passed, 1 explicit
real-audit skip in 1.68s**; lint and type (49 sources) clean. Final independent
quality review approved all four formerly failing repros plus **83 passed,
1 skip in 5.64s**. The unrelated cooperative-stop tests remain deliberately red
until their separate implementation; do not count them as Task 3 regressions.

Problem: strategy learning currently treats journal strategy labels, cached scores,
and broker fill labels as proof of decision attribution. The current mutable
library cannot establish the universe or behavior selected for a historical cycle.
The prior real audit measured 862 symbol mismatches among 1602 attributed cycles;
no immutable decision-time universe was recorded. Those observations do not prove
any historical decision clean. Preserve all journal records and raw accounting.

Acceptance: unknown or inconsistent attribution contributes zero calibration,
offline decisions, lesson effects, strategy scores, performance lifecycle evidence,
or champion claims. Future decisions capture the actual selected immutable behavior
and universe, including model overrides and fallback. Quotes remain available for
counterfactual horizons regardless of decision eligibility. Real accounting,
holdings, trade caps and cumulative service/attempt budgets retain all records.
Broker-verified strategy learning PnL additionally requires clean originating
orders throughout the full journal, including both sides of every contributing lot.
Legacy caches and accepted journal lessons fail closed; newer inconclusive screens
supersede old passes. No current universe is backfilled into legacy records.

Estimator correction: distinct timestamps with the same observed price are valid
zero-return observations. Inherited price-equality compression can skip a genuine
24-hour horizon, producing PENDING/GAP or selecting a later move. Establish a red
flat-price horizon regression, preserve timestamped real quotes, and replace the
old test that incorrectly calls every repeated price a non-observation.

Minimal design: one shared provenance/eligibility module; immutable canonical
behavior JSON plus SHA-256, id and universe captured by StrategyExecutor and Hybrid
from the already selected spec. Reuse that gate at existing consumers. Cache
version, source cycle ids and frozen behavior accompany derived metrics. Resolve
lesson citations against actual cycles at admission/maintenance and stamp only
verified admissions. Keep raw ledger and cumulative budget counters separate from
learning evidence. No new dependencies, runtime writes or broker operations.

Verification sequence (all synthetic regression fixtures explicitly identified):
- [x] Establish fresh failing consumer regressions via `make test ENVFILE=/dev/null`.
- [x] Capture selected metadata; gate calibration, counterfactual/offline and lessons.
- [x] Gate strategy metrics/PnL, reflection caches and lifecycle consumers; preserve budgets.
- [x] Verify historical lesson admission, cache refresh and production consumer wiring.
- [x] Run focused tests, lint/type checks and real read-only gate audit (commands below).
- [x] Reconcile README counts and run broad regressions excluding concurrent Task 4 tests.
- [x] Parent integration/fresh-clone verification and independent reviews completed in Task 6.

The parent execution plan records that planning-with-files is unavailable; this
separate written plan supplies the documentation gate. Task 2 source is frozen
under review; initial work is read-only inspection and independent red regressions.

## Executed evidence and implementation

Fresh initial red command: `make test ENVFILE=/dev/null
TEST_ARGS='tests/autopoiesis/test_learning_evidence.py'` produced **4 failed in
0.30s** after correcting a fixture setup error. The failures were legacy/mismatched
calibration, legacy offline rows, learning scores from unknown cycles, and the
whole-window reflection fallback. The initial four regressions then passed.

Capture uses immutable canonical behavior JSON/id/universe/SHA-256 from the actual
selected StrategySpec. Hybrid overrides model-supplied ids/metadata, reads the
library once including its reflection path, captures before calling the model,
and preserves that selection through fallback. Lifecycle and probation restarts
are excluded from the behavior digest. Consumers compare current behavior only;
the current library never supplies an unknown historical universe.

Learning metrics select the latest actually recorded clean behavior per id; older
behavior cannot supply its cycles, actions, scores or PnL. Raw cumulative service
and attempts retain all history, including legacy cache budget counters, so a
behavior update or cache rejection does not grant unlimited service. Raw broker
PnL/holdings remain unchanged; every lot contributing learning PnL requires clean
full-journal originating orders for both sides. `_reflect` reads the full journal
once, derives its recent window and budgets, and reuses that read for fill origins.

Counterfactual rows carry verifiable attribution fields, and unchanged verdicts
refresh when eligibility metadata changes. Latest invalid/changed-strategy rows
invalidate older clean rows. Offline caches require the current gate version,
frozen behavior and actual source ids; a newer inconclusive screen supersedes an
old pass. Reflection caches require equivalent metadata, with no score fallback.
Accepted journal lessons require citations resolved against clean actual cycles
and an admission-created gate stamp. Their source digest hashes the complete cited
CycleRecord, so a changed action/confidence/price invalidates the lesson cache.
Verification happens during maintenance/reflection, not by scanning the journal
for every trading decision. Historical unstamped accepted lessons are withheld.

Additional freshly reproduced estimator defect: `make test ENVFILE=/dev/null
TEST_ARGS='tests/autopoiesis/test_learning_evidence.py -k equal_price'` produced
**1 failed, 14 deselected in 0.34s**: the valid unchanged-price quote at 24 hours
was skipped and the decision remained PENDING. Equality compression was removed;
timestamped equal prices now produce actual zero gross return and recorded cost.
Focused counterfactual/daemon/evidence verification then passed **87 tests in 0.95s**.

Full-suite first integration: `make test ENVFILE=/dev/null TEST_ARGS=''` produced
**34 failed, 1372 passed, 5 skipped in 131.85s**. Thirty-three failures were valid
synthetic fixtures lacking explicit provenance/admission, subsequently migrated;
one was the README module/test counts after new files. README now records measured
47 modules, 25 models importers and 92 test files. No production permissive
default was introduced to accommodate old fixtures.

Final focused command: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_learning_evidence.py tests/autopoiesis/test_calibration.py tests/autopoiesis/test_counterfactual.py tests/autopoiesis/test_offline_validation.py tests/autopoiesis/test_evaluator.py tests/autopoiesis/test_reflection_memory.py tests/autopoiesis/test_strategy_engine.py tests/autopoiesis/test_daemon.py tests/autopoiesis/test_llm_provenance.py tests/autopoiesis/test_policy_engine.py tests/autopoiesis/test_knowledge_lessons.py tests/autopoiesis/test_knowledge_admission.py tests/autopoiesis/test_knowledge_library.py tests/autopoiesis/test_knowledge.py tests/autopoiesis/test_curriculum.py tests/autopoiesis/test_curriculum_feedback.py tests/autopoiesis/test_doctor.py tests/autopoiesis/test_doctor_chain_checks.py tests/autopoiesis/test_doctor_proof_checks.py tests/autopoiesis/test_durability.py tests/autopoiesis/test_fill_reconciler.py tests/autopoiesis/test_score_latch.py'` produced **519 passed,
2 skipped in 6.41s**. The opt-in real audit and an existing host integration check
are the explicit skips. `make lint ENVFILE=/dev/null` passed; `make type
ENVFILE=/dev/null` reported **no issues in 49 source files**; `git diff --check` passed.

Broad regression command: `make test ENVFILE=/dev/null TEST_ARGS='--ignore=tests/autopoiesis/test_benchmark.py --ignore=tests/autopoiesis/test_public_site.py --ignore=tests/autopoiesis/test_status_page.py -k "not test_the_readme_counts_match_the_tree"'`
produced **1390 passed, 6 skipped, 1 deselected in 29.91s**. The three excluded
files were concurrently owned by Task 4. After the README reconciliation,
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_governance.py::test_the_readme_counts_match_the_tree'`
produced **1 passed in 0.04s**.

Read-only real command:
`AUTOPOIESIS_LEARNING_AUDIT_JOURNAL=/localscratch/liuyime2/QuantGroup/runtime/autopoiesis/journal.jsonl make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_learning_evidence.py -k real_journal -s'`
produced **1 passed, 18 deselected in 1.94s**. Stable before/after file size/mtime
was checked; lock=False prevented creation of locks/manifests or runtime writes.
Observed four history files, **218676065 bytes**, SHA-256
`73a65488c524af75df890c73741c7593b2759b4372c3e376d48269bccd4e8254`, **2326 parsed
cycles**, **1602 attributed cycles**, **2326 UNKNOWN_PROVENANCE**, **0 learning
strategy buckets**, and **1602 preserved raw service cycles**. Decision-vs-snapshot
symbol mismatches were 0; the prior 862/1602 finding concerned strategy-universe
attribution, which cannot be certified retrospectively without frozen universes.
This establishes eligibility exclusions, not profitability or future live behavior.
No broker calls, lifecycle changes, daemon restart or credentials were used.
