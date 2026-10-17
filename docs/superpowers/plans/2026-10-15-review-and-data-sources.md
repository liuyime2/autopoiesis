# Plan: the 2026-10-15 review, and the data sources it asks for

Written 2026-10-15 evening, from an independent review of the running system. It records
what was found, what is verified, what is still open, and the order to work in. Nothing in
here is a claim that something is fixed.

## 0. Why this document exists

The review's most important finding is structural, and it invalidates a rule this session
had been leaning on: **"CI is green" is not "the system is healthy."** Two gate classes fail
locally and are skipped on GitHub's runners, so a green CI run and a red local gate describe
the same commit. Deploying on CI alone therefore shipped past a red gate twice.

The corrected rule, which is the review's and is stricter than what was being followed:

> **Do not deploy while the local gate is red.** Resolve the failing classes first, or
> record why a specific class is expected to be red at this point in the work.

## 1. Verified findings

### 1.1 The VIX floor was silently disengaged (verified, alarm now added)

| claim | measurement |
|---|---|
| cache last updated 2026-10-09 | confirmed, `runtime/autopoiesis/replay/VIX.pkl` |
| more than 4 days old | confirmed, 7 days |
| the guard is correct | confirmed, `vix_floor` refuses past `VIX_STALE_AFTER_DAYS` |
| **nothing refreshes it on a schedule** | confirmed, no timer, research path is run by hand |
| **nothing reported it** | confirmed, `doctor.py` had no VIX check at all |

So a risk input that P6 measured as a 12.4% forecast-error improvement was absent from every
decision from 2026-10-13, and the doctor said `RESULT: OK`.

**Fixed:** the doctor now carries a `vix freshness` check reading the same
`VIX_STALE_AFTER_DAYS` constant the floor uses. **Not fixed:** the cache is still stale.
The floor stays disengaged until the refresh exists.

### 1.2 SPY is being sold on stale reference prices (reported, not yet independently measured)

Two days took SPY from 32 to 13 shares. The sells are *conditional* - a TREND_FOLLOW whose
reference price was set months ago at 786-800 while SPY trades near 769 - so the
"refuse an unconditional sell" rule cannot catch them. Same family as the TLT drain, reached
by a different route.

**The distinguishing property:** `_declared_actions` returns SELL, so the strategy *can*
act, and it is therefore not filtered by the actionability check. What is wrong is that the
band's reference is far from the market, which `_reference_price_rejection_reason` already
refuses *at admission* - but these were admitted before that check existed.

### 1.3 Two gate classes fail locally and are skipped in CI (confirmed)

- `replay-audit` - the naive pass and the ledger disagree on scope. **The crash half is
  fixed** (six failed confirmations with no `side` raised `KeyError`; they are now reported).
  The scope difference is open, and the plan's own premise is partly wrong: the ledger's
  owner lots arrive through `_apply_activity`, not `seeded_fills`, and `seeded_fills` is fed
  `confirmed_fill_activities`, which reads the same `ORDER_FILL_CONFIRMED` source the naive
  pass does. The difference is in the matching, not the universe.
- `self-evolution-closes` - the lifecycle stage never fires on a real-bars replay. The
  review's hypothesis: the 2026-10-09 stale-quote refusal does not use the replay clock, so
  every submission in a replay is refused and no evidence reaches the lifecycle. **Untested.**

### 1.4 Decision cadence halved (reported)

A round now asks the model about every symbol and takes about 20 minutes; SPY is decided 24
times a day against 44 before, and 2026-10-15's first cycle was at 09:47.

**This is a consequence of the watchlist wiring added on 2026-10-14**, and the cost was not
assessed when it was added. That is the mistake to record, not the wiring itself.

### 1.5 Quote staleness (reported)

44 decisions on 2026-10-15 were refused for a stale quote, the oldest by 18 hours. The free
IEX feed covers roughly 2-3% of volume, so a thinly-traded watchlist symbol often has no
recent print.

## 2. Data sources

### 2.1 What the live decision path uses today

- Alpaca paper: account, positions, orders, clock - authoritative.
- Alpaca free IEX: latest trade price, adjusted daily bars (risk block), most-actives and
  movers (watchlist candidates).
- The system's own journalled snapshots (market context).
- The VIX cache - populated by hand during research, currently stale.
- The local model.

### 2.2 Fetched and used in research only, not in the live path

Yahoo long history; SEC fundamentals; Benzinga news; Alpaca options history; corporate
actions (dividends and splits); 1-minute bars; tick trades.

### 2.3 The rule for adding one

**Correctness data is added directly. Decision-improving data goes through the signal gate,
then shadow, then live.** A source that only makes the record more correct does not need a
signal test; a source that could change a decision does.

