# The PnL total omits the exits it cannot price, and the report calls that an upper bound

Date: 2026-10-03
Status: **DONE - the false claim deleted; the scope question answered by measurement a
day later, and nothing built for it**
Baseline commit for rollback: `ffc635e`
Scope: `src/min_agent/doctor.py` (two strings and one comment), `STATUS.md`. No behaviour
change, no new field, no new check class: `unmatched_sell_quantity` already carries the
number and `tests/min_agent/test_evaluator.py` already pins the semantics. What is wrong is
the sentence the system tells the operator about it.

---

## 0. The question this answers

`STATUS.md` has carried one open finding for weeks: `UNMATCHED SELLS
{'trend-follow-sell-002': 29.0}`, with the claim that *"the headline realized PnL includes
part that cannot be attributed to a strategy, so it is an upper bound rather than an exact
result"*, and with a hypothesis for the cause that it admits is *"a hypothesis, not a
finding"*.

The objective's rule is that a knowledge which cannot change behaviour is not learning, and
that an open finding carried as a hypothesis is worse than a closed one. So: measure the
mechanism, and either close the finding or record what is actually true.

## 1. The baseline, as a measurement

All figures from the live record: 1179 cycle records and 62 broker-confirmed `FILL` events
across two retained generations, `2026-06-09T06:00` to `2026-10-02T15:58`.

### 1.1 What the ledger does with a sell it cannot price

`evaluator._apply_activity`, lines 878-882:

```python
# A SELL that matched nothing still cost money and is still a fact. Charge the
# remainder to the closing strategy so it is visible rather than dropped, which
# is what the old code did with `unmatched_sell_quantity`.
closing.unmatched_sell_quantity += remaining
closing.fees += sell_fee_remaining
```

The quantity and the fee are recorded. **No PnL is booked**, because there is no lot to
compute one against. So those exits are *absent from* `strategy_realized_pnl`, not present in
it without a name.

### 1.2 What that means for the direction of the error

Reported today: `realized +594.33` across 35 closed lots = 366.03 + 146.94 + 81.36, and
`net +643.40 = realized 594.33 + unrealized 49.07`.

The 29 unmatched shares were sold at an average of 766.57. Their proceeds are real and their
cost basis is not in the record. Therefore the true account-level realized figure is
`594.33 + <unknown sign>`. The reported number is **incomplete**. It is not an upper bound:
it is too low by whatever those 29 shares gained and too high by whatever they lost, and
nothing in the record says which.

The report says the opposite. `doctor.py:853`:

> part of this PnL cannot be attributed to a strategy and **the headline figure is an upper
> bound** rather than an exact result

That sentence tells an operator that +594.33 is the most the run could possibly have earned.
The mechanism is the reverse: it is the most the run can *prove*.

### 1.3 Where the 29 shares came from

Cross-strategy exiting is **not** the cause, which was the standing hypothesis. The ledger
matches a SELL FIFO across the account and records the closing strategy separately
(`closed_lot_attribution.closing_strategy_id`), precisely so a lot opened by one strategy
can be closed by another. The recorded evidence:

| Fact | Value |
|---|---|
| Fill events retained | 62, all SPY |
| Shares bought on the record | 68 |
| Shares sold on the record | 64 |
| The one large sell | `trend-follow-sell-002`, 52 shares @ 766.57, 2026-09-28T19:12Z |
| Its own rationale | *"a trend following strategy that sells when the price deviates downward …"* |
| `fixed-size-sell-005`'s rationale | *"the open SPY position (10 shares, $7,641.80) has no exit path"* |
| Shares of that sell that closed an agent lot | 23 |
| Shares with no lot | 29 |
| Cumulative retained position at the moment of the sell | **−29** |

Reading the fills in time order, the account's on-record position goes to −29 the instant
that sell lands, and only returns to positive through subsequent BUYs. So the agent sold 29
shares it had never bought on its own record. The exit strategies exist precisely to close
an account-level position, and the position was partly not theirs.

This is the same fact the doctor already reports as `unmanaged exposure` - 5 positions
outside the allowlist, $36,784.52, 37% of equity - seen from the PnL side. It is a
property of the **account**, not of the trading path.

## 2. The defect, stated as one sentence

**The system books no PnL for a sell it cannot price, and then describes the resulting
total as an upper bound when it is an incomplete one** - a claim that is wrong in the
direction that flatters the run.

## 3. The minimal change

**Delete the false claim. Add nothing.**1. `doctor.py:853` - replace *"the headline figure is an upper bound rather than an exact
   result"* with the mechanism: those shares' PnL is **not in the total**, its sign is
   unknown, read the figure as incomplete.
2. `doctor.py:834` - the comment above it states the same claim as the rationale for WARN
   severity. Correct it there too, so the next reader does not restore the sentence.
3. `doctor.py:808` - *"so that PnL is unproven"* → say it is excluded from the total rather
   than merely unproven, which is the stronger and more useful fact.
