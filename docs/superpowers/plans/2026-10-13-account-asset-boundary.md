# Audit the whole account and keep unsupported assets outside the equity trader

Written planning phase before implementation. Use Superpowers implementation,
then independent specification and quality review. The user explicitly confirmed
that the scope audit must include options and crypto, beyond US equities and ETFs.

## Problem and measured baseline

The real October 13 broker smoke observed six positions and zero open orders,
but prints neither instrument classes nor options permission levels. The production
gateway has one `list_orders(status="open")` consumer with no explicit limit or
full-page refusal. Readiness already requests 500 and refuses a full page, so the
trader and its readiness audit have different completeness guarantees. The equity
universe is explicitly `asset_class="us_equity"`; held/allowlisted symbols bypass
that universe check in Guardian. The real gateway currently requests stock quotes
without first proving that the requested instrument is a US equity. Its model
orders use integer quantity, market/day and quantity times stock price, which is
not an options contract-risk calculation or a crypto execution contract.

Read-only focused baseline: `make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_manage_account.py tests/autopoiesis/test_universe.py tests/autopoiesis/test_guardian_shorts.py tests/autopoiesis/test_data_gateway.py tests/autopoiesis/test_instrument_profile.py tests/autopoiesis/test_scheduler.py'`
produced **68 passed in 1.64s**. Those tests do not certify multi-asset execution.
There are **zero implemented options/crypto-specific trading adapters**. Frozen
selectable strategies currently cover SPY/BIL/TLT, not every configured or held
instrument. Missing coverage must remain an explicit safe abstention.

Additional measured code baseline before this correction: the gateway's one
`_required_float` converter accepts NaN/infinity; position quantity/market value
and cash/buying power have no finite-value constraint. Non-finite broker numbers
can defeat comparison-based risk checks. Reproduce this with synthetic fixtures
before changing that converter, rather than rely on the broker normally behaving.

## Success and minimum design

1. Production gateway requests at most 500 open orders and refuses a full page,
   an unavailable list or invalid contents. A missing order must never weaken
   conflicting-order or aggregate-risk checks. The same missing explicit limit is
   present in startup reconciliation and doctor, while readiness has a duplicate
   bounded reader. Merge these open-order reads into a small existing
   broker_client.py helper and reuse it in gateway, reconciliation, doctor,
   readiness and smoke. Use only the pinned SDK's list_orders, not a new legacy
   compatibility path. Historical broker evidence also requests status=all with
   the SDK default limit: request 500 there and treat a full page as incomplete
   evidence, preserving its existing PARTIAL/FAILED result handling. Do not label
   a full result page as a complete account or complete historical evidence.
2. Before stock quotation/decisions, the real gateway uses the broker's existing
   `get_asset(symbol)` metadata and requires explicit `us_equity`. Missing, unknown,
   options or crypto metadata fail closed before stock quotes or a model/order.
   Do not infer class from spelling or turn an unsupported holding into a stock.
   Do not change historical/replay schema or weaken any Guardian limit. Keep
   unsupported positions visible in the account-wide inventory and exposure.
   Require finite real broker account/position/order/quote numbers using the
   existing conversion boundary; malformed values fail closed. Do not rewrite
   historical models or coerce malformed data to zero.
3. Extend the existing `make smoke` read-only entry, rather than add another
   trading loop or credential mechanism: account cash/equity/buying power and
   blocked flags; options approved/trading levels with UNKNOWN when absent;
   every held position's broker-reported class and metadata; complete open orders;
   explicit current implementation support for equity vs unsupported classes.
   Account reads happen independently of per-instrument quotes. No model, submit,
   cancel, exercise, account-settings write, or credential output. Financial
   output stays in private runtime evidence. Use positive flags only for observed
   permissions, never infer all asset support from account ACTIVE.
4. Update scope documentation/todo: the broker offers options and crypto, while
   this project cannot yet claim their end-to-end control. Record the needed
   separate contract/data/risk/calendar/fill-accounting adapters and paper audit;
   do not enable them to manufacture a readiness pass.

## Verification

Labelled synthetic red tests must expose truncated-order acceptance, stock quote
calls for unsupported/unknown assets, and incomplete smoke inventory. Then the
same tests must pass with no broker/model mutating call available. Run relevant
Make tests, lint/type checks and regression tests immediately; specification then
quality review; final full check and committed-tree fresh clone. Run actual
`make smoke` only after the source freezes and keep the real asset/permission
results distinct from synthetic tests. Preserve user's main prereg.py change.

- [x] Read-only baseline and written acceptance before code edits.
- [x] Reproduce failures with clearly labelled synthetic fixtures (commands below).
- [x] Make bounded correction and run focused regression checks (commands below).
- [x] Independent specification then quality approval: specification independently
  ran **211 passed in 7.40s**, lint/diff clean and approved; subsequent quality
  independently ran **211 passed in 7.60s** plus **125 passed in 2.98s**,
  lint/diff clean and approved. Latest-book work was excluded from these reviews.
- [x] Run real read-only account inventory and reconcile actual capabilities:
  `make smoke` exited 0, actual broker clock **2026-10-13 03:48:21 EDT**;
  six long integer us_equity positions, zero complete open orders;
  crypto_status ACTIVE, options approved/trading levels 3, three blocked flags
  false. Ten held/configured/allowlist metadata records all us_equity/active/
  tradable. Private output 0600; no model/quote/submit/cancel/exercise. Detailed
  scope and uncovered XLB/XLE/XLF remain in the account-scope evidence document.
