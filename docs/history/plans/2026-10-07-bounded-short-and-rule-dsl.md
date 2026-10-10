# Bounded short selling and a rule DSL

Date: 2026-10-07
Status: **BUILT; rollout at step 1 (shadow)** — written before any code, per `AGENTS.md` §4; outcome below
Baseline commit: `ad595fe` (HEAD before this work)
Authorised: the operator chose short selling and the DSL on 2026-10-06 ("做空 / 策略 DSL").

## Problem, measured

Every strategy kind is long-only, and so is the research backtest. Measured in
`2026-10-06-paper-audit-and-enable-execution.md`: on a pure downtrend TREND_FOLLOW emits SELL
on every bar and earns +0.00% while buy-and-hold loses 119.95%, because `models.py` has no
action that opens a short, `guardian.py` refuses a SELL without a position, and `backtest.py`
treats a SELL with no position as a no-op. The ceiling on every rule in the space is therefore
buy-and-hold's own return. The broker is not the obstacle: `shorting_enabled` is True with a
multiplier of 4.

The rule space is also three hard-coded kinds (`HOLD_BASELINE`, `TREND_FOLLOW`, `FIXED_SIZE`),
so the curriculum can only re-parameterise them.

## Baseline

- `Action` = BUY, SELL, HOLD. No short exposure is possible anywhere in the code.
- Live account, 2026-10-07: SPY 6 (the agent's), plus owner positions BIL, TLT, XLB/XLE/XLF.
- Gate: 52 classes green; `make check` 1008 passed.

## Design

1. **Actions.** `SHORT` opens or adds to a short; `COVER` buys it back. `SELL` still only
   reduces a long, and the Guardian's existing SELL checks are unchanged.
2. **Guardian: a new rule with its own limits; no existing limit is relaxed.** A SHORT is
   approved only if all hold:
   - shorts are enabled (`AUTOPOIESIS_SHORTS` is `shadow` or `paper`; default `off`);
   - the symbol is on the allowlist;
   - the account holds **no long position** in the symbol — owner's or agent's. Alpaca nets
     long and short in one account, so a short against a long sells the long first: the
     29-share incident in another form;
   - the agent's resulting short value is at most `max_position_value`;
   - long allowlist exposure plus short value plus this order is at most `max_total_exposure`;
   - every existing order gate (daily loss, trades per day, confidence, market open,
     staleness, conflicting order) passes as for any order.

   A COVER is approved only up to the agent's own short quantity (unknown means refused, as
   for SELL).
3. **Execution.** SHORT submits side `sell`, COVER side `buy`. The fill keeps the decision's
   action as its side in the journal, so the agent's long and short holdings replay separately.
4. **Ledger.** A broker SELL closes long lots FIFO; any remainder opens a short lot only when
   the order was a SHORT decision (by `client_order_id`), otherwise it stays an unmatched sell
   as today. A BUY covers short lots first, the remainder opens a long. Closed and open lots
   carry `direction`; short PnL is (short price − cover price) × quantity − fees.
5. **Counterfactual and backtest.** SHORT is direction −1, COVER +1 in the probe; the backtest
   opens shorts instead of ignoring them.
6. **Rule DSL.** A fourth kind, `RULE`, with constrained parameters:
   `signal ∈ {return_over_n, price_vs_sma}`, `lookback` (2–78 bars), `threshold` (percent),
   `when_above`, `when_below` ∈ Action, `quantity`, `confidence`. Evaluated by
   `StrategyExecutor` on journal bars passed in; validated by pydantic at admission. The model
   proposes JSON parameters only — never code or shell (`AGENTS.md` §17).
7. **Rollout.** `AUTOPOIESIS_SHORTS=shadow` first: SHORT/COVER decisions go through the Guardian
   and are journalled as SHADOWED intents while long trading stays real. `paper` only after a
   session with SHORT intents and no new doctor failures — an operator change, recorded.

## Falsifiers

- The Guardian approves any SHORT while the account is long the symbol → the rule is wrong.
- The ledger books a short lot from a SELL that was not a SHORT decision → the 29-share
  protection is broken.
- On the replayed downtrend the backtest still earns exactly 0 for a SELL-signal rule.

## Out of scope

Margin interest and borrow fees (paper charges neither; recorded as an assumption), shorting
symbols outside the allowlist, and live trading.

## Rollback

`AUTOPOIESIS_SHORTS=off` (the default) restores today's behaviour without a code change.

## Outcome (2026-10-07)

| Step | Commit | Result |
| --- | --- | --- |
| Actions and the Guardian rule | `Add SHORT and COVER behind a Guardian rule…` | 18 tests, one per refusal path. Found and fixed on the way: `PositionSnapshot.market_value >= 0` would have failed every snapshot after the first real short |
| Ledger | `Book short lots in the ledger…` | live record unchanged: +757.95 over 47 lots and 29 unmatched shares, before and after |
| Counterfactual and backtest | `Grade and backtest shorts…` | falsifier refuted: on a 40-bar downtrend TREND_FOLLOW earns 0 long-only and a positive net with shorts |
| Rule DSL | `Add a RULE strategy kind…` | 16 tests; one signal function shared by executor, selector probe and backtest |
| Rollout | operator env file, not the repository | `AUTOPOIESIS_SHORTS=shadow` set and the daemon restarted on 2026-10-07 |

**Not yet done, and why it cannot be done today.** The switch to `paper` is gated on a session
with SHORT intents and no new doctor failures. No SHORT can be approved on this account until
it is flat: it holds 6 SPY, and the Guardian refuses a short against any long. The switch is
therefore an operator decision to be taken on evidence, by setting `AUTOPOIESIS_SHORTS=paper` in
the env file once `minictrl doctor` and the journal show SHORT decisions reviewed in shadow.