4. `STATUS.md` - replace the open finding's hypothesis with the measured cause, and drop the
   upper-bound claim from its current-state section.

No new field: `unmatched_sell_quantity` is the number, and the tests already pin the
semantics. Adding `unpriced_exit_proceeds` would restate it.

## 4. What this deliberately does not do

- **Not fetch older broker activities to price those 29 shares.** It would work - the broker
  has the fills, and section 8a records that measurement - and it is a *capability* addition
  that changes the reported number. It is only worth building if the agent is meant to manage
  positions it did not open, which is a decision about scope, not a defect fix. Recorded as
  the next question instead.
- **Not change what the PnL ledger books.** Zero-PnL-for-unmatched is correct: inventing a
  cost basis would be worse than omitting the exit.
- **Not change severity.** It stays WARN. The severity argument stands on its own - a
  four-month accounting gap must not make `make verify` permanently red - and only its
  wording is wrong.

## 5. What would prove this wrong

| Prediction | Falsifier |
|---|---|
| An unmatched sell contributes no realized PnL | A journal where `realized` includes an exit with no matched lot |
| The retained position really is short 29 at the sell | A retained BUY fill of those 29 shares earlier in time |
| The claim in the report is false | The ledger booking a basis-free PnL somewhere else in the code path |

The first two are already checked against the record and against
`tests/min_agent/test_evaluator.py:631-636`, which asserts `closed_lot_count == 23`,
`unmatched_sell_quantity == {"closer": 29.0}` and `opener == (766.57 - 742.03) * 23` - the
same 52-share sell, the same 29 shares. If that test were passing for a different reason, the
claim would need re-measuring rather than rewriting.

## 6. When to delete this

If the agent is later scoped to manage only positions it opened, and every sell it submits is
covered by its own lots, then `unmatched_sell_quantity` should read 0 forever, this whole
finding is moot, and the wording matters no more than the field. That is the condition, and
it is a scope decision rather than a code change.

**The condition is already half met, and measured.** The Guardian refuses any SELL exceeding
what the agent itself bought, its comment citing this exact 52-share sell, and 9 of the 9
sells since that rule landed are covered by its own lots. The remaining 29 shares are a
historical fact with no path to recurrence, so the scope decision has been made by
circumstance rather than by argument: the agent manages only what it opened.

## 8a. What paging the broker's own history showed, recorded later the same day

Paging `get_activities` with `page_token` to exhaustion returns **6000 fills back to
2025-10-22**, of which 279 fall in the last nine months. So the cost basis for those 29
shares is recoverable in principle - the account was simply being read through a window that
did not reach back far enough. That confirms the first half of the question below and leaves
the second half answered by the Guardian rule: nothing needs building for it.

The paging did surface a real defect in the same path. `broker_evidence` makes one request
and the broker's 100-row page cap is the whole fetch, so a nine-month window yielded 100 of
279 fills with `missing_reasons` empty - a confident number computed from a fragment.
Disclosed rather than paged, because every window the code actually requests is under the
limit (24 hours returns 0-20, 30 days 43, four months 66). Pinned by three tests and
recorded in `STATUS.md`.

## 7. Verification

- The doctor run before and after: the WARN survives, the sentence changes, and the figure it
  describes does not move (`+594.33` before and after - nothing about the number changes,
  only the claim about it).
- `tests/min_agent/test_evaluator.py` unchanged and green; the semantics it pins are what the
  new wording states.
- `make verify` - the whole gate.

## 8. The next question, if this one closes

**Should the agent manage account positions it did not open?** 37% of equity sits outside
the allowlist and 29 shares of one sell had no basis on record. That question decides whether
the right answer is to price the unowned shares from broker history or to refuse to touch
positions the strategy library does not own. Both are buildable; which one is correct depends
on a scope decision, and building either before the decision is exactly the kind of
anticipatory infrastructure this project has been deleting.

**Answered, and nothing built** - see section 8a. The basis is recoverable, the Guardian
already refuses to sell what the agent does not own, and 9 of 9 sells since that rule landed
are covered by the agent's own lots. So the decision made itself: the agent manages only what
it opened, and the 29 shares stay a disclosed historical fact.

## 9. Result, measured against the live report

`minictrl doctor` before and after, same run of the same account:

```
before: ... | UNMATCHED SELLS 29: ... so that PnL is unproven; ... so part of this PnL cannot
        be attributed to a strategy and the headline figure is an upper bound rather than an
        exact result
after:  ... | UNMATCHED SELLS 29: ... and their PnL is absent from the total above rather
        than merely unproven; 29 share(s) were sold that no BUY accounts for, so the PnL of
        those exits is not in the total above at all - their proceeds are real and their cost
        basis is not on the record, so the omitted term can be positive or negative and the
        figure is incomplete rather than exact
```

`+594.33 across 35 closed lot(s)` is identical on both sides, which is the point: **no number
moved and the claim about it became true.** The WARN is still a WARN. 60 doctor, proof,
attribution and evaluator tests green.