| # | source | why | cost | route |
|---|---|---|---|---|
| 1 | **VIX refreshed before each open, with a freshness alarm** | P6 measured a 12.4% forecast-error reduction; currently disengaged | free | correctness - direct |
| 2 | **Quote quality: use the latest quote (bid/ask) for freshness; watchlist candidates must have a recent print** | removes the 44 refusals/day | free | correctness - direct |
| 2b | Full-market SIP feed | root fix for thin symbols | paid, operator's call | decide after "wider universe has value" is shown |
| 3 | **Corporate actions: ex-dividend dates and splits** | BIL and TLT go ex-monthly; the price drop is currently read as a loss and reaches scoring and PnL | free | correctness - direct |
| 4 | Earnings and event calendar | avoid opening before an announcement | free, uneven quality | decision - gate first |
| 5 | Per-symbol option implied volatility | extend the VIX result from the index to single names | free, indicative quotes | decision - gate first |
| 6 | Real-time news as a "something just happened" risk marker | research showed the direction read is right but already priced before the open | already available | risk use only, never for edge |
| 7 | Macro (FRED rates, credit spreads) | may help the TLT and BIL environment; no evidence yet | free | decision - gate first |
| - | tick order flow, 1-minute cross-asset lead/lag, fundamental factors, technical indicators | tested; no signal at our decision speed | - | **do not add** |

## 3. Order of work

### Before the next open

1. **Refresh the VIX cache and add a pre-open refresh plus the freshness alarm** (the alarm
   exists; the refresh does not).
2. **Pause the SPY trend strategies whose reference price is far from the market**, through
   the audited pause entry point, and make the lifecycle able to retire a strategy whose
   reference has drifted from the market rather than waiting for a human.
3. **Do not deploy while the local gate is red.** Resolve §1.3 or record why it is expected.

### Next

4. **Diagnose the two gate classes.** For `replay-audit`, start from "the difference is in
   the matching" (§1.3) rather than the universe. For `self-evolution-closes`, test the
   replay-clock hypothesis.
5. **P2**: measure the real failing prompt and thinking tokens, then set the decision context
   window. Target: parse-failure rate below 2% (currently 6-9%).
6. **Cadence**: choose the watchlist before the open; cap how many symbols a round asks the
   model about so a core symbol is decided at least every 10 minutes.
7. **Data sources 1-3.**
8. **Merge to `main` once the gate is 55/55**, then P7-P11.

## 3b. The gate now takes longer than a working session (measured 2026-10-16)

`make verify` did not finish in 25 minutes. The bottleneck is one class:
`self-evolution-closes` runs `tools/self_evolution_probe.py`, which drives a full daemon
replay over real bars, and that probe now takes **953 seconds** - 15.9 minutes on its own.

It got slower because it got *correct*: until 2026-10-16 the probe scored nothing (the
replay never stamped provenance, so every counterfactual row was refused), and a stage that
does nothing is fast. Fixing the root cause made the offline validation actually run over
the scored decisions, and that is where the time goes.

**This is a defect in its own right, and it blocks the rule above.** "Do not deploy while
the local gate is red" cannot be executed by an operator who cannot get the gate to finish.
It is not a reason to weaken the probe or to skip the class.

What is known, and what is not:

- Known: 847 cycles, and the probe asserts at least 100. The cost scales with the bar count.
- Known: the daemon's `_maintenance` - which now runs the counterfactual over scored
  decisions - is on `maintenance_interval_seconds`, and the replay drives time through
  `sleep=lambda _seconds: gateway.advance()`.
- **Not known: where the 953 seconds actually go.** No profile has been taken. The two
  obvious candidates - a counterfactual pass that rebuilds price series from every record
  each time it runs, and per-cycle snapshot construction - are guesses until measured, and
  this plan does not act on guesses.

Next: profile the probe (`cProfile` over `harness.run()`), then either fix the hot path or
reduce the replay's cycle count *with the probe's own assertion re-derived*, not lowered.
The probe's `cycles >= 100` bar and every other assertion stay as they are.

## 3c. Incremental journal parsing: attempted 2026-10-16, reverted

The profile in §3b names `journal._scan` as 76% of the replay. A signature-keyed cache
was landed first and is in the tree: it took the probe from 953s to 622s and still returns
`[]`. It stops there because the daemon writes an event every cycle, so the signature
changes every cycle and the cache misses on nearly every read.

Incremental parsing was then attempted - parse only the bytes a file has gained, keyed by
inode so a rotated file keeps its identity when renamed from `.1` to `.2`. **It was
reverted, and the two bugs it hit are recorded here so the next attempt starts from them
rather than from scratch.**

