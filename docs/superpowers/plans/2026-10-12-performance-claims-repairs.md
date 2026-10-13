# Performance claim repair

Problem: the benchmark divides agent PnL by the sum of independent strategy peak
exposures, then compares it with whole-record SPY. Asynchronous peaks do not define
agent allocation/equity, and neither leg establishes an aligned portfolio return.
Unknown unrealized PnL becomes zero, valid zero market return becomes missing,
configured cost/owner fills are omitted, and a short record can produce PASS.

Measured baseline: committed published figures quote capital 45,233.90, return
1.81%, excess -0.88 points over only 19 recorded days using that unsupported
denominator. These are old published claims, not new runtime results. Reproduce
synthetic structured-PnL and short-window refusal regressions before source edits.

Acceptance: preserve verified raw agent realized/unrealized PnL, observed and
assumed costs and owner segregation; unknown valuation stays unknown; zero market
move remains zero as contextual data. Agent allocation/cash-flow-adjusted equity
and an aligned rolling 60-day benchmark are absent, so agent return/excess/target
status must be UNKNOWN, never PASS/FAIL inferred from a peak-exposure ratio. Public
and operator pages must reject legacy unversioned portfolio claims and render the
same UNKNOWN status without crashing on nulls. Collector success must be distinct
from measurement success, and published aggregate evidence must match execution.

Minimum design: a versioned accounting-only payload with explicit unavailable
comparison reason; reuse evaluator's structured net-after-cost calculation with
configured cost and owner-fill history. Keep per-strategy peak-exposure ratios
labelled diagnostics, remove their status as a project success metric. Update
existing consumers instead of introducing an invented equity curve. New valid
allocation/equity tracking is a follow-up experiment contract, not guessed data.

Verify: red software fixtures, benchmark/public/status tests, scoped lint; a real
read-only Make benchmark under final source and rebuilt public pages containing
only aggregate evidence. Synthetic fixtures establish arithmetic contracts only.

Red baselines (Make, ENVFILE=/dev/null): benchmark file **4 failed, 3 passed in
0.73s** after correcting the synthetic fixture constructor; public legacy/null
checks **2 failed, 5 deselected in 0.13s**; operator legacy/null checks **2 failed,
12 deselected in 0.16s** after supplying all required synthetic Snapshot fields.
Failures reproduced an unsupported PASS, suppressed zero market return,
publication of a legacy peak ratio, `None pts`, and null formatting crashes.
After accounting/refusal and consumer repairs, the three files returned
**28 passed in 1.01s**. Scoped lint found one unused variable, removed immediately;
lint then passed. Independent review and final real/publication evidence pending.

Additional publication regression: the empty JSON payload exposed the configured
absolute journal path. Red `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/
test_benchmark.py -k empty_json'` -> **1 failed, 7 deselected in 0.53s**. Success:
JSON aggregates omit that path (operator prose may identify its input), and empty
measurement still reports explicit UNKNOWN. Remove the field at its producer so
both public benchmark and record collectors inherit the correction.

Spec review found account-only evidence being rendered as measured agent zero and
one remaining sum-of-peaks/cap utilization claim. Root reproduced **2 failed, 2
passed, 20 deselected in 0.40s**. Success: absent agent-linked fills produce no
measured accounting payload; historical independent maxima are labelled diagnostic
without a simultaneous-utilization percentage. Fix these producer/render boundaries
and rerun all three consumer test files before re-review.

Quality review reproduced **4 failed, 1 passed, 16 deselected in 0.19s**:
the JSON collector treated ordinary Python exit-1 failures as successful figures,
and a versioned accounting-only payload could still publish a stale positive
return in two public-page locations. Require matching JSON measurement status,
exit code and boolean outcome before publishing, and render UNKNOWN at every
portfolio-return boundary. Remove unsupported current research assertions from
the operator snapshot and old denominator/control wording.

Real read-only `make benchmark-json` with the external operator configuration and
actual journal completed with measurement exit 2 / UNKNOWN: 2,326 cycles across
20 distinct recorded dates, realized 757.95, unrealized 89.59, assumed cost
35.8189, net 811.72 (rounded presentation). Observed fees 0 means no fees observed,
not proof of zero real trading costs. Allocation, agent return, aligned SPY and
excess are null. This is paper accounting, not profitability/alpha/RSI evidence.
Public figures still require final refresh under the accepted source.

Independent quality re-review approved the fixes: **36 passed in 1.55s**, scoped
lint and diff checks clean. Final publication refresh remains a Task 6 requirement.

Public refresh review measured two unsupported current assertions in the overview:
that scheduled research is operating autonomously, and that all 129 legacy signal
hypotheses establish a family-wise-tested volatility result. The published
aggregate has counts/verdict names only, and the actual new research diagnostic is
0 PASS with a square-root sample heuristic, not FWER control. Success: render
research as a contract-gated capability whose runtime requires verification, and
legacy ledger verdicts as historical counts without alpha/statistical certification.
Minimum change: replace these two prose blocks, retain actual counts and UNKNOWN
portfolio results; run existing project-site tests and regenerate through Make.