- [ ] Final full checks, fresh clone, exact CI and deployment evidence.

Other audit findings remain explicit follow-up rather than silently removed:
whole-account COVER currently derives agent-only short lots and shorts is configured
shadow; curriculum's open-book view can retain old longs after a later flat book.
Neither is evidence that options, crypto or all short positions are controllable.
Resolve measured defects in subsequent bounded units, preserving the hard controls.

## Local implementation evidence (synthetic, no broker or model execution)

The first RED command was
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_data_gateway.py tests/autopoiesis/test_broker_evidence.py tests/autopoiesis/test_broker_client.py -k synthetic'`:
**17 failed, 25 deselected in 0.72s**. Gateway fixtures containing 51/499 orders
returned only 50; 500 did not refuse; unsupported/missing class and metadata outage
were not checked before stock quotation; historical orders requested default 50.
The initial smoke fixture also stopped at a missing optional position field before
printing asset/permission inventory. Its fields were subsequently completed, and
the read-only broker exposes no quote, model, order, exercise or account-write method.
After the shared reader/class/inventory correction, the same selection yielded
**17 passed, 25 deselected in 0.41s**.

Before editing the numeric converter,
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_data_gateway.py -k nonfinite'`
yielded **24 failed, 20 deselected in 0.86s** for NaN/+infinity/-infinity in
cash, buying power, equity, portfolio value, last equity, position quantity/value
and trade price. The existing converter now refuses non-finite input, reused by
account and quote reads. That selection then yielded **24 passed, 20 deselected in
0.36s**. Order quantity has the same finite check in the shared raw reader;
additional fixtures cover its three non-finite values and preserve unsupported
holdings in whole-account exposure.

The implementation consolidates `list_orders(status="open", limit=500)` into
`broker_client.list_open_orders` for gateway, startup reconciliation, doctor,
readiness and smoke. Missing lists, malformed rows and full pages fail closed.
Historical `status="all"` uses 500 and a full page enters existing PARTIAL/FAILED
handling. There is no `get_orders` compatibility fallback. Gateway `get_asset`
must explicitly report `us_equity` before a stock quote. Smoke observes held and
configured symbols (including allowlist) independently of quotes and reports
unavailable metadata/options levels as UNKNOWN. It also observes `crypto_status`,
`non_marginable_buying_power` and `options_buying_power`; missing fields stay
UNKNOWN. Makefile smoke help/comments and tools documentation describe the
quote-independent inventory. Implementation support labels do
not certify Guardian approval, strategy coverage or profitability.

Focused regression command:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_broker_client.py tests/autopoiesis/test_broker_evidence.py tests/autopoiesis/test_data_gateway.py tests/autopoiesis/test_order_reconciler.py tests/autopoiesis/test_doctor_infra_checks.py tests/autopoiesis/test_cli.py'`.
Before the final four additional assertions, this yielded **207 passed in 8.04s**.
Those assertions yielded **211 passed in 8.00s**. The account permission-field
and smoke help update then yielded **1 failed, 210 passed in 7.57s**: the existing
operator-help assertion requires the phrase `read-only broker`. The new help
description had split that phrase. The help wording was corrected while retaining
the whole-account inventory description; its final focused result is appended below.

Final focused result after that documentation correction: **211 passed in 7.44s**.
Final `make lint-files` result: **All checks passed**. `git diff --check` passed;
`rg --files tests/autopoiesis -g 'test_*.py' | wc -l` returned **94**. The production
source is frozen for the independent specification review, then quality review.

Existing safety/account/profile/doctor/fill regressions:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_manage_account.py tests/autopoiesis/test_universe.py tests/autopoiesis/test_guardian_shorts.py tests/autopoiesis/test_guardian_trade_freshness.py tests/autopoiesis/test_instrument_profile.py tests/autopoiesis/test_scheduler.py tests/autopoiesis/test_doctor.py tests/autopoiesis/test_doctor_chain_checks.py tests/autopoiesis/test_doctor_proof_checks.py tests/autopoiesis/test_fill_reconciler.py tests/autopoiesis/test_universe_wiring.py'`
yielded **125 passed in 2.95s**.

`make lint-files ENVFILE=/dev/null FILES='src/autopoiesis/broker_client.py src/autopoiesis/broker_evidence.py src/autopoiesis/data_gateway.py src/autopoiesis/order_reconciler.py src/autopoiesis/doctor.py tools/alpaca_smoke.py tools/market_readiness.py tests/autopoiesis/test_broker_client.py tests/autopoiesis/test_broker_evidence.py tests/autopoiesis/test_data_gateway.py tests/autopoiesis/test_order_reconciler.py tests/autopoiesis/test_doctor_infra_checks.py tests/autopoiesis/test_cli.py'`
initially found one useless test lambda; `make type ENVFILE=/dev/null` initially
found one missing `None` narrowing before quantity conversion. Both were corrected;
lint then reported **All checks passed**, and type checking reported **no issues
in 49 source files**. No test file was added: the registry remains 94 files.
No real broker/model/order/service/runtime operation, commit, full check or fresh
clone was run in this implementation unit. Coordinator subsequently completed
specification and quality approval and the private real inventory recorded above.
Final full check, committed-tree clean clone, exact CI and deployment remain pending.