**Bug 1, found by the test and fixed before reverting.** `size >= offset` cannot tell an
append from a rewrite: a rewritten file can be *larger*, and reading from a stale offset
then parses whatever now sits at that byte position. The test that caught it rewrote a file
longer and got only its first record back. The fix was a boundary check - keep the last 64
bytes of the consumed text and require them to still be there before extending. Cheap, and
it makes the rewrite case fall back to a whole read.

**Bug 2, not found, and the reason for the revert.** After a partial line is completed by
appending the rest, the next scan still returned only the earlier records. The offsets were
traced by hand through `_parsed` -> `_boundary_holds` -> `_extend` and every step read
correctly, which means the fault is somewhere those three do not show - most likely in how
`entry` is rebuilt and stored back, or in `records` being extended from `entry["records"]`
and then again inside `_extend`.

**Why it was reverted rather than debugged further.** The journal is the system of record.
A faster reader that silently loses a record is far worse than the slow one it replaces,
and the failing case is not exotic - it is "the writer appended the rest of a line", which
is what a real append does whenever a read lands mid-write. Shipping it would have traded a
known slowness for an unknown loss.

**The test to restore first.** `tests/autopoiesis/test_journal_incremental_parse.py` was
written with it and deleted with it. Its central assertion is the one that matters and
should be written before the code: **over a file growing one record at a time, the
incremental read must equal a whole re-parse at every step**, compared against a reader
that has never seen the file. Two of its cases are worth keeping in mind because they are
what a naive implementation gets wrong: a line without its terminator is not a record yet,
and a file that shrank must be re-read whole rather than extended from a stale offset.

## 3d. Corporate actions: the source exists, measured 2026-10-17

Data source 3 was recorded as "free, correctness - direct". It is, and the provider was
tested rather than assumed:

    GET https://data.alpaca.markets/v1/corporate-actions
        ?symbols=TLT&types=cash_dividend&start=2026-06-01&end=2026-10-17

reached with the existing paper key, returning `{"cash_dividends": [{ex_date, payable_date,
rate, cusip, ...}]}`. Counts from 2026-06-01: **TLT 5, XLE 2, XLF 2, XLB 2, SPY 1, AAPL 1,
BIL 0**.

Two things this settles and one it does not.

**Settled: the endpoint exists and the key can read it.** The first attempt used
`types=dividend`, which is not a valid type - the valid ones are `cash_dividend`,
`forward_split`, `reverse_split`, `stock_dividend`, `spin_off`, `cash_merger` and others -
and a 400 was read as "no source" for a moment. It was a wrong parameter, not a missing
service.

**Settled: splits are available from the same endpoint**, which matters for the same reason:
a forward split is a price change that is not a return, and the counterfactual would score
it as one.

**Not settled: BIL has no records.** BIL is the instrument the agent holds most of (209
shares) and it distributes monthly, so a zero is either a gap in the provider's coverage of
T-bill ETFs or a date-range problem. That has to be resolved before the fix can be claimed
to cover the position it matters most for, and the honest state until then is that this
covers TLT, XLE, XLF, XLB and SPY and **does not cover BIL**.

### Where the fix goes

The defect is not in the broker's accounting: a dividend is a cash credit and the account
equity already reflects it. The defect is in **our derived measures**, which read a price
change as a return:

- `counterfactual.py` builds a price series per symbol and scores a decision against the
  change in it. On an ex-date the price drops by the distribution, so a HOLD is scored as a
  loss and a SELL as a gain, neither of which is true of the total return.
- The same series feeds the volatility forecast and the regime labels.

The fix is to adjust the series for the distribution on the ex-date - total return rather
than price return - and the dividend cache belongs beside `VIX.pkl`, refreshed the same way
and for the same reason: a correctness input that goes stale silently disengages.

**The measurement that should come first was taken, and it changes the urgency.**

    TLT ex-dates from the provider:  2026-08-03, 2026-09-01, 2026-10-01
    TLT cycles in the journal:       only 2026-10-08 .. 2026-10-16
    TLT cycles within a day of any ex-date:  0

The journal holds no TLT cycle before 2026-10-08, because TLT was not in the cycled symbol
set until then - the doctor's retention window starts 2026-06-09, so this is the symbol set
changing rather than rotation losing them.

**So the defect is latent and nothing has been mis-scored yet.** That is a statement about
the past and not about the future: TLT distributes monthly, the next ex-date is around
2026-11-02, and from 2026-10-08 TLT is cycled every session. The first ex-date after that
change is the first one that will bite.

Which makes this **correctness work with a deadline rather than an urgent scoring defect**,
and the plan's "priority 3, free, direct" is right - but the reason it is not higher is that
the calendar has not reached it yet, not that it does not matter. A fix landed after
2026-11-02 would be a fix landed after the first mis-scored decisions.

## 3e. The dividend fix, attempted and reverted 2026-10-17