Final actual public refresh ran all five collectors successfully against the sealed
real restored journal and a hash-identical private copy of the main legacy signal
ledger (sha256 b5ca9c7ae6cc139351478f1c7a650ee64d46a19970e7255ada132e121885f762).
Published figures show 2,326 cycles / 20 distinct dates, net paper accounting
811.72, all portfolio ratios null, comparison UNKNOWN. Legacy signal ledger is
173 verdict rows / 129 hypotheses; it is separate from the threshold research
trial ledger and does not confer eligibility under the new contract. Gate collector
lists 55 classes, test collector counts 1,490 collected tests / 94 files; neither
collector is proof that those checks passed. Actual execution is recorded separately.

Publication review also found the historical verdict paragraph and build denominator
prose overstated validation; both now state historical counts/accounting/UNKNOWN.
First root invocation used nonexistent test_project_site.py and ran zero tests;
corrected `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_public_site.py'`
returned **8 passed in 0.19s**; `make site ENVFILE=/dev/null` regenerated all pages,
and scoped lint passed. Independent publication re-review approved source/pages,
nulls/accounting consistency and absence of private paths/credentials. Refresh
completed without publishing raw journal, fills, account positions or credentials.
# Publication follow-up: configuration is not historical compliance

Acceptance reconciliation: independent specification review passed **8 public-site
tests in 0.21s**; independent quality review passed **8 in 0.23s** and closed the
risk-history blocker. Both subsequently inspected the final fee/UNKNOWN wording
and generated safety page, approved with no blocker. No historical risk evidence
or fee data was fabricated to justify the corrected prose. Final committed-tree CI
will also run the complete gate; its actual result is checked before delivery.

Adjacent measured wording correction from quality review: one safety-page sentence
asserts broker fees are zero on paper. The record only establishes observed fees=0.
Success is wording limited to no broker fees observed in this paper record, with
assumed friction still separate; change that sentence in the same renderer and
rerun the existing public-site tests/Make generation and focused lint. This does
not change fee data, risk settings or an execution/accounting path.

Problem measured in the final independent review: **one evidence-page heading**
asserts that the historical results were produced inside the displayed risk budget.
The producer reads current AgentConfig; refreshing the same sealed historical journal
under isolated defaults changed position/exposure/trade limits from
25,000/90,000/100 to 5,000/20,000/10. Neither set establishes past compliance.

Success: label the displayed values as configuration used for this measurement,
explicitly state that historical risk configuration/compliance is not established,
and retain the actual isolated-default measurements rather than invent historical
limits. Minimum change: evidence-page heading and one explanatory paragraph in the
existing renderer. No new production layer, data field, tests mirroring prose, risk
change or alteration of real numbers. Run existing public-site tests and focused
lint, regenerate via Make, then independent review of the rendered page and final
gate/CI. This is a documentation/rendering correction; earlier source measurements
retain their actual SHA and counts.

Publication wording follow-up completed and frozen for independent review:
only the evidence renderer's heading and explanatory paragraph changed. It now
labels risk values as configuration used for this measurement and explicitly
states that historical risk configuration/compliance are not established. Data
fields, actual limits, accounting values, and Guardian rules remain unchanged.

`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_public_site.py'`
returned **8 passed in 0.25s**. `make lint-files ENVFILE=/dev/null FILES='tools/project_site.py'`
returned **All checks passed**. `make site ENVFILE=/dev/null` successfully regenerated
the five public pages from the existing figures; no collector ran and no figures
were changed by this follow-up. Render inspection found the new heading at
`docs/site/evidence.html:101` and explicit historical-compliance qualification at
line 108. `git diff --check -- tools/project_site.py docs/site/evidence.html`
returned no findings. No new tests mirror the prose. Independent publication
review and final-source gate/CI remain pending for this distinct wording change;
earlier source evidence retains its actual SHA.

Adjacent safety wording corrections are now frozen: the page states **No broker
fees were observed in this paper record**, retains the explicitly assumed
round-trip-cost explanation, and says **The target remains unproven** to match
UNKNOWN rather than imply measured failure. No fee data, accounting, risk,
execution path, or new tests changed.

After the fee correction, the existing public-site tests returned **8 passed in
0.21s**. After the final unproven-target wording correction,
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_public_site.py'`
returned **8 passed in 0.23s**; `make lint-files ENVFILE=/dev/null FILES='tools/project_site.py'`
returned **All checks passed**; `make site ENVFILE=/dev/null` successfully
regenerated all five pages. Render inspection confirms both qualified claims at
`docs/site/safety.html:84`. `git diff --check -- tools/project_site.py docs/site/safety.html docs/site/evidence.html`
returned no findings. The separate full-gate run started at the prior risk-wording
freeze; these later safety-prose changes have their own focused checks and are
not represented as same-source-SHA full-gate evidence. Independent review and
exact committed-source CI publication evidence remain root integration work.
