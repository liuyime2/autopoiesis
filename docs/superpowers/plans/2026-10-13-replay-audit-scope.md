# Bounded plan: make the independent replay compare like with like

Written 2026-10-13 22:50 EDT. This is a pre-edit plan. No code change has been made.

## The failure, measured

`make verify` class `replay-audit` fails on three figures:

    closed lots:          naive FIFO 47   vs ledger 49
    realized PnL:         naive FIFO 757.95 vs ledger 760.03
    unmatched sell shares: naive 29.0     vs ledger 42.0

Both sides are correct. They are counting different things, and the audit asserts equality
between them.

## Why they differ

The naive pass in `tools/replay_audit.py` reads only `ORDER_FILL_CONFIRMED` events, which are
the agent's own fills: **81 bought, 76 sold**, and nothing else. It FIFO-matches those against
each other across all strategies.

The production ledger runs `_apply_activity`, which is a deliberately account-level lot
ledger. It seeds lots from the **account owner's recorded fills** as well as the agent's, so a
sell by one agent strategy consumes whatever lots exist for that symbol - including lots the
owner bought. That is the correct convention and `_apply_activity` documents why: a lot
opened by one strategy must be closable by another, and the earlier per-strategy-tracker
version made cross-strategy closes structurally unreachable.

So the two universes differ, and the gap widens exactly where the owner is involved:

- 49 closed lots against 81 agent buys means roughly 49 shares the agent bought were consumed
  by sells, while `open_lots_by_source` still shows the remainder open. The naive pass pairs
  them and calls them closed.
- 42 unmatched against 29: the ledger can tell an agent sell of an owner-held share from an
  agent sell with no agent lot behind it, because it knows the owner's history. The naive pass
  cannot, so it silently consumes a buyer lot that the ledger treats as the owner's.

## What this plan will not do

It will not relax the numbers, align the ledger to the naive pass, or delete the check. The
script's own docstring states the philosophy - "a naive check that disagrees is strong
evidence" - and it was right: it surfaced a scope difference nobody had written down. Removing
it would remove the instrument.

Nor will it touch the ledger. The account total of 49 lots and +760.03 was verified unchanged
by the attribution fix of the same evening, and it is the figure the whole record rests on.

## The change

Compare on a quantity both sides define identically, and report the rest rather than assert
equality.

1. Give the naive pass the same owner lots the ledger has, read from the owner's recorded
   fills via the existing `autopoiesis owner-history` path, so both sides run over the same
   universe. Everything above the naive JSON-only boundary moves to a clearly-labelled
   section, because the independence that makes the check worth having is now narrower and
   should say so.
2. Assert agreement on `closed lots` and `realized PnL` over that shared universe.
3. For `unmatched sell shares`, assert the invariant that holds under both conventions rather
   than the equality - that the ledger's figure is greater than or equal to the naive one, and
   that the difference is accounted for by owner-attributed shares, named in the output.

## Success criterion

- `replay-audit` passes with both sides over one universe.
- A synthetic fixture where the owner has no fills still reproduces today's exact naive
  figures, so the shared-universe change is proven not to have moved the independent
  arithmetic.
- The owner-attributed difference is printed, not silently absorbed.

## Stop conditions

Stop if the change requires editing `_apply_activity`, the lot ledger, or any figure the
benchmark reports; if it makes the naive pass import the production lot machinery, which
would leave a check that can only agree; or if the shared universe cannot be assembled from
recorded fills without inventing one.