`tools/refresh_dividends.py` is in the tree and works. The adjustment was implemented in
`counterfactual._price_series` and **reverted**, and the reason is a design error worth
recording rather than a bug.

**It corrupted the reported price.** `_price_series` does not only feed the return
arithmetic - its price becomes `CounterfactualRow.price_at_horizon`, and
`test_point_in_time` asserts that value is a real observed quote. The adjustment made it
749.523516 against a set of real quotes, so the check failed correctly. The point-in-time
property is not decoration: a row whose horizon price is not a price is a row nothing can
audit.

**It was also not the no-op it looked like.** All three TLT ex-dates precede 2026-10-08, so
for TLT the cumulative cancels - but **SPY's 2026-06-18 falls inside the retained window**,
which starts 2026-06-09. So the adjustment moved real verdicts, and
`test_replaying_the_live_journal_reproduces_the_recorded_verdicts` failed with it.

**Where the adjustment belongs**: on the return, not on the series. The counterfactual
computes `(price_at_horizon / price_at_decision - 1)`; the distribution has to enter that
expression as `(price_at_horizon + D_between) / price_at_decision - 1`, leaving both
reported prices exactly as observed. That is a change at the call site rather than in the
series builder, and it is the next attempt.

Two things this leaves in place and one it does not:

- In place: the cache, the tool, and the measurement that nothing has been mis-scored yet.
- In place: the finding that the provider covers every cycled symbol including BIL - an
  earlier note said BIL had no records, and that was a date-range artifact in the query, not
  a gap in the provider. BIL has 13.
- Not in place: any change to the counterfactual. It is exactly as it was, and the defect
  it has remains latent until around 2026-11-02.

## 3f. The dividend fix at the call site: implemented, and it needs a decision

The second attempt put the adjustment where section 3e said it belonged - on the return
expression, `(price_at_horizon + D_between) / price_at_decision - 1` - with
`_horizon_quote` extended to return the quote's own timestamp so the window ends at the
quote actually used. Both reported prices stayed exactly as observed, and
`test_point_in_time` passed. That part is settled and is what the next attempt should start
from.

**It was reverted anyway, because it changes verdicts already in the journal.**

`test_replaying_the_live_journal_reproduces_the_recorded_verdicts` failed with **28
journalled SPY verdicts that no longer reproduce** - same verdicts, different values, for
example `GOOD_HOLD` at `-0.444362` recorded against `-0.190756` recomputed. SPY's
2026-06-18 ex-date falls inside the retained window that starts 2026-06-09, so every SPY
decision spanning it was scored without the distribution.

**Those verdicts were wrong when they were written.** That is the whole point of the fix.
But the journal is the system of record, and a change that makes a recomputation disagree
with what was written is not a change I will make on my own authority - it is the same
shape as `replay-audit`: it changes what the system reports, and the account holder decides
that.

The three options, and what each costs:

1. **Apply it and accept the divergence.** The record's old verdicts stay as written, new
   ones are correct, and the test changes from "reproduces the record" to "is deterministic
   and the divergence is explained". The record then holds two arithmetics, labelled by
   date.
2. **Apply it and recompute the affected verdicts** into new events, leaving the originals
   in place - append-only, so nothing is destroyed and the correction is itself evidence.
   This is the most faithful to the journal's own discipline, and it is more work.
3. **Do not apply it.** The defect stays latent until the next ex-date, around 2026-11-02,
   and the first mis-scored decisions are then real rather than reconstructed.

The deadline is what makes this a decision rather than a queue item: option 3 has a date
attached to it, and it is about five weeks away.

## 4. Acceptance

- `make verify` reports 55/55 locally, and CI reports green, on the same commit.
- The VIX floor is engaged on the next open, and a stale cache produces a doctor warning.
- SPY sells stop for the paused strategies, and no strategy with a drifted reference can
  reach service without a lifecycle verdict naming the drift.
- Parse-failure rate below 2% over a full session.
- No symbol waits more than 10 minutes for a decision.

## 5. Stop conditions

- Do not raise `max_probation_queue`, relax `stale_after_seconds`, or edit any acceptance
  gate to obtain a green result.
- Do not change `_apply_activity` or the lot ledger for `replay-audit`; do not let the naive
  pass import the production lot machinery, which would leave a check that can only agree.
- Do not deploy while the local gate is red.
- Live trading stays forbidden until paper behaviour is audited and the hard risk controls
  are verified (AGENTS.md 16). Attributable trading days are 1 of 60; the gate is not 55/55.

## 6. What this document does not claim

That anything above is fixed. §1.1's alarm is in the tree; §1.1's refresh, §1.2, §1.3, §1.4
and §1.5 are open. Several findings here are the review's measurements and have not been
independently reproduced yet, and each is marked as such.
