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
