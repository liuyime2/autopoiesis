# v6 forward paper trading: an authorised amendment to the stopping rule (2026-11-24)

The v6 spec (`v6-mom-top10.spec`, registered `cf952923…`) says: "PROVEN at any look → a separate forward paper-trading plan". Look 1 was **not PROVEN** (`docs/evidence/xsection_v6/look1.md`: −9.65%/yr, p 0.931).

On 2026-11-24 the account holder authorised paper-trading v6 **anyway**, as evidence collection. Their answers:
- "Real paper orders (raise limits)";
- pool $30k;
- keep the $500 daily-loss stop.

This record exists so the departure from the spec is written down, not implied.

## What this changes and what it does not
- **Not a look:** paper P&L is not a test of v6 and is not judged by the x2 rule. Looks 2 and 3 (2028-11-17 and 2030-11-15) stay as registered, on refetched historical bars, with no parameter changes.
- **Not a claim:** the basket's paper results are reported as real execution evidence (fills, cost, slippage, the gap from the research series). They are never described as proof of profitability.
- **Guardian and market-open checks are unchanged:** every basket order goes through `guardian.review` → executor (`_session_is_open`) → journal. No hard limit was raised. The live env already had `UNIVERSE=tradable`, `MAX_TRADES_PER_DAY=100` and `MAX_TOTAL_EXPOSURE=90000`.
- **Screen rule:** the basket is the second authorised exception, after the core sleeve. It applies only while `AUTOPOIESIS_BASKET_POOL` is set, and the pool is capped in code (`BASKET_POOL_CAP = 30_000`).

## Where forward trading differs from the registered research series

| Aspect | Research series | Forward paper |
|---|---|---|
| Rebalance timing | every 5 sessions, on the membership grid | weekly: targets written Saturday, effective on the next session |
| Weighting | equal weight every day (mean of held returns) | equal weight at entry, resized only when a name drifts more than 25% of its budget |
| Position size | fractional | integer shares, `round(budget / price)` (at $600 a name, a share above $1,200 rounds to 0) |
| Control | — | the whole account position in a basket name, as the core sleeve does for SPY; no other strategy trades it while it is owned |
| Excluded names | — | the sleeve symbol, hold-only symbols and cash equivalents |
| Universe | frozen look-1 cache (ac05…) | a separate forward cache (`universe-forward-1Day-adjall`), extended weekly. It includes ETFs, as v6's universe does |

These differences make the paper series a close but not identical copy of the research series. A later report compares the two.
