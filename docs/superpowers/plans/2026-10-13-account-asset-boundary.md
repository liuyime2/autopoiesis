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
- [ ] Reproduce failures with clearly labelled synthetic fixtures.
- [ ] Make bounded correction and run focused regression checks.
- [ ] Independent specification then quality approval.
- [ ] Run real read-only account inventory and reconcile actual capabilities.
- [ ] Final full checks, fresh clone, exact CI and deployment evidence.

Other audit findings remain explicit follow-up rather than silently removed:
whole-account COVER currently derives agent-only short lots and shorts is configured
shadow; curriculum's open-book view can retain old longs after a later flat book.
Neither is evidence that options, crypto or all short positions are controllable.
Resolve measured defects in subsequent bounded units, preserving the hard controls.
