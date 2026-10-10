# A replay environment over real bars, so changes can be tested before they touch the account

Date: 2026-10-03
Baseline commit for rollback: `829a303`
Status: plan written before any code, per AGENTS.md #4.

---

## 0. What was asked, and what it actually needs

The request: set up a simulated account that reproduces real conditions, keep developing
against it, confirm nothing is wrong, then connect to Alpaca.

One correction first, because it shapes the whole design: **the system is already connected
to Alpaca paper.** Credentials are in place, and the PnL on record
(`broker_strategy_closed_lot_pnl_verified`, 35 closed lots) came back through that API. So
"接入 Alpaca" is not a missing step to add at the end — it is the path the change lands on
once it has been verified somewhere that cannot contaminate the account.

What is actually missing is a place to verify. The remaining work starts with **P1-a**:
re-adjudicating the 33 paused strategies, which requires passing the current price into
`review()` and resetting stale evaluation records. That change alters **which strategies
trade**. Testing it on the live paper account is wrong twice over — it needs market hours,
and it would write the change's behaviour into the same evidence stream that Monday's
first real test of the probation budget depends on. Two measurements would become
indistinguishable.

## 1. Real data is available, so nothing here has to be synthetic

Probed against the live credentials before designing anything:

```
SPY  5Min OK via .df: 451 rows
     2026-09-30 13:30:00+00:00 -> 2026-10-02 19:00:00+00:00 | close 766.74 -> 769.29
```

451 real five-minute bars, and the range brackets the live journal exactly (the 766.57 exit,
the 769.58 last spot). So the replay drives the real loop over the real tape rather than a
fabricated one. **This is the difference between a simulation and a toy**, and it is why no
synthetic price generator is built.

## 2. Not a second implementation

The objective requires one authoritative implementation and one canonical execution path, so
this must not become a parallel loop. It does not have to: `TradingLoop` already takes its
collaborators by injection.

```
loop.py:16  def __init__(self, *, data_gateway, decision_engine, guardian,
                         executor, journal, mode, trade_counter=None)
```

So the replay harness constructs the **real** `TradingLoop` with two substitutions and
nothing else:

| collaborator | live | replay |
|---|---|---|
| `data_gateway` | `AlpacaDataGateway` | `ReplayDataGateway` — real bars, one per cycle |
| `executor` | broker executor | `ReplayExecutor` — fills at the next bar |
| `decision_engine` | real | **unchanged** (the real LLM) |
| `guardian` | real | **unchanged** (risk stays unbypassable) |
| `journal` | live path | separate replay path |

The Guardian being the real one is the point. A simulator with its own soft risk layer would
test a system that is not the one that trades, which is the specific defect `shadow.py`
already documents and refuses.

## 3. Safety properties, and how each is enforced

This is the part that must not be got wrong, because a simulated fill leaking into the PnL
ledger would be the most dangerous bug available: the account would show profit from money
never risked, and an unvalidated path would look like the best one on record.

1. **It cannot reach the broker.** `ReplayExecutor` takes no client and calls no broker
   endpoint. There is no code path from the replay to a live order.
2. **Its fills are unfalsifiable.** Replay execution reports `status="REPLAYED"`, which is
   not in the set the evaluator treats as executed — the same technique `shadow.py` uses for
   `SHADOWED`, and for the same reason.
3. **Its journal is a different file.** Configured by `AUTOPOIESIS_JOURNAL` / the harness, never
   the live path.
4. **A gate check asserts it.** `make verify` gains a class proving the replay modules do not
   import the paper/live executor and that `REPLAYED` cannot be read as a fill. This is the
   one safety claim that must be machine-checked rather than asserted in prose, because prose
   is what failed the last three times.

## 4. Success criterion, stated so it cannot be faked

The environment is worth building only if it actually exercises the change it was built for.
So the acceptance test is not "the harness runs" — it is:

> Replaying a real session reproduces the live loop's behaviour on the same bars, and a
> paused strategy whose reason has expired returns to the candidate pool **without** anything
> being promoted that was not promoted live.

Concretely, measured:
- the replay produces cycle records with real bars, and its journal parses under the same
  reader the live journal uses;
- the lifecycle rules produce the same verdicts for the same strategy/results as the live run;
- P1-a, run only in replay, restores expired-reason pauses and promotes nothing.

## 5. What this does not do

- **It does not produce trading evidence.** Replay verdicts are labelled and stay out of the
  PnL ledger (AGENTS.md #5, #17). A replayed profit is not a profit.
- **It does not replace market hours.** Monday's real measurement still has to happen on real
  cycles; the replay only means the *code change* no longer has to be tested there.
- **It does not validate live-only behaviour** — broker reconciliation, partial fills,
  rejected orders. Those need the real API by definition.

## 6. Order

```
1. ReplayDataGateway over real bars          (no behaviour change anywhere else)
2. ReplayExecutor, REPLAYED status, isolated journal
3. Gate check: replay cannot be read as a fill
4. Prove the replay reproduces the live loop on the same bars
5. Only then: P1-a in replay, then merge to Alpaca paper
```

Step 4 before step 5 is not optional. A harness that does not reproduce the live loop is a
second system, and building P1-a on it would prove nothing about the system that trades.

## 7.1 What step 4 found, and why it mattered that it ran first

Running the **real** `TradingLoop` and the **real** `Guardian` over the 847 cached real bars
refused **all 847 cycles** with `data snapshot is stale`. The Guardian compares a snapshot's
timestamp against the current time, and those bars are from last week - so it was right, and
the replay was useless.

The tempting fix is to relax the staleness check. That would have produced a harness that
trades, and it would have been worthless: it would have tested a risk system nobody ships,
which is precisely the defect this harness exists to avoid. Instead `TradingLoop` gained an
optional `now` callable passed through to `guardian.review(now=...)`, defaulting to `None`
so the live path is unchanged and still falls back to wall-clock. A replay passes the
replayed session's own clock. Same check, correct time base.

After that: **130 replayed fills across 847 cycles, and still `submitted_orders=0`,
`account_realized_or_reported_pnl=None`, `closed_lot_count=0`**,
`pnl_evidence=missing_fill_price_and_broker_activity`. The safety property holds under real
trading load rather than only on an idle path.

Had the harness been declared working on the strength of "it runs", this would have shipped
as a replay that could never place a single order.

## 7. Rollback

Each step is one commit touching new files only until step 5. Reverting returns the tree to
`829a303`; the live path is untouched throughout steps 1-4.