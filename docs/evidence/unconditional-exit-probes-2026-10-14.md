# The unconditional exit probes, measured and stopped

2026-10-14. Written after the action, with the numbers that justified it and the one
that is still outstanding.

## What was measured

The curriculum treats "this position has no strategy that can sell it" as a gap to
fill, and filled it the only way `FIXED_SIZE` can express - an unconditional `SELL`:

| strategy | symbol | parameters | market condition | cumulative cap |
|---|---|---|---|---|
| `fixed-size-tlt-sell-001` | TLT | `SELL 1`, conf 0.5 | none | none |
| `fixed-size-xle-sell-001` | XLE | `SELL 1`, conf 0.6 | none | none |
| `fixed-size-xlf-sell-001` | XLF | `SELL 1`, conf 0.6 | none | none |
| `fixed-size-spy-sell-001` | SPY | `SELL 1`, conf 0.7 | none | none |
| `fixed-size-sell-20261111-001` | SPY | `SELL 2`, conf 0.7 | none | none |

A strategy with no condition has no state that can make it false, so it fires on every
cycle in which it is selected until the position is empty. `trend-follow-sell-001` in
the same library carries `reference_price` and `threshold_pct`, so the library can
express a *conditional* sell. The gap was real; choosing an unconditional kind to fill
it was the defect.

Broker-verified fills, from `BROKER_EVIDENCE_INGESTED` orders:

| symbol | sell orders submitted | shares filled | position now |
|---|---|---|---|
| TLT | 202 | 188 | 210 |
| XLE | 36 | 36 | 0 - emptied |
| XLF | 31 | 31 | 0 - emptied |

## What it cost today

`ORDER_SUBMISSION_INTENT` on 2026-10-14, from `fixed-size-tlt-sell-001` only:

    09:38:57  10:17:01  10:50:14  11:24:47  12:03:40

Roughly every 32 minutes, matching the reported pattern. Five shares, 215 to 210. The
last was six minutes before the pause at 12:09:36. On the same cadence the session
would have taken about ten more.

## What was done

There was no way to take a strategy out of service. `StrategyLibrary.save` is the
low-level writer, every lifecycle transition goes through the manager, and no operator
entry point existed. Hand-editing the library files would have stopped the drain and
broken the rule that live code paths are never hand-edited.

`src/autopoiesis/strategy_pause.py` is that entry point, reachable as
`make pause-strategy STRATEGY_IDS=... PAUSE_REASON=...`. It moves the lifecycle to
PAUSED, which `StrategyLibrary` already excludes from selection, and journals the
transition as `STRATEGY_LIFECYCLE_UPDATED` with `source=operator_pause`, the actor and
`previous_lifecycle`, so the audit can say what it changed from. Retirement stays
terminal: its evidence is what retired it.

`StrategyLibrary.list()` reads every spec from disk on each call, so a pause written by
the operator reaches the running daemon on its next cycle with no restart and no
deploy. That is what made this the right instrument at 12:09 EDT with the market open:
it stops the loss without putting unreviewed code into the trading path.

Rehearsed on a copy of the live library first - both specs moved to PAUSED, the audit
line recorded `previous_lifecycle=PROBATION`, and a repeat was refused as a no-op.
Fourteen tests, all synthetic, cover the selection effect, survival across a reload,
that one pause leaves the others alone, the audit fields, and that no refused path
writes anything.

Executed against the live library at 12:09:31 EDT, TLT/XLE/XLF all moved from PROBATION
to PAUSED with the reason recorded. A pre-pause copy is at
`/tmp/opencode/strategies-pre-pause.120931`.

## Still outstanding

**The pause has not yet been observed to hold.** It was written at 12:09:36 and the next
TLT order on the observed cadence would be about 12:35. Confirming that no
`ORDER_SUBMISSION_INTENT` for TLT appears after 12:09:36 is what turns "the pause
should work" into "the pause stopped the drain". Zero events of any kind were recorded
after 12:09:36 at the time of writing, so there was not yet a cycle to observe.

**The admission gate still accepts this shape.** The pause stops the three live probes.
It does not stop the curriculum from proposing a fourth. The gate half - refusing a
sell spec with no market condition and no cumulative cap - is not written yet, and until
it is, the next pause is a manual step rather than a fix.

**`SPY` is being sold one share at a time too**, by `trend-follow-20260804-001`, on the
same ~32-minute cadence: 09:37, 10:15, 10:48, 11:22, 12:01. That strategy is conditional
and a threshold being met is a legitimate reason to sell, so it is not a defect of the
same kind - but SPY has gone from 32 to 27 shares and the cadence is worth its own look.

**The curriculum failure count is still climbing.** Nine `CURRICULUM_FAILED` today, all
with the message that names no cause. The diagnostics fix that would name it is
committed (`c136c6a`) and not deployed, because deploying during an open session is the
worse trade. It should go in after the close